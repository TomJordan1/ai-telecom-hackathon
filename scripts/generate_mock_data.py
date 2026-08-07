import sys
import os
import random
from datetime import datetime

# Add the root directory to sys.path so we can import 'app'
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import engine, Base, SessionLocal
from app.db.models import ReciboCliente, HistorialInteracciones, CatalogoPlanes, TerminosRestringidos

def init_db():
    # Create tables
    print("Creando tablas en SQLite...")
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

    # 3. Poblar Recibos Cliente (Mock Data para 3 usuarios representativos)
    
    # Usuario A: Escenario de Fin de Promoción
    # El mes actual (2026-08) subió a 119.90, el mes pasado (2026-07) era 99.90
    db.add(ReciboCliente(user_id="user_a_fin_promo", mes_emision="2026-07", monto_total=99.90, fecha_emision=datetime(2026, 7, 1), conceptos_facturados={"cargo_fijo": 119.90, "descuento_promo": -20.00}, plan_actual="Internet Hogar 300 Mbps"))
    db.add(ReciboCliente(user_id="user_a_fin_promo", mes_emision="2026-08", monto_total=119.90, fecha_emision=datetime(2026, 8, 1), conceptos_facturados={"cargo_fijo": 119.90}, plan_actual="Internet Hogar 300 Mbps"))
    
    # Usuario B: Escenario de Prorrateo por Cambio de Plan
    # En 2026-07 pagaba 69.90 (100Mbps). A mitad de mes cambió a 300Mbps (99.90).
    db.add(ReciboCliente(user_id="user_b_prorrateo", mes_emision="2026-07", monto_total=69.90, fecha_emision=datetime(2026, 7, 1), conceptos_facturados={"cargo_fijo": 69.90}, plan_actual="Internet Hogar 100 Mbps"))
    db.add(ReciboCliente(user_id="user_b_prorrateo", mes_emision="2026-08", monto_total=84.90, fecha_emision=datetime(2026, 8, 1), conceptos_facturados={"cargo_fijo_100M_15dias": 34.95, "cargo_fijo_300M_15dias": 49.95}, plan_actual="Internet Hogar 300 Mbps"))

    # Usuario C: Escenario Cuota de Equipo
    # Compró un repetidor de 120 soles financiado en 6 cuotas de 20 soles.
    db.add(ReciboCliente(user_id="user_c_equipo", mes_emision="2026-07", monto_total=99.90, fecha_emision=datetime(2026, 7, 1), conceptos_facturados={"cargo_fijo": 99.90}, plan_actual="Internet Hogar 300 Mbps"))
    db.add(ReciboCliente(user_id="user_c_equipo", mes_emision="2026-08", monto_total=119.90, fecha_emision=datetime(2026, 8, 1), conceptos_facturados={"cargo_fijo": 99.90, "cuota_equipo_1_de_6": 20.00}, plan_actual="Internet Hogar 300 Mbps"))

    db.commit()
    print("Datos de prueba (Mocks) insertados exitosamente en SQLite.")
    db.close()

if __name__ == "__main__":
    init_db()
