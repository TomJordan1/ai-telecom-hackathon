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
#
# El brief dimensiona el problema con "recibo actual + 5 recibos previos"
# (lo mismo que ya muestra la App Mi Movistar, pero sin explicarlo). Se trae
# ese mismo horizonte para poder comparar contra el mes inmediato anterior
# (causa puntual) y también detectar patrones recurrentes en la ventana de
# 5 meses (p. ej. "esto ya pasó 3 veces"), que es la explicación que un
# historial de un solo mes no puede dar.
HORIZONTE_RECIBOS = 6  # recibo actual + hasta 5 previos


def calculate_billing_facts(user_id: str, db: Session) -> Dict[str, Any]:
    """
    Calcula variaciones (Delta M) y genera el Deterministic Fact Payload.
    """
    recibos = crud.get_recibos_by_user(db, user_id, limit=HORIZONTE_RECIBOS)
    
    if not recibos:
        return {"error": "No hay recibos para este usuario."}
    
    current_bill = recibos[0]
    recibos_previos = recibos[1:]
    
    if not recibos_previos:
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
        
    previous_bill = recibos_previos[0]
    
    delta_m = round(current_bill.monto_total - previous_bill.monto_total, 2)
    variation_pct = round((delta_m / previous_bill.monto_total) * 100, 2) if previous_bill.monto_total > 0 else 0
    
    # Detectar el evento causal analizando 'conceptos_facturados'
    conceptos_actuales = current_bill.conceptos_facturados or {}
    conceptos_pasados = previous_bill.conceptos_facturados or {}
    
    detected_event = "SIN_CAMBIOS"
    evidence = []
    
    if delta_m > 0:
        if any("reconex" in str(k) or "cargo_reconexion" in str(k) for k in conceptos_actuales.keys()):
            detected_event = "RECONEXION_MOROSIDAD"
            evidence.append("Se aplicó un cargo de reconexión por suspensión previa del servicio.")

        elif "descuento_promo" in conceptos_pasados and "descuento_promo" not in conceptos_actuales:
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

    patron_recurrente = _detectar_patron_recurrente(detected_event, recibos_previos)
    if patron_recurrente:
        evidence.append(patron_recurrente)

    payload = {
        "moneda": MONEDA_CODIGO,
        "simbolo_moneda": MONEDA_SIMBOLO,
        "plan_actual": current_bill.plan_actual,
        "current_bill": {
            "amount": current_bill.monto_total,
            "issue_date": current_bill.mes_emision
        },
        "previous_bills": [
            {"month": r.mes_emision, "amount": r.monto_total}
            for r in recibos_previos
        ],
        "variation_amount": delta_m,
        "variation_percentage": variation_pct,
        "detected_event": detected_event,
        "evidence": evidence,
        "upcoming_alerts": _calculate_upcoming_alerts(current_bill)
    }
    
    return payload


def _detectar_patron_recurrente(detected_event: str, recibos_previos: List[Any]) -> Optional[str]:
    """
    Revisa la ventana completa de recibos previos (hasta 5 meses) para detectar
    si el mismo tipo de variación ya ocurrió antes. Un solo mes de comparación
    solo explica la causa puntual; esta señal explica el patrón ("esto ya pasó
    N veces"), que es justo lo que un historial de un solo mes no puede dar.

    No inventa nada: solo cuenta coincidencias de montos entre meses consecutivos
    dentro de los datos ya cargados, exactamente como el chequeo del mes actual.
    """
    if detected_event in ("SIN_CAMBIOS", "NUEVO_CLIENTE", "") or len(recibos_previos) < 2:
        return None

    ocurrencias = 1  # el mes actual ya cuenta como una ocurrencia
    for i in range(len(recibos_previos) - 1):
        mes_a, mes_b = recibos_previos[i], recibos_previos[i + 1]
        delta = round(mes_a.monto_total - mes_b.monto_total, 2)
        if abs(delta) < 0.01:
            continue
        conceptos_a = mes_a.conceptos_facturados or {}
        conceptos_b = mes_b.conceptos_facturados or {}
        # Reutiliza la misma heurística de detección por tipo de concepto, aplicada
        # par a par, para no duplicar la lógica de calculate_billing_facts.
        if detected_event == "FIN_PROMOCION" and "descuento_promo" in conceptos_b and "descuento_promo" not in conceptos_a:
            ocurrencias += 1
        elif detected_event == "CUOTA_EQUIPO" and any("cuota_equipo" in str(k) for k in conceptos_a.keys()):
            ocurrencias += 1
        elif detected_event == "RECONEXION_MOROSIDAD" and any("reconex" in str(k) for k in conceptos_a.keys()):
            ocurrencias += 1

    if ocurrencias >= 2:
        return (
            f"Este mismo tipo de variación ya se registró {ocurrencias} veces "
            f"en los últimos {len(recibos_previos)} meses."
        )
    return None


def _calculate_upcoming_alerts(current_bill, dias_umbral: int = 15) -> List[Dict[str, Any]]:
    """
    Detecta promociones activas con fecha de fin conocida y próxima a vencer.
    Es la base del enganche proactivo (alertar antes de que el cambio ocurra,
    no solo explicarlo después). Se calcula sobre 'promo_activa' dentro de
    conceptos_facturados: {"descuento": X, "fecha_fin": "YYYY-MM-DD"}.
    Si no existe ese dato, no se genera ninguna alerta (no se inventa nada).
    """
    conceptos = current_bill.conceptos_facturados or {}
    promo = conceptos.get("promo_activa")
    if not isinstance(promo, dict) or "fecha_fin" not in promo:
        return []

    try:
        fecha_fin = datetime.strptime(promo["fecha_fin"], "%Y-%m-%d")
        referencia = current_bill.fecha_emision or datetime.utcnow()
        dias_restantes = (fecha_fin - referencia).days
    except (ValueError, TypeError):
        return []

    if 0 <= dias_restantes <= dias_umbral:
        descuento = promo.get("descuento", 0)
        return [{
            "concepto": promo.get("nombre_concepto", "Descuento activo"),
            "fecha_fin": promo["fecha_fin"],
            "impacto_estimado": f"+{MONEDA_SIMBOLO} {abs(descuento):.2f}",
            "tipo": "FIN_PROMOCION",
            "dias_restantes": dias_restantes
        }]
    return []


def has_pending_followup_question(message: str) -> bool:
    """
    Detecta si el mensaje actual plantea una duda de seguimiento (el cliente
    sigue insatisfecho o tiene algo más que preguntar). Señal determinista
    para la condición 'no_preguntas_pendientes' del gatillo comercial: no se
    debe ofrecer nada mientras el cliente todavía tiene una duda abierta.
    """
    if "?" not in message:
        return False
    marcadores_duda = re.search(
        r"\b(pero|aun|a[uú]n|todav[ií]a|sigo|tambi[eé]n|adem[aá]s|otra duda|otra cosa|no entiendo)\b",
        message,
        re.IGNORECASE
    )
    return marcadores_duda is not None


def extract_emotional_comment(message: str) -> Optional[str]:
    """
    Detecta si el mensaje trae una expresión emocional explícita que merece
    quedar en memoria y ser referenciada cálidamente en un turno futuro
    (resignación, cansancio, "siempre pasa lo mismo", etc). Heurística ligera
    y determinista: no consume una llamada al LLM en cada turno de facturación.
    Retorna la frase a recordar, o None si no hay señal clara.
    """
    patrones = [
        r"\b(ntp|no hay problema|no te preocupes|tranquil[oa])\b.{0,30}\b(dif[ií]cil|complicado|entiendo)\b",
        r"\b(la verdad|sinceramente|honestamente)\b.{0,30}\b(cansad[oa]|frustrad[oa]|molest[oa]|preocupad[oa])\b",
        r"\b(siempre|otra vez|de nuevo)\b.{0,20}\b(lo mismo|igual|pasa esto|pasa lo mismo)\b",
        r"\b(se que no es tu culpa|sé que no es tu culpa|no es tu culpa)\b",
    ]
    for patron in patrones:
        if re.search(patron, message, re.IGNORECASE):
            return message.strip()[:200]
    return None


def is_case_resolved(detected_event: str) -> bool:
    """
    ¿La consulta fue clasificada como resuelta? Es decir, ¿se identificó una
    causa concreta y explicable, en vez de un evento ambiguo o inexistente?
    Señal determinista para la condición 'estado_resolucion' del gatillo comercial.
    """
    eventos_no_resueltos = {"SIN_CAMBIOS", "INCREMENTO_OTROS", "CONSULTA_GENERAL", "NUEVO_CLIENTE", ""}
    return detected_event not in eventos_no_resueltos


def recommend_plan_upgrade(db: Session, plan_actual_nombre: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Selecciona, de forma determinista, un plan superior real del catálogo
    (misma o menor tarifa, mayor velocidad) para el cross-sell. El LLM nunca
    elige ni inventa el plan recomendado: solo redacta el mensaje comercial
    sobre este dato ya verificado.
    """
    if not plan_actual_nombre:
        return None

    planes = crud.get_catalogo_planes(db)
    if not planes:
        return None

    def _velocidad(nombre: str) -> Optional[int]:
        match = re.search(r"(\d+)\s*Mbps", nombre or "", re.IGNORECASE)
        return int(match.group(1)) if match else None

    plan_actual = next((p for p in planes if p.nombre == plan_actual_nombre), None)
    if plan_actual is None:
        # No se puede verificar el precio actual contra el catálogo: no se arriesga
        # a proponer un plan sin certeza de que sea realmente una mejora.
        return None

    velocidad_actual = _velocidad(plan_actual_nombre)
    candidatos = []
    for plan in planes:
        if plan.nombre == plan_actual_nombre:
            continue
        if plan.precio > plan_actual.precio:
            continue  # nunca proponer algo más caro
        velocidad_candidato = _velocidad(plan.nombre)
        if velocidad_actual is not None and velocidad_candidato is not None and velocidad_candidato <= velocidad_actual:
            continue  # solo mejoras reales de velocidad
        candidatos.append((velocidad_candidato or 0, plan))

    if not candidatos:
        return None

    candidatos.sort(key=lambda t: t[0], reverse=True)
    mejor = candidatos[0][1]
    return {"nombre": mejor.nombre, "precio": mejor.precio, "beneficios": mejor.beneficios}

# 3. Gatillo Comercial Estricto
#
# Lista blanca alineada a los detected_event reales que produce el motor
# determinista. Quedan EXCLUIDOS a propósito: RECONEXION_MOROSIDAD (equivalente
# a deuda pendiente/mora), SIN_CAMBIOS, INCREMENTO_OTROS y CONSULTA_GENERAL
# (eventos no resueltos con certeza) — nunca se ofrece nada en esos casos.
LISTA_BLANCA_CROSS_SELL = ["FIN_PROMOCION", "PRORRATEO_CAMBIO_PLAN", "CUOTA_EQUIPO", "REDUCCION_TARIFA"]


def evaluate_cross_sell_eligibility(sentiment_score: int, estado_resolucion: bool, intent_category: str, no_preguntas_pendientes: bool) -> bool:
    """
    Evalúa las 4 condiciones estrictas para ofrecer planes de mayor valor.
    Las 4 condiciones deben calcularse con señales reales (ver is_case_resolved
    y has_pending_followup_question), no asumirse siempre verdaderas.
    """
    if (sentiment_score >= 4 and 
        estado_resolucion is True and 
        intent_category in LISTA_BLANCA_CROSS_SELL and 
        no_preguntas_pendientes is True):
        return True
    return False
