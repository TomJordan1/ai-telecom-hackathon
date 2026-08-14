"""
ingest_real_data.py
-------------------
Ingesta exclusiva de datos reales derivados de la carpeta `disclaimer/`.
Fuente de datos:
  - disclaimer/FACTURACION_CLIENTES.csv (delimitador ';')
  - disclaimer/PLANTA_CLIENTES.csv       (delimitador ';')
  - disclaimer/ORDENES.csv               (delimitador ',')
  - disclaimer/NOTAS_CREDITO.csv          (delimitador ',')
  - disclaimer/CATALOGO_OFERTAS.csv       (delimitador ';')

No genera datos ficticios, planes aleatorios ni promociones inventadas.
"""

import argparse
import os
import sys
from pathlib import Path
from datetime import datetime
import pandas as pd

# La URL SQLite/Postgres por defecto es relativa al directorio de trabajo.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from app.db.database import engine, Base, SessionLocal
from app.services import deterministic
from app.db.models import (
    FacturacionCliente,
    TerminosRestringidos,
    ContactoUsuario,
    OrdenCliente,
    NotaCredito,
    PlantaCliente,
    CatalogoOfertas,
)

# ---------------------------------------------------------------------------
# Rutas absolutas a los CSV del directorio disclaimer
# ---------------------------------------------------------------------------
DISCLAIMER_DIR = PROJECT_ROOT / "disclaimer"
PATH_FACTURACION = DISCLAIMER_DIR / "FACTURACION_CLIENTES.csv"
PATH_PLANTA = DISCLAIMER_DIR / "PLANTA_CLIENTES.csv"
PATH_ORDENES = DISCLAIMER_DIR / "ORDENES.csv"
PATH_NOTAS_CREDITO = DISCLAIMER_DIR / "NOTAS_CREDITO.csv"
PATH_CATALOGO_OFERTAS = DISCLAIMER_DIR / "CATALOGO_OFERTAS.csv"


# Marcadores de valor ausente que aparecen al convertir celdas vacías de pandas
# con str(). Sin filtrarlos, la cadena literal "nan" acababa almacenada como si
# fuera el motivo real de una orden y se enviaba al modelo como evidencia.
_VALORES_NULOS = {"", "nan", "none", "null", "nat", "<na>"}


def _texto(value) -> str:
    """Convierte una celda a texto limpio, devolviendo '' si no hay valor real."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    texto = str(value).strip()
    return "" if texto.lower() in _VALORES_NULOS else texto


def _normalize_key(value) -> str:
    """
    Normaliza identificadores a str entero sin decimales ni espacios.
    Evita diferencias como '102968745.0' vs '102968745'.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    s = str(value).strip()
    if "." in s:
        try:
            s = str(int(float(s)))
        except (ValueError, OverflowError):
            pass
    return s


# ---------------------------------------------------------------------------
# Selección de cuentas
# ---------------------------------------------------------------------------
#
# Los escenarios críticos del desafío no están repartidos de forma uniforme: en
# el dataset completo hay 1652 cuentas con prorrateo pero solo 17 con cuota de
# equipo financiado. Tomar "las primeras N cuentas del archivo" deja escenarios
# enteros fuera de la base y hace imposible demostrarlos.
#
# Por eso la selección se hace por cobertura: primero se asegura que entren
# cuentas de cada escenario, y solo después se rellena hasta el límite pedido.

MIN_CICLOS_ESCENARIO = 3   # ciclos necesarios para que la comparación tenga sentido
CUOTA_POR_ESCENARIO = 40   # cuentas a reservar por escenario, si existen

# Eventos que corresponden a los cinco escenarios que el desafío pide demostrar.
EVENTOS_ESCENARIO = (
    "PRORRATEO_CAMBIO_PLAN",   # (a) prorrateos
    "CUOTA_EQUIPO",            # (b) cuota de equipo financiado
    "RECONEXION_MOROSIDAD",    # (c) reconexión tras suspensión morosa
    "FIN_PROMOCION",           # (d) fin de descuentos
    "CAMBIO_PLAN",             # (e) cambios de plan
    # Otras causas de variación que el desafío menciona explícitamente.
    "COMPRA_PAQUETE",
    "TRAFICO_ADICIONAL",
    # NOTA: NOTA_CREDITO_AJUSTE no se puede priorizar en esta etapa. Ese evento
    # nace de cruzar el recibo con NOTAS_CREDITO.csv, y aquí solo se ha leído
    # FACTURACION_CLIENTES.csv. Las notas de crédito de las cuentas elegidas se
    # ingieren más abajo, así que el evento sí se detecta en tiempo de ejecución.
)


def _evento_por_cuenta(df_fact: "pd.DataFrame") -> dict:
    """
    Determina qué evento detectaría el motor determinista en el último ciclo de
    cada cuenta. Usa el MISMO módulo que la aplicación en tiempo de ejecución
    (`app.services.deterministic`), así que la selección no puede divergir de lo
    que Lucía realmente explicará.
    """
    columnas = [
        "FINANCIAL_ACCOUNT_KEY", "ciclo", "CHARGE_TOTAL_AMOUNT", "CHARGE_CODE_ID",
        "CHARGE_CODE_DESC", "CHARGE_CODE_CLASSIFICATION", "GRUPO", "SUB_GRUPO",
    ]
    reducido = df_fact[columnas].copy()
    reducido["ciclo"] = reducido["ciclo"].astype(str)

    eventos = {}
    for cuenta, filas_cuenta in reducido.groupby("FINANCIAL_ACCOUNT_KEY", sort=False):
        ciclos = sorted(filas_cuenta["ciclo"].unique(), reverse=True)
        if len(ciclos) < MIN_CICLOS_ESCENARIO:
            continue

        def cargos_de(ciclo):
            sub = filas_cuenta[filas_cuenta["ciclo"] == ciclo]
            return sub.rename(columns={"CHARGE_TOTAL_AMOUNT": "CHARGE_TOTAL_AMOUNT"}).to_dict("records")

        cargos_actuales = cargos_de(ciclos[0])
        cargos_previos = cargos_de(ciclos[1])
        delta = round(
            sum(c["CHARGE_TOTAL_AMOUNT"] for c in cargos_actuales)
            - sum(c["CHARGE_TOTAL_AMOUNT"] for c in cargos_previos),
            2,
        )
        componentes = deterministic.descomponer_variacion(cargos_actuales, cargos_previos)
        eventos[cuenta] = (deterministic._detectar_evento(componentes, delta), len(ciclos))

    return eventos


def seleccionar_cuentas(df_fact: "pd.DataFrame", max_users: int) -> set:
    """
    Elige qué cuentas ingerir garantizando que los escenarios del desafío queden
    representados. Con max_users <= 0 se cargan todas y no hace falta priorizar.
    """
    todas = list(df_fact["FINANCIAL_ACCOUNT_KEY"].unique())
    if not max_users or max_users <= 0:
        print(f"  -> Se ingieren todas las cuentas del archivo: {len(todas)}")
        return set(todas)

    print("Clasificando cuentas por escenario para garantizar cobertura de la demo...")
    eventos = _evento_por_cuenta(df_fact)

    por_evento = {}
    for cuenta, (evento, n_ciclos) in eventos.items():
        por_evento.setdefault(evento, []).append((n_ciclos, cuenta))

    seleccionadas = []
    vistas = set()
    for evento in EVENTOS_ESCENARIO:
        candidatos = sorted(por_evento.get(evento, []), reverse=True)
        elegidos = [cuenta for _, cuenta in candidatos[:CUOTA_POR_ESCENARIO]]
        nuevos = [c for c in elegidos if c not in vistas]
        vistas.update(nuevos)
        seleccionadas.extend(nuevos)
        estado = f"{len(nuevos)} cuenta(s)" if nuevos else "sin cobertura en el dataset"
        print(f"  - {evento:24} {estado} (candidatas: {len(candidatos)})")

    if len(seleccionadas) > max_users:
        print(f"  [AVISO] Los escenarios requieren {len(seleccionadas)} cuentas y max_users={max_users}. "
              f"Se amplía el límite para no dejar escenarios sin datos.")
    else:
        # Se rellena con el resto de cuentas en orden de archivo hasta el límite.
        for cuenta in todas:
            if len(seleccionadas) >= max_users:
                break
            if cuenta not in vistas:
                vistas.add(cuenta)
                seleccionadas.append(cuenta)

    print(f"  -> Cuentas seleccionadas: {len(seleccionadas)}")
    return set(seleccionadas)


def init_db(reset: bool = False, max_users: int = 1000):
    print("=" * 70)
    print(f"  Ingesta de Datos Reales (`disclaimer/`) -> Engine: {engine.dialect.name}")
    print("=" * 70)

    print("Creando tablas si no existen...")
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()

    try:
        if reset:
            print("(--reset activado) Limpiando datos previos de facturación y entidades relacionadas...")
            db.query(FacturacionCliente).delete()
            db.query(OrdenCliente).delete()
            db.query(NotaCredito).delete()
            db.query(PlantaCliente).delete()
            db.query(CatalogoOfertas).delete()
            db.commit()
            print("Tablas operacionales limpiadas exitosamente.")
        elif db.query(FacturacionCliente).first():
            print("La base de datos ya contiene cargos en facturacion_clientes. Usa --reset si deseas volver a ingerir.")
            return

        # ------------------------------------------------------------------
        # 2. Poblar Términos Restringidos
        # ------------------------------------------------------------------
        if not db.query(TerminosRestringidos).first():
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
            db.commit()

        # ------------------------------------------------------------------
        # 3. Ingerir CATALOGO_OFERTAS.csv
        # ------------------------------------------------------------------
        BATCH_SIZE = 1000

        if PATH_CATALOGO_OFERTAS.exists():
            print(f"Cargando {PATH_CATALOGO_OFERTAS.name}...")
            df_ofertas = pd.read_csv(PATH_CATALOGO_OFERTAS, sep=";")
            ofertas_objs = []
            for _, r in df_ofertas.iterrows():
                cc = _texto(r.get("CHARGE CODE"))
                if cc:
                    ofertas_objs.append(CatalogoOfertas(
                        charge_code=cc,
                        rate_final=float(pd.to_numeric(r.get("rate_final"), errors="coerce") or 0.0),
                        tipo_renta=_texto(r.get("TIPO DE RENTA")),
                    ))
            for i in range(0, len(ofertas_objs), BATCH_SIZE):
                db.add_all(ofertas_objs[i:i + BATCH_SIZE])
                db.commit()
            print(f"  -> {len(ofertas_objs)} ofertas agregadas al catálogo.")

        # ------------------------------------------------------------------
        # 4. Ingerir FACTURACION_CLIENTES.csv
        # ------------------------------------------------------------------
        print(f"Cargando {PATH_FACTURACION.name}...")
        df_fact = pd.read_csv(PATH_FACTURACION, sep=";")
        
        # Limpiar espacios en los nombres de columnas (e.g. 'FECHA-VENCIMIENTO ')
        df_fact.columns = [c.strip() for c in df_fact.columns]

        df_fact["FINANCIAL_ACCOUNT_KEY"] = df_fact["FINANCIAL_ACCOUNT_KEY"].apply(_normalize_key)
        df_fact["CUSTOMER_KEY"] = df_fact["CUSTOMER_KEY"].apply(_normalize_key)
        df_fact["CHARGE_NET_AMOUNT"] = pd.to_numeric(df_fact["CHARGE_NET_AMOUNT"], errors="coerce").fillna(0.0)
        df_fact["CHARGE_TOTAL_AMOUNT"] = pd.to_numeric(df_fact["CHARGE_TOTAL_AMOUNT"], errors="coerce").fillna(0.0)

        target_accounts = seleccionar_cuentas(df_fact, max_users)
        df_fact_filtered = df_fact[df_fact["FINANCIAL_ACCOUNT_KEY"].isin(target_accounts)]

        customer_keys_ingeridos = set(df_fact_filtered["CUSTOMER_KEY"].dropna().unique())

        cargos_individuales = []
        for _, row in df_fact_filtered.iterrows():
            cargos_individuales.append(FacturacionCliente(
                financial_account_key=_texto(row.get("FINANCIAL_ACCOUNT_KEY")),
                customer_key=_texto(row.get("CUSTOMER_KEY")),
                billing_arrangement_key=_texto(row.get("BILLING_ARRANGEMENT_KEY")),
                legal_invoice_number=_texto(row.get("LEGAL_INVOICE_NUMBER")),
                billing_cycle_key=int(row["BILLING_CYCLE_KEY"]) if pd.notna(row.get("BILLING_CYCLE_KEY")) else None,
                charge_net_amount=float(row.get("CHARGE_NET_AMOUNT", 0.0)),
                charge_total_amount=float(row.get("CHARGE_TOTAL_AMOUNT", 0.0)),
                charge_code_id=_texto(row.get("CHARGE_CODE_ID")),
                charge_code_desc=_texto(row.get("CHARGE_CODE_DESC")),
                charge_code_classification=_texto(row.get("CHARGE_CODE_CLASSIFICATION")),
                subscriber_key=_texto(row.get("SUBSCRIBER_KEY")),
                period_start_date=_texto(row.get("PERIOD_START_DATE")),
                period_end_date=_texto(row.get("PERIOD_END_DATE")),
                ciclo=_texto(row.get("ciclo")),
                grupo=_texto(row.get("GRUPO")),
                sub_grupo=_texto(row.get("SUB_GRUPO")),
                fecha_vencimiento=_texto(row.get("FECHA-VENCIMIENTO")),
                deuda=_texto(row.get("DEUDA")),
            ))

        # Inserción por lotes para evitar sobrecargar la conexión
        for i in range(0, len(cargos_individuales), BATCH_SIZE):
            db.add_all(cargos_individuales[i:i + BATCH_SIZE])
            db.commit()

        print(f"  -> {len(cargos_individuales)} cargos individuales insertados y confirmados en 'facturacion_clientes'.")

        # ------------------------------------------------------------------
        # 5. Poblar ContactosUsuario
        # ------------------------------------------------------------------
        # Se descartan primero los marcadores de posición de ingestas anteriores:
        # contactos sin ningún canal configurado cuya cuenta ya no está cargada.
        # Sin esta limpieza la tabla acumula cuentas inexistentes en cada corrida.
        # Los contactos CON canal (WhatsApp/Telegram reales) nunca se tocan.
        objetivo = {str(a) for a in target_accounts}
        huerfanos = (
            db.query(ContactoUsuario)
            .filter(
                ContactoUsuario.whatsapp_number.is_(None),
                ContactoUsuario.telegram_chat_id.is_(None),
            )
            .all()
        )
        eliminados = 0
        for contacto in huerfanos:
            if str(contacto.user_id) not in objetivo:
                db.delete(contacto)
                eliminados += 1
        if eliminados:
            db.commit()
            print(f"  -> {eliminados} contacto(s) sin canal de ingestas previas eliminados.")

        existing_contacts = {c[0] for c in db.query(ContactoUsuario.user_id).all()}
        contactos_a_insertar = [
            ContactoUsuario(user_id=str(acc), whatsapp_number=None, telegram_chat_id=None)
            for acc in target_accounts if str(acc) not in existing_contacts
        ]
        for i in range(0, len(contactos_a_insertar), BATCH_SIZE):
            db.add_all(contactos_a_insertar[i:i + BATCH_SIZE])
            db.commit()
        print(f"  -> {len(contactos_a_insertar)} contacto(s) de cuenta registrados.")

        # ------------------------------------------------------------------
        # 6. Ingerir PLANTA_CLIENTES.csv
        # ------------------------------------------------------------------
        if PATH_PLANTA.exists():
            print(f"Cargando {PATH_PLANTA.name}...")
            df_planta = pd.read_csv(PATH_PLANTA, sep=";", dtype=str)
            df_planta.columns = [c.strip() for c in df_planta.columns]
            df_planta["FINANCIAL_ACCOUNT"] = df_planta["FINANCIAL_ACCOUNT"].apply(_normalize_key)
            df_planta["COD_CLIENTE"] = df_planta["COD_CLIENTE"].apply(_normalize_key)

            df_planta_filtered = df_planta[df_planta["FINANCIAL_ACCOUNT"].isin(target_accounts)]
            planta_objs = []
            for _, r in df_planta_filtered.iterrows():
                planta_objs.append(PlantaCliente(
                    cod_cliente=_texto(r.get("COD_CLIENTE")),
                    financial_account=_texto(r.get("FINANCIAL_ACCOUNT")),
                    num_anexo=_texto(r.get("NUM_ANEXO")),
                    telefono_hash=_texto(r.get("telefono_hash")),
                    fecha_activacion_original=_texto(r.get("fecha_activacion_original")),
                    ciclo=_texto(r.get("ciclo")),
                    lob_type=_texto(r.get("lob_type")),
                    negocio=_texto(r.get("negocio")),
                ))
            for i in range(0, len(planta_objs), BATCH_SIZE):
                db.add_all(planta_objs[i:i + BATCH_SIZE])
                db.commit()
            print(f"  -> {len(planta_objs)} registros de planta de clientes agregados.")

        # ------------------------------------------------------------------
        # 7. Ingerir ORDENES.csv
        # ------------------------------------------------------------------
        if PATH_ORDENES.exists():
            print(f"Cargando {PATH_ORDENES.name}...")
            df_ord = pd.read_csv(PATH_ORDENES, sep=",")
            df_ord.columns = [c.strip() for c in df_ord.columns]
            df_ord["CUSTOMER_KEY"] = df_ord["CUSTOMER_KEY"].apply(_normalize_key)

            df_ord_filtered = df_ord[df_ord["CUSTOMER_KEY"].isin(customer_keys_ingeridos)]
            ordenes_objs = []
            for _, r in df_ord_filtered.iterrows():
                s_dt = pd.to_datetime(r.get("ORDER_ACTION_START_DATE"), errors="coerce")
                c_dt = pd.to_datetime(r.get("ORDER_ACTION_COMPLETION_DATE"), errors="coerce")
                ordenes_objs.append(OrdenCliente(
                    customer_key=_texto(r.get("CUSTOMER_KEY")),
                    subscriber_key=_texto(r.get("SUBSCRIBER_KEY")),
                    order_type=_texto(r.get("ORDER_ITEM_TYPE_DESC")),
                    order_reason=_texto(r.get("ORDER_ACTION_REASON_DESC")),
                    start_date=s_dt if pd.notna(s_dt) else None,
                    completion_date=c_dt if pd.notna(c_dt) else None,
                ))
            for i in range(0, len(ordenes_objs), BATCH_SIZE):
                db.add_all(ordenes_objs[i:i + BATCH_SIZE])
                db.commit()
            print(f"  -> {len(ordenes_objs)} órdenes clientes agregadas.")

        # ------------------------------------------------------------------
        # 8. Ingerir NOTAS_CREDITO.csv
        # ------------------------------------------------------------------
        if PATH_NOTAS_CREDITO.exists():
            print(f"Cargando {PATH_NOTAS_CREDITO.name}...")
            df_nc = pd.read_csv(PATH_NOTAS_CREDITO, sep=",")
            df_nc.columns = [c.strip() for c in df_nc.columns]
            df_nc["RECEIVER_CUSTOMER"] = df_nc["RECEIVER_CUSTOMER"].apply(_normalize_key)
            df_nc["BA_NO"] = df_nc["BA_NO"].apply(_normalize_key)

            df_nc_filtered = df_nc[
                (df_nc["RECEIVER_CUSTOMER"].isin(customer_keys_ingeridos)) |
                (df_nc["BA_NO"].isin(target_accounts))
            ]
            nc_objs = []
            for _, r in df_nc_filtered.iterrows():
                eff_dt = pd.to_datetime(r.get("EFFECTIVE_DATE"), errors="coerce")
                p_start = pd.to_datetime(r.get("PERIOD_START_DATE"), errors="coerce")
                p_end = pd.to_datetime(r.get("PERIOD_END_DATE"), errors="coerce")
                amt = float(pd.to_numeric(r.get("AMOUNT"), errors="coerce") or 0.0)

                nc_objs.append(NotaCredito(
                    receiver_customer=_texto(r.get("RECEIVER_CUSTOMER")),
                    ba_no=_texto(r.get("BA_NO")),
                    service_receiver_id=_texto(r.get("SERVICE_RECEIVER_ID")),
                    charge_code=_texto(r.get("CHARGE_CODE")),
                    cancel_charge_type=_texto(r.get("CANCEL_CHARGE_TYPE")),
                    effective_date=eff_dt if pd.notna(eff_dt) else None,
                    amount=amt,
                    period_start_date=p_start if pd.notna(p_start) else None,
                    period_end_date=p_end if pd.notna(p_end) else None,
                    ciclo=_texto(r.get("CICLO")),
                ))
            for i in range(0, len(nc_objs), BATCH_SIZE):
                db.add_all(nc_objs[i:i + BATCH_SIZE])
                db.commit()
            print(f"  -> {len(nc_objs)} notas de crédito agregadas.")

        # ------------------------------------------------------------------
        # Commit general
        # ------------------------------------------------------------------
        db.commit()
        print("-" * 70)
        print(f"¡Ingesta exitosa en {engine.dialect.name}!")
        print(f"Cuentas cargadas: {len(target_accounts)}")
        print(f"Cargos en facturacion_clientes: {len(cargos_individuales)}")

        print("\nEjemplos de cuentas listos para probar:")
        for c in list(target_accounts)[:5]:
            print(f"  - Account ID (user_id): {c}")
        print("=" * 70)

    except Exception as e:
        print(f"[ERROR] Falló la ingesta: {e}")
        db.rollback()
        raise
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Ingesta de datos reales desde disclaimer/")
    parser.add_argument("--reset", action="store_true", help="Limpia los datos previos antes de insertar.")
    parser.add_argument("--max-users", type=int, default=1000, help="Límite de cuentas únicas a cargar (0 para todas).")
    args = parser.parse_args()

    init_db(reset=args.reset, max_users=args.max_users)


if __name__ == "__main__":
    main()
