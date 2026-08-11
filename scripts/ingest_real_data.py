"""
ingest_real_data.py
-------------------
Ingesta exclusiva de datos reales derivados de los CSV del dataset.
No genera datos ficticios, planes aleatorios ni promociones inventadas.
Para escenarios de demo/testing usar seed_demo.py (separado).
"""

import os
import sys
from pathlib import Path
from datetime import datetime

import pandas as pd

# La URL SQLite por defecto es relativa al directorio de trabajo. Forzamos la
# raíz del proyecto ANTES de importar database para que la ingesta y la app web
# usen el mismo lucia_brain.db aunque el script se ejecute desde scripts/.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from app.db.database import engine, Base, SessionLocal
from app.db.models import ReciboCliente, CatalogoPlanes, TerminosRestringidos, ContactoUsuario, OrdenCliente

# ---------------------------------------------------------------------------
# Rutas absolutas a los CSV (no depende del cwd de ejecución)
# ---------------------------------------------------------------------------
DISCLAIMER_DIR = PROJECT_ROOT / "disclaimer"
PATH_CARGOS = DISCLAIMER_DIR / "Cargos_FacturadosV2.csv"
PATH_CLIENTES = DISCLAIMER_DIR / "REGISTROS_CLIENTES_20MIL.csv"
PATH_ORDENES = DISCLAIMER_DIR / "Ordenes.csv"

# Límite de cuentas para desarrollo local
MAX_USERS = 500


def _normalize_account_key(value) -> str:
    """
    Normaliza FINANCIAL_ACCOUNT_KEY / FINANCIAL_ACCOUNT a str entero sin decimales ni espacios.
    Evita problemas como '102968745.0' vs '102968745'.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    s = str(value).strip()
    # Si viene como float string (e.g. "102968745.0"), quitar el .0
    if "." in s:
        try:
            s = str(int(float(s)))
        except (ValueError, OverflowError):
            pass
    return s


def init_db():
    print(f"Creando tablas en {engine.dialect.name}...")
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()

    try:
        # ------------------------------------------------------------------
        # Idempotencia: verificar si ya hay recibos cargados (no depender de CatalogoPlanes)
        # ------------------------------------------------------------------
        if db.query(ReciboCliente).first():
            print("La base de datos ya contiene recibos. Saltando ingesta.")
            return

        # ------------------------------------------------------------------
        # 1. Poblar Catálogo de Planes (mantener para cross-sell determinista)
        # ------------------------------------------------------------------
        planes = [
            CatalogoPlanes(nombre="Internet Hogar 100 Mbps", precio=69.90, beneficios="Internet Ilimitado"),
            CatalogoPlanes(nombre="Internet Hogar 300 Mbps", precio=99.90, beneficios="Internet Ilimitado + Router Smart"),
            CatalogoPlanes(nombre="Internet 600 Mbps", precio=99.90, beneficios="Doble velocidad, Movistar Play incluido (PROMO)"),
            CatalogoPlanes(nombre="Internet Fibra 1000 Mbps", precio=149.90, beneficios="Máxima velocidad, 2 Repetidores Mesh"),
        ]
        db.add_all(planes)

        # ------------------------------------------------------------------
        # 2. Poblar Términos Restringidos
        # ------------------------------------------------------------------
        terminos = [
            TerminosRestringidos(
                patron_regex=r"\b(m[ií]erda|put[oa]|imb[ée]cil|est[úu]pido)\b",
                accion_disparador="INSULTO",
                mensaje_bloqueo="Por favor mantengamos el respeto en nuestra conversación. Estoy aquí para ayudarte.",
            ),
            TerminosRestringidos(
                patron_regex=r"\b(denunciar|indecopi|abogado|demanda)\b",
                accion_disparador="LEGAL_RIESGO",
                mensaje_bloqueo="Entiendo tu molestia. Quiero ayudarte a resolver esto de la mejor manera. Déjame revisar tu caso en detalle.",
            ),
            TerminosRestringidos(
                patron_regex=r"\b(\d{16}|\d{4}-\d{4}-\d{4}-\d{4})\b",
                accion_disparador="DATOS_SENSIBLES",
                mensaje_bloqueo="Por tu seguridad, no compartas números de tarjeta de crédito por este medio.",
            ),
        ]
        db.add_all(terminos)

        # ------------------------------------------------------------------
        # 3. Leer y procesar CSVs
        # ------------------------------------------------------------------
        print("Cargando CSVs con pandas...")

        try:
            df_cargos = pd.read_csv(PATH_CARGOS, sep=";")
            df_clientes = pd.read_csv(PATH_CLIENTES, sep=";", dtype=str)
        except Exception as e:
            print(f"Error al leer los CSVs: {e}")
            print(f"  PATH_CARGOS: {PATH_CARGOS}")
            print(f"  PATH_CLIENTES: {PATH_CLIENTES}")
            db.rollback()
            return

        # Normalizar las claves de cuenta en ambos DataFrames
        df_cargos["FINANCIAL_ACCOUNT_KEY"] = df_cargos["FINANCIAL_ACCOUNT_KEY"].apply(_normalize_account_key)
        df_clientes["FINANCIAL_ACCOUNT"] = df_clientes["FINANCIAL_ACCOUNT"].apply(_normalize_account_key)

        # Convertir montos explícitamente a float
        df_cargos["CHARGE_NET_AMOUNT"] = pd.to_numeric(df_cargos["CHARGE_NET_AMOUNT"], errors="coerce").fillna(0.0)
        df_cargos["CHARGE_TOTAL_AMOUNT"] = pd.to_numeric(df_cargos["CHARGE_TOTAL_AMOUNT"], errors="coerce").fillna(0.0)

        # Extraer las top MAX_USERS cuentas
        unique_accounts = df_cargos["FINANCIAL_ACCOUNT_KEY"].unique()[:MAX_USERS]
        df_cargos_filtered = df_cargos[df_cargos["FINANCIAL_ACCOUNT_KEY"].isin(unique_accounts)]

        print(f"Procesando recibos para {len(unique_accounts)} cuentas...")

        # Construir mapa de clientes indexado por FINANCIAL_ACCOUNT para lookup rápido.
        # Un FINANCIAL_ACCOUNT puede tener múltiples filas (una por línea/servicio),
        # por lo que se desduplicamos quedándonos con la primera ocurrencia.
        df_clientes_unique = df_clientes.drop_duplicates(subset="FINANCIAL_ACCOUNT", keep="first")
        clientes_map = df_clientes_unique.set_index("FINANCIAL_ACCOUNT").to_dict(orient="index")

        # ------------------------------------------------------------------
        # 4. Generar recibos agrupados por cuenta + ciclo
        # ------------------------------------------------------------------
        grouped = df_cargos_filtered.groupby(["FINANCIAL_ACCOUNT_KEY", "ciclo"])

        recibos_a_insertar = []

        for (account_id, ciclo), group in grouped:
            monto_total = round(float(group["CHARGE_TOTAL_AMOUNT"].sum()), 2)

            # Formatear fecha de emisión desde el ciclo (YYYYMMDD)
            ciclo_str = str(int(float(ciclo))) if not isinstance(ciclo, str) else str(ciclo)
            if len(ciclo_str) >= 6:
                mes_emision = f"{ciclo_str[:4]}-{ciclo_str[4:6]}"
                fecha_emision = datetime(int(ciclo_str[:4]), int(ciclo_str[4:6]), 1)
            else:
                mes_emision = "2026-01"
                fecha_emision = datetime(2026, 1, 1)

            # Preservar información real de los cargos (req #9)
            conceptos_facturados = []
            for _, row in group.iterrows():
                conceptos_facturados.append({
                    "CHARGE_CODE_ID": str(row.get("CHARGE_CODE_ID", "")),
                    "CHARGE_CODE_DESC": str(row.get("CHARGE_CODE_DESC", "")),
                    "CHARGE_CODE_CLASSIFICATION": str(row.get("CHARGE_CODE_CLASSIFICATION", "")),
                    "CHARGE_TOTAL_AMOUNT": round(float(row["CHARGE_TOTAL_AMOUNT"]), 2),
                    "CHARGE_NET_AMOUNT": round(float(row["CHARGE_NET_AMOUNT"]), 2),
                    "GRUPO": str(row.get("GRUPO", "")),
                    "SUB_GRUPO": str(row.get("SUB_GRUPO", "")),
                })

            # Conservar información de factura (req #10) — tomar del primer row del grupo
            first_row = group.iloc[0]
            info_factura = {
                "CUSTOMER_KEY": str(first_row.get("CUSTOMER_KEY", "")),
                "BILLING_ARRANGEMENT_KEY": str(first_row.get("BILLING_ARRANGEMENT_KEY", "")),
                "LEGAL_INVOICE_NUMBER": str(first_row.get("LEGAL_INVOICE_NUMBER", "")),
                "BILLING_CYCLE_KEY": str(first_row.get("BILLING_CYCLE_KEY", "")),
                "SUBSCRIBER_KEY": str(first_row.get("SUBSCRIBER_KEY", "")),
                "PERIOD_START_DATE": str(first_row.get("PERIOD_START_DATE", "")),
                "PERIOD_END_DATE": str(first_row.get("PERIOD_END_DATE", "")),
                "FECHA_VENCIMIENTO": str(first_row.get("FECHA-VENCIMIENTO ", first_row.get("FECHA-VENCIMIENTO", ""))).strip(),
                "DEUDA": str(first_row.get("DEUDA", "")),
            }

            recibos_a_insertar.append(
                ReciboCliente(
                    user_id=str(account_id),
                    mes_emision=mes_emision,
                    monto_total=monto_total,
                    fecha_emision=fecha_emision,
                    conceptos_facturados={
                        "cargos": conceptos_facturados,
                        "info_factura": info_factura,
                    },
                    plan_actual=None,  # No asignar plan ficticio (req #5, #8)
                )
            )

        db.add_all(recibos_a_insertar)

        # ------------------------------------------------------------------
        # 5. Poblar ContactosUsuario
        # ------------------------------------------------------------------
        print("Creando contactos de usuario...")
        contactos_a_insertar = []

        for account_id in unique_accounts:
            acc_str = str(account_id)

            # Determinar número de teléfono real (req #7)
            # No hay PRIMARY_RESOURCE_VALUE en el dataset; telefono_hash no es utilizable.
            # Dejar whatsapp_number = None.
            whatsapp_number = None

            contactos_a_insertar.append(
                ContactoUsuario(
                    user_id=acc_str,
                    whatsapp_number=whatsapp_number,
                    telegram_chat_id=None,
                )
            )

        db.add_all(contactos_a_insertar)

        # ------------------------------------------------------------------
        # 6. Poblar OrdenCliente (historial de órdenes CRM/OSS)
        # ------------------------------------------------------------------
        print("Cargando órdenes...")
        try:
            df_ordenes = pd.read_csv(PATH_ORDENES)
        except Exception as e:
            print(f"  Aviso: no se pudo leer Ordenes.csv ({e}). Se omite la ingesta de órdenes.")
            df_ordenes = pd.DataFrame()

        if not df_ordenes.empty:
            # Normalizar CUSTOMER_KEY para el join
            df_ordenes["CUSTOMER_KEY"] = df_ordenes["CUSTOMER_KEY"].astype(str).str.strip()

            # Solo ingerir órdenes de los CUSTOMER_KEYs que ya están en los recibos cargados.
            # La relación Cargos→Ordenes es vía CUSTOMER_KEY (presente en info_factura de cada recibo).
            customer_keys_ingeridos = set()
            for r in recibos_a_insertar:
                ck = (r.conceptos_facturados or {}).get("info_factura", {}).get("CUSTOMER_KEY", "")
                if ck:
                    customer_keys_ingeridos.add(ck)

            df_ordenes_filtrado = df_ordenes[df_ordenes["CUSTOMER_KEY"].isin(customer_keys_ingeridos)]
            print(f"  {len(df_ordenes_filtrado)} órdenes corresponden a las {len(customer_keys_ingeridos)} cuentas ingeridas.")

            ordenes_a_insertar = []
            for _, row in df_ordenes_filtrado.iterrows():
                start_dt = None
                completion_dt = None
                try:
                    start_dt = pd.to_datetime(row.get("ORDER_ACTION_START_DATE"))
                except Exception:
                    pass
                try:
                    completion_dt = pd.to_datetime(row.get("ORDER_ACTION_COMPLETION_DATE"))
                except Exception:
                    pass

                ordenes_a_insertar.append(OrdenCliente(
                    customer_key=str(row["CUSTOMER_KEY"]),
                    subscriber_key=str(row.get("SUBSCRIBER_KEY", "")),
                    order_type=str(row.get("ORDER_ITEM_TYPE_DESC", "")),
                    order_reason=str(row.get("ORDER_ACTION_REASON_DESC", "")),
                    start_date=start_dt,
                    completion_date=completion_dt,
                ))

            db.add_all(ordenes_a_insertar)
            print(f"  {len(ordenes_a_insertar)} órdenes preparadas para inserción.")

        # ------------------------------------------------------------------
        # Commit
        # ------------------------------------------------------------------
        db.commit()
        print(f"Datos reales ({len(unique_accounts)} cuentas, {len(recibos_a_insertar)} recibos) insertados en {engine.dialect.name}.")

        # Imprimir credenciales de prueba
        print("\n--- Credenciales para Testing (Web) ---")
        for c in contactos_a_insertar[:5]:
            print(f"  User ID: {c.user_id} | WhatsApp: {c.whatsapp_number or '(sin número)'}")
        print("---------------------------------------")

    except Exception as e:
        print(f"Error durante la ingesta: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
