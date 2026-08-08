import re
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from app.db import crud
from datetime import datetime

# La moneda es un hecho determinista, no una inferencia del LLM.
# Sin este dato explícito el modelo puede alucinar símbolos (€, $) al redactar.
MONEDA_CODIGO = "PEN"
MONEDA_SIMBOLO = "S/"

# 1. Pre-Filtro de Cumplimiento
def validate_compliance(message: str, db: Session) -> Optional[str]:
    """
    Evalúa el mensaje del usuario contra los patrones regex de cumplimiento.
    Retorna un mensaje de bloqueo si se dispara una regla, de lo contrario None.
    """
    terminos = crud.get_terminos_restringidos(db)
    for regla in terminos:
        if re.search(regla.patron_regex, message, re.IGNORECASE):
            return regla.mensaje_bloqueo
    return None

# 2. Motor Investigador (Matemáticas y Hechos Deterministas)
def calculate_billing_facts(user_id: str, db: Session) -> Dict[str, Any]:
    """
    Calcula variaciones (Delta M) y genera el Deterministic Fact Payload.
    """
    recibos = crud.get_recibos_by_user(db, user_id, limit=2)
    
    if not recibos:
        return {"error": "No hay recibos para este usuario."}
    
    current_bill = recibos[0]
    
    if len(recibos) == 1:
        # Solo tiene un recibo, no hay historial con qué comparar.
        return {
            "moneda": MONEDA_CODIGO,
            "simbolo_moneda": MONEDA_SIMBOLO,
            "current_bill": {
                "amount": current_bill.monto_total,
                "issue_date": current_bill.mes_emision
            },
            "previous_bills": [],
            "variation_amount": 0.0,
            "variation_percentage": 0.0,
            "detected_event": "NUEVO_CLIENTE",
            "evidence": ["Primer recibo emitido."]
        }
        
    previous_bill = recibos[1]
    
    delta_m = round(current_bill.monto_total - previous_bill.monto_total, 2)
    variation_pct = round((delta_m / previous_bill.monto_total) * 100, 2) if previous_bill.monto_total > 0 else 0
    
    # Detectar el evento causal analizando 'conceptos_facturados'
    conceptos_actuales = current_bill.conceptos_facturados or {}
    conceptos_pasados = previous_bill.conceptos_facturados or {}
    
    detected_event = "SIN_CAMBIOS"
    evidence = []
    
    if delta_m > 0:
        if "descuento_promo" in conceptos_pasados and "descuento_promo" not in conceptos_actuales:
            detected_event = "FIN_PROMOCION"
            evidence.append(f"El descuento de S/ {abs(conceptos_pasados['descuento_promo'])} finalizó.")
        
        elif any("cuota_equipo" in str(k) for k in conceptos_actuales.keys()):
            detected_event = "CUOTA_EQUIPO"
            evidence.append("Se está cobrando una cuota de equipo financiado.")
            
        elif sum(1 for k in conceptos_actuales.keys() if "15dias" in k or "dias" in k) >= 2:
            detected_event = "PRORRATEO_CAMBIO_PLAN"
            evidence.append("Cobro proporcional por cambio de plan a mitad de ciclo.")
        else:
            detected_event = "INCREMENTO_OTROS"
            evidence.append("Incremento en cargos fijos detectado.")
            
    elif delta_m < 0:
        detected_event = "REDUCCION_TARIFA"
        evidence.append("Reducción en los montos facturados.")

    payload = {
        "moneda": MONEDA_CODIGO,
        "simbolo_moneda": MONEDA_SIMBOLO,
        "current_bill": {
            "amount": current_bill.monto_total,
            "issue_date": current_bill.mes_emision
        },
        "previous_bills": [
            {
                "month": previous_bill.mes_emision,
                "amount": previous_bill.monto_total
            }
        ],
        "variation_amount": delta_m,
        "variation_percentage": variation_pct,
        "detected_event": detected_event,
        "evidence": evidence,
        "upcoming_alerts": [] # Se podría calcular si se tuvieran fechas fin_promocion específicas
    }
    
    return payload

# 3. Gatillo Comercial Estricto
def evaluate_cross_sell_eligibility(sentiment_score: int, estado_resolucion: bool, intent_category: str, no_preguntas_pendientes: bool) -> bool:
    """
    Evalúa las 4 condiciones estrictas para ofrecer planes de mayor valor.
    """
    lista_blanca_intenciones = ["EXPLICACION_EXITOSA", "FIN_PROMOCION", "CAMBIO_PLAN", "PRUEBA_INICIAL"]
    
    if (sentiment_score >= 4 and 
        estado_resolucion is True and 
        intent_category in lista_blanca_intenciones and 
        no_preguntas_pendientes is True):
        return True
    return False
