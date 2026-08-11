import sys
import os
import random
from datetime import datetime
import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import engine, Base, SessionLocal
from app.db.models import ReciboCliente, CatalogoPlanes, TerminosRestringidos, ContactoUsuario

def init_db():
    print(f"Creando tablas en {engine.dialect.name}...")
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    
    # Check if we already have data
    if db.query(CatalogoPlanes).first():
        print("La base de datos ya contiene datos.")
        db.close()
        return

    # 1. Poblar Catalogo de Planes
    planes = [
        CatalogoPlanes(nombre="Internet Hogar 100 Mbps", precio=69.90, beneficios="Internet Ilimitado"),
        CatalogoPlanes(nombre="Internet Hogar 300 Mbps", precio=99.90, beneficios="Internet Ilimitado + Router Smart"),
        CatalogoPlanes(nombre="Internet 600 Mbps", precio=99.90, beneficios="Doble velocidad, Movistar Play incluido (PROMO)"),
        CatalogoPlanes(nombre="Internet Fibra 1000 Mbps", precio=149.90, beneficios="Máxima velocidad, 2 Repetidores Mesh"),
    ]
    db.add_all(planes)

    # 2. Poblar Términos Restringidos
    terminos = [
        TerminosRestringidos(patron_regex=r"\b(m[ií]erda|put[oa]|imb[ée]cil|est[úu]pido)\b", accion_disparador="INSULTO", mensaje_bloqueo="Por favor mantengamos el respeto en nuestra conversación. Estoy aquí para ayudarte."),
        TerminosRestringidos(patron_regex=r"\b(denunciar|indecopi|abogado|demanda)\b", accion_disparador="LEGAL_RIESGO", mensaje_bloqueo="Entiendo tu molestia. Quiero ayudarte a resolver esto de la mejor manera. Déjame revisar tu caso en detalle."),
        TerminosRestringidos(patron_regex=r"\b(\d{16}|\d{4}-\d{4}-\d{4}-\d{4})\b", accion_disparador="DATOS_SENSIBLES", mensaje_bloqueo="Por tu seguridad, no compartas números de tarjeta de crédito por este medio.")
    ]
    db.add_all(terminos)

    # 3. Leer y procesar CSVs
    print("Cargando CSVs con pandas (esto puede tomar unos segundos)...")
    
    # Limitar el número de cuentas para agilizar el entorno de desarrollo local.
    MAX_USERS = 500
    
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    REPO_DIR = os.path.join(BASE_DIR, "ai-telecom-hackathon")
    DISCLAIMER_DIR = os.path.join(REPO_DIR, "disclaimer")

    path_cargos = os.path.join(DISCLAIMER_DIR, "Cargos_FacturadosV2.csv")
    path_clientes = os.path.join(DISCLAIMER_DIR, "REGISTROS_CLIENTES_20MIL.csv")
    
    try:
        df_clientes = pd.read_csv(path_clientes, sep=";", dtype=str)
        df_cargos = pd.read_csv(path_cargos, sep=";")
    except Exception as e:
        print(f"Error al leer los CSVs: {e}")
        db.close()
        return

    # Extraer las top MAX_USERS cuentas
    unique_accounts = df_cargos['FINANCIAL_ACCOUNT_KEY'].unique()[:MAX_USERS]
    df_cargos = df_cargos[df_cargos['FINANCIAL_ACCOUNT_KEY'].isin(unique_accounts)]
    
    print(f"Procesando recibos para {len(unique_accounts)} cuentas...")

    # Agrupar por cuenta y ciclo de facturacion (ciclo = mes)
    grouped = df_cargos.groupby(['FINANCIAL_ACCOUNT_KEY', 'ciclo'])
    
    recibos_a_insertar = []
    
    # Asignar un plan al azar de nuestro catalogo reducido a cada usuario, para efectos de la demo.
    planes_nombres = [p.nombre for p in planes]
    user_plan_map = {acc: random.choice(planes_nombres) for acc in unique_accounts}

    for (account_id, ciclo), group in grouped:
        monto_total = round(group['CHARGE_TOTAL_AMOUNT'].sum(), 2)
        
        # Formatear fecha de emision y mes
        # ciclo viene como YYYYMMDD (ej. 20260705). Asumiremos el inicio de ese mes
        ciclo_str = str(ciclo)
        if len(ciclo_str) >= 6:
            mes_emision = f"{ciclo_str[:4]}-{ciclo_str[4:6]}"
            fecha_emision = datetime(int(ciclo_str[:4]), int(ciclo_str[4:6]), 1)
        else:
            mes_emision = "2026-01"
            fecha_emision = datetime(2026, 1, 1)

        conceptos_facturados = {}
        for _, row in group.iterrows():
            desc = str(row['CHARGE_CODE_DESC']).lower().replace(" ", "_")
            if desc not in conceptos_facturados:
                conceptos_facturados[desc] = 0.0
            conceptos_facturados[desc] += row['CHARGE_TOTAL_AMOUNT']

        # Redondear valores
        for k in conceptos_facturados:
            conceptos_facturados[k] = round(conceptos_facturados[k], 2)
            
        # Inyectar promo_activa aleatoriamente para probar las alertas proactivas
        if random.random() < 0.1:
            conceptos_facturados["promo_activa"] = {
                "nombre_concepto": f"Descuento {user_plan_map[str(account_id)]}",
                "fecha_fin": "2026-08-06",
                "descuento": -20.00
            }

        recibos_a_insertar.append(
            ReciboCliente(
                user_id=str(account_id),
                mes_emision=mes_emision,
                monto_total=monto_total,
                fecha_emision=fecha_emision,
                conceptos_facturados=conceptos_facturados,
                plan_actual=user_plan_map[str(account_id)]
            )
        )
        
    db.add_all(recibos_a_insertar)

    # 4. Poblar ContactosUsuario
    print("Creando contactos de usuario...")
    contactos_a_insertar = []
    
    # Convert FINANCIAL_ACCOUNT_KEY in clientes to string for mapping
    df_clientes['FINANCIAL_ACCOUNT'] = df_clientes['FINANCIAL_ACCOUNT'].astype(str)
    
    for account_id in unique_accounts:
        acc_str = str(account_id)
        # Buscar en df_clientes
        cliente_info = df_clientes[df_clientes['FINANCIAL_ACCOUNT'] == acc_str]
        telefono = None
        if not cliente_info.empty:
            # En la vida real usariamos un hash o numero real. Aquí, para la demo,
            # como telefono_hash es muy largo, asignaremos un numero correlativo para pruebas web.
            pass
        
        # Asignar un "DNI" o numero facil para testing.
        # Por simplicidad en la demo, el telefono será el mismo account_id pero empezando con 9.
        telefono = f"9{str(account_id)[-8:]}".ljust(9, '0')
        
        contactos_a_insertar.append(
            ContactoUsuario(
                user_id=acc_str,
                whatsapp_number=telefono,
                telegram_chat_id=None
            )
        )
        
    db.add_all(contactos_a_insertar)

    db.commit()
    print(f"Datos reales (Limitados a {MAX_USERS} usuarios) insertados exitosamente en {engine.dialect.name}.")
    
    # Print sample credentials for testing
    print("\n--- Credenciales para Testing (Web) ---")
    for c in contactos_a_insertar[:5]:
        print(f"User ID: {c.user_id} | Teléfono/Doc: {c.whatsapp_number}")
    print("---------------------------------------")

    db.close()

if __name__ == "__main__":
    init_db()
