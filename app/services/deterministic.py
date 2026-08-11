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


def _extraer_cargos(conceptos_facturados: Optional[Dict]) -> List[Dict[str, Any]]:
    """
    Extrae la lista de cargos individuales de conceptos_facturados,
    soportando tanto el formato nuevo (datos reales del CSV):
        {"cargos": [...], "info_factura": {...}}
    como el formato legacy (datos mock de generate_mock_data.py):
        {"cargo_fijo": 99.90, "descuento_promo": -20.00}
    """
    if not conceptos_facturados:
        return []
    # Formato nuevo: tiene clave "cargos" con una lista
    if "cargos" in conceptos_facturados and isinstance(conceptos_facturados["cargos"], list):
        return conceptos_facturados["cargos"]
    # Formato legacy: dict plano {concepto: monto}
    return []


def _es_formato_legacy(conceptos_facturados: Optional[Dict]) -> bool:
    """Detecta si conceptos_facturados usa el formato plano legacy (mock data)."""
    if not conceptos_facturados:
        return False
    # El formato nuevo siempre tiene "cargos" como key
    return "cargos" not in conceptos_facturados


def _detectar_evento_legacy(conceptos_actuales: Dict, conceptos_pasados: Dict, delta_m: float) -> tuple:
    """
    Detección de evento para el formato legacy (generate_mock_data.py).
    Devuelve (detected_event, evidence_list).
    """
    detected_event = "SIN_CAMBIOS"
    evidence = []

    if delta_m > 0:
        if any("reconex" in str(k) or "cargo_reconexion" in str(k) for k in conceptos_actuales.keys()):
            detected_event = "RECONEXION_MOROSIDAD"
            evidence.append("Se aplicó un cargo de reconexión por suspensión previa del servicio.")
        elif "descuento_promo" in conceptos_pasados and "descuento_promo" not in conceptos_actuales:
            detected_event = "FIN_PROMOCION"
            evidence.append(f"El descuento de {MONEDA_SIMBOLO} {abs(conceptos_pasados['descuento_promo'])} finalizó.")
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

    return detected_event, evidence


def _detectar_evento_real(cargos_actuales: List[Dict], cargos_pasados: List[Dict], delta_m: float) -> tuple:
    """
    Detección de evento para el formato real (ingest_real_data.py).
    Analiza GRUPO, SUB_GRUPO y CHARGE_CODE_CLASSIFICATION de cada cargo.
    Devuelve (detected_event, evidence_list).
    """
    detected_event = "SIN_CAMBIOS"
    evidence = []

    if abs(delta_m) < 0.01:
        return detected_event, evidence

    # Construir sets de señales del periodo actual
    grupos_actuales = {c.get("GRUPO", "").upper() for c in cargos_actuales}
    subgrupos_actuales = {c.get("SUB_GRUPO", "").upper() for c in cargos_actuales}
    clasificaciones_actuales = {c.get("CHARGE_CODE_CLASSIFICATION", "").lower() for c in cargos_actuales}

    # Construir sets de señales del periodo anterior
    clasificaciones_pasadas = {c.get("CHARGE_CODE_CLASSIFICATION", "").lower() for c in cargos_pasados}
    subgrupos_pasados = {c.get("SUB_GRUPO", "").upper() for c in cargos_pasados}

    if delta_m > 0:
        # 1. RECONEXION — señal inequívoca
        if "CARGO POR RECONEXION" in grupos_actuales or "CARGO POR RECONEXION" in subgrupos_actuales:
            detected_event = "RECONEXION_MOROSIDAD"
            cargos_reconexion = [c for c in cargos_actuales
                                 if "RECONEXION" in c.get("GRUPO", "").upper()
                                 or "RECONEXION" in c.get("SUB_GRUPO", "").upper()]
            monto_reconexion = sum(c.get("CHARGE_TOTAL_AMOUNT", 0) for c in cargos_reconexion)
            evidence.append(f"Se aplicó un cargo de reconexión por {MONEDA_SIMBOLO} {monto_reconexion:.2f} por suspensión previa del servicio.")

        # 2. FIN_PROMOCION — bonificación/descuento que estaba antes y ya no está
        elif _descuento_desaparecido(cargos_actuales, cargos_pasados):
            detected_event = "FIN_PROMOCION"
            descuentos_perdidos = _descuento_desaparecido(cargos_actuales, cargos_pasados)
            evidence.append(f"Finalizó un descuento/bono que estaba vigente: {descuentos_perdidos}.")

        # 3. CUOTA_EQUIPO — financiamiento de equipo
        elif any("financiamiento" in cl for cl in clasificaciones_actuales) \
                or "FINANCIAMIENTO" in subgrupos_actuales or "EQUIPOS" in subgrupos_actuales:
            detected_event = "CUOTA_EQUIPO"
            cargos_equipo = [c for c in cargos_actuales
                            if "financiamiento" in c.get("CHARGE_CODE_CLASSIFICATION", "").lower()
                            or c.get("SUB_GRUPO", "").upper() in ("FINANCIAMIENTO", "EQUIPOS")]
            monto_equipo = sum(c.get("CHARGE_TOTAL_AMOUNT", 0) for c in cargos_equipo)
            evidence.append(f"Se está cobrando una cuota de equipo financiado por {MONEDA_SIMBOLO} {monto_equipo:.2f}.")

        # 4. PRORRATEO — cargos proporcionales
        elif any("PROPORCIONAL" in g for g in grupos_actuales) \
                or any("PROPORCIONAL" in sg for sg in subgrupos_actuales):
            detected_event = "PRORRATEO_CAMBIO_PLAN"
            cargos_prop = [c for c in cargos_actuales
                          if "PROPORCIONAL" in c.get("GRUPO", "").upper()
                          or "PROPORCIONAL" in c.get("SUB_GRUPO", "").upper()]
            monto_prop = sum(c.get("CHARGE_TOTAL_AMOUNT", 0) for c in cargos_prop)
            evidence.append(f"Cobro proporcional por {MONEDA_SIMBOLO} {monto_prop:.2f} por cambio de plan a mitad de ciclo.")

        else:
            detected_event = "INCREMENTO_OTROS"
            evidence.append("Incremento en cargos fijos detectado.")

    elif delta_m < 0:
        detected_event = "REDUCCION_TARIFA"
        # Intentar detectar la causa de la reducción
        if any("descuento" in cl for cl in clasificaciones_actuales) \
                and not any("descuento" in cl for cl in clasificaciones_pasadas):
            evidence.append("Se aplicó un nuevo descuento o bonificación.")
        else:
            evidence.append("Reducción en los montos facturados.")

    return detected_event, evidence


def _descuento_desaparecido(cargos_actuales: List[Dict], cargos_pasados: List[Dict]) -> Optional[str]:
    """
    Detecta si un bono/descuento/bonificación que estaba en el periodo anterior
    ya no está presente en el actual. Señal de fin de promoción.
    Retorna la descripción del descuento perdido, o None.
    """
    _CLASIFICACIONES_DESCUENTO = {
        "bono recurrente negativo",
        "descuento cargo recurrente",
        "descuento fija",
        "cargo recurrente de plan neg",
    }

    # IDs de cargos de descuento en cada periodo
    ids_descuento_pasado = {
        c.get("CHARGE_CODE_ID") for c in cargos_pasados
        if c.get("CHARGE_CODE_CLASSIFICATION", "").lower() in _CLASIFICACIONES_DESCUENTO
        or c.get("SUB_GRUPO", "").upper() in ("DESCUENTO CARGO RECURRENTE", "DESCUENTO CARGO RECURRENTE - RV", "CHURN / FIDELIZACION")
    }
    ids_descuento_actual = {
        c.get("CHARGE_CODE_ID") for c in cargos_actuales
        if c.get("CHARGE_CODE_CLASSIFICATION", "").lower() in _CLASIFICACIONES_DESCUENTO
        or c.get("SUB_GRUPO", "").upper() in ("DESCUENTO CARGO RECURRENTE", "DESCUENTO CARGO RECURRENTE - RV", "CHURN / FIDELIZACION")
    }

    perdidos = ids_descuento_pasado - ids_descuento_actual
    if not perdidos:
        return None

    # Buscar las descripciones de los descuentos que desaparecieron
    descs = []
    for c in cargos_pasados:
        if c.get("CHARGE_CODE_ID") in perdidos:
            descs.append(c.get("CHARGE_CODE_DESC", "Descuento"))
    return ", ".join(descs[:3]) if descs else "Descuento/Bono anterior"


def _inferir_plan_desde_cargos(cargos: List[Dict]) -> Optional[str]:
    """
    Intenta inferir el nombre del plan desde los cargos con clasificación
    'Cargo Recurrente De Plan'. Si hay uno, su CHARGE_CODE_DESC es el plan.
    """
    for c in cargos:
        clasificacion = c.get("CHARGE_CODE_CLASSIFICATION", "").lower()
        if "cargo recurrente de plan" in clasificacion and "neg" not in clasificacion:
            desc = c.get("CHARGE_CODE_DESC", "")
            if desc:
                return desc
    return None


def _formatear_fecha_vencimiento(valor: str) -> Optional[str]:
    """Convierte 'YYYYMMDD' (formato de FECHA-VENCIMIENTO en el CSV) a 'YYYY-MM-DD'."""
    if not valor or not valor.isdigit() or len(valor) != 8:
        return None
    try:
        return datetime.strptime(valor, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _obtener_estado_deuda(conceptos_facturados: Optional[Dict], mes_emision: str = "") -> Optional[Dict[str, str]]:
    """
    Extrae el estado de deuda que viene explícitamente en el CSV.

    No convierte texto ambiguo en un monto ni asume que la ausencia del campo
    significa saldo cero. Solo expone el valor verificable y el período de la
    factura para poder responder consultas de deuda sin inventar información.

    PERIOD_END_DATE llega vacío/corrupto en el CSV fuente (literal "00:00.0"),
    así que se usa FECHA-VENCIMIENTO como referencia de período cuando es una
    fecha válida, y como último recurso el mes de emisión del recibo.
    """
    info_factura = (conceptos_facturados or {}).get("info_factura", {})
    valor = str(info_factura.get("DEUDA", "")).strip()
    if not valor or valor.lower() in {"nan", "none", "null"}:
        return None

    periodo = _formatear_fecha_vencimiento(str(info_factura.get("FECHA_VENCIMIENTO", "")).strip())
    if not periodo:
        periodo_raw = str(info_factura.get("PERIOD_END_DATE", "")).strip()
        periodo = periodo_raw if periodo_raw and not periodo_raw.lower().endswith(":00.0") else mes_emision

    sin_deuda = valor.upper() in {"SIN DEUDA", "NO TIENE DEUDA", "NO REGISTRA DEUDA"}
    return {
        "valor": valor,
        "estado": "SIN_DEUDA" if sin_deuda else "REPORTADA",
        "periodo": periodo or "",
    }


def calculate_billing_facts(user_id: str, db: Session) -> Dict[str, Any]:
    """
    Calcula variaciones (Delta M) y genera el Deterministic Fact Payload.
    Soporta tanto el formato de datos reales (CSV) como el formato legacy (mock).
    """
    recibos = crud.get_recibos_by_user(db, user_id, limit=HORIZONTE_RECIBOS)

    if not recibos:
        return {"error": "No hay recibos para este usuario."}

    current_bill = recibos[0]
    recibos_previos = recibos[1:]
    conceptos_actuales = current_bill.conceptos_facturados or {}

    # Estos hechos se extraen antes de evaluar el historial para que un cliente
    # con un único recibo también pueda consultar su plan o estado de deuda.
    plan_actual = current_bill.plan_actual
    if not plan_actual and not _es_formato_legacy(conceptos_actuales):
        plan_actual = _inferir_plan_desde_cargos(_extraer_cargos(conceptos_actuales))
    estado_deuda = _obtener_estado_deuda(conceptos_actuales, mes_emision=current_bill.mes_emision)

    if not recibos_previos:
        # Solo tiene un recibo, no hay historial con qué comparar.
        return {
            "moneda": MONEDA_CODIGO,
            "simbolo_moneda": MONEDA_SIMBOLO,
            "plan_actual": plan_actual,
            "estado_deuda": estado_deuda,
            "current_bill": {
                "amount": current_bill.monto_total,
                "issue_date": current_bill.mes_emision,
            },
            "previous_bills": [],
            "variation_amount": 0.0,
            "variation_percentage": 0.0,
            "detected_event": "NUEVO_CLIENTE",
            "evidence": ["Primer recibo emitido."],
            "upcoming_alerts": _calculate_upcoming_alerts(current_bill),
        }

    previous_bill = recibos_previos[0]
    delta_m = round(current_bill.monto_total - previous_bill.monto_total, 2)
    variation_pct = round((delta_m / previous_bill.monto_total) * 100, 2) if previous_bill.monto_total > 0 else 0

    # Detectar el evento causal: elegir lógica según el formato de datos
    conceptos_pasados = previous_bill.conceptos_facturados or {}

    if _es_formato_legacy(conceptos_actuales) or _es_formato_legacy(conceptos_pasados):
        # Formato plano legacy (generate_mock_data.py)
        detected_event, evidence = _detectar_evento_legacy(conceptos_actuales, conceptos_pasados, delta_m)
    else:
        # Formato real con lista de cargos (ingest_real_data.py)
        cargos_actuales = _extraer_cargos(conceptos_actuales)
        cargos_pasados = _extraer_cargos(conceptos_pasados)
        detected_event, evidence = _detectar_evento_real(cargos_actuales, cargos_pasados, delta_m)

    patron_recurrente = _detectar_patron_recurrente(detected_event, recibos_previos)
    if patron_recurrente:
        evidence.append(patron_recurrente)

    # Enriquecer con órdenes CRM relevantes para el evento detectado (solo datos reales).
    # Aporta evidencia de contexto: cuándo/por qué ocurrió una suspensión, cambio de plan, etc.
    ordenes_contexto = []
    _EVENTOS_CON_ORDENES = {"RECONEXION_MOROSIDAD", "PRORRATEO_CAMBIO_PLAN", "CUOTA_EQUIPO", "NUEVA_LINEA"}
    if detected_event in _EVENTOS_CON_ORDENES and not _es_formato_legacy(conceptos_actuales):
        customer_key = conceptos_actuales.get("info_factura", {}).get("CUSTOMER_KEY", "")
        if customer_key:
            ordenes_raw = crud.get_ordenes_por_customer_key(db, customer_key, limit=5)
            for o in ordenes_raw:
                ordenes_contexto.append({
                    "tipo": o.order_type,
                    "motivo": o.order_reason,
                    "fecha_inicio": o.start_date.isoformat() if o.start_date else None,
                    "fecha_fin": o.completion_date.isoformat() if o.completion_date else None,
                })

    return {
        "moneda": MONEDA_CODIGO,
        "simbolo_moneda": MONEDA_SIMBOLO,
        "plan_actual": plan_actual,
        "estado_deuda": estado_deuda,
        "current_bill": {
            "amount": current_bill.monto_total,
            "issue_date": current_bill.mes_emision,
        },
        "previous_bills": [
            {"month": r.mes_emision, "amount": r.monto_total}
            for r in recibos_previos
        ],
        "variation_amount": delta_m,
        "variation_percentage": variation_pct,
        "detected_event": detected_event,
        "evidence": evidence,
        "ordenes_contexto": ordenes_contexto,
        "upcoming_alerts": _calculate_upcoming_alerts(current_bill),
    }


def _detectar_patron_recurrente(detected_event: str, recibos_previos: List[Any]) -> Optional[str]:
    """
    Revisa la ventana completa de recibos previos (hasta 5 meses) para detectar
    si el mismo tipo de variación ya ocurrió antes. Un solo mes de comparación
    solo explica la causa puntual; esta señal explica el patrón ("esto ya pasó
    N veces"), que es justo lo que un historial de un solo mes no puede dar.

    Soporta tanto el formato legacy (dict plano) como el formato real (cargos list).
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

        if _es_formato_legacy(conceptos_a) or _es_formato_legacy(conceptos_b):
            # Lógica legacy: heurísticas de keys del dict plano
            if detected_event == "FIN_PROMOCION" and "descuento_promo" in conceptos_b and "descuento_promo" not in conceptos_a:
                ocurrencias += 1
            elif detected_event == "CUOTA_EQUIPO" and any("cuota_equipo" in str(k) for k in conceptos_a.keys()):
                ocurrencias += 1
            elif detected_event == "RECONEXION_MOROSIDAD" and any("reconex" in str(k) for k in conceptos_a.keys()):
                ocurrencias += 1
        else:
            # Lógica real: buscar señales equivalentes en la lista de cargos
            cargos_a = _extraer_cargos(conceptos_a)
            cargos_b = _extraer_cargos(conceptos_b)

            if detected_event == "FIN_PROMOCION" and _descuento_desaparecido(cargos_a, cargos_b):
                ocurrencias += 1
            elif detected_event == "CUOTA_EQUIPO":
                if any("financiamiento" in c.get("CHARGE_CODE_CLASSIFICATION", "").lower() for c in cargos_a) \
                        or any(c.get("SUB_GRUPO", "").upper() in ("FINANCIAMIENTO", "EQUIPOS") for c in cargos_a):
                    ocurrencias += 1
            elif detected_event == "RECONEXION_MOROSIDAD":
                if any("RECONEXION" in c.get("GRUPO", "").upper() or "RECONEXION" in c.get("SUB_GRUPO", "").upper() for c in cargos_a):
                    ocurrencias += 1
            elif detected_event == "PRORRATEO_CAMBIO_PLAN":
                if any("PROPORCIONAL" in c.get("GRUPO", "").upper() or "PROPORCIONAL" in c.get("SUB_GRUPO", "").upper() for c in cargos_a):
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
    no solo explicarlo después).

    Soporta dos formatos:
    - Legacy (mock): conceptos_facturados tiene clave "promo_activa" con
      {"descuento": X, "fecha_fin": "YYYY-MM-DD", "nombre_concepto": "..."}.
    - Real (CSV): se revisa la info_factura."FECHA_VENCIMIENTO" y los bonos/descuentos
      activos para alertar sobre vencimientos próximos.
    """
    conceptos = current_bill.conceptos_facturados or {}

    # --- Formato legacy (generate_mock_data.py) ---
    promo = conceptos.get("promo_activa")
    if isinstance(promo, dict) and "fecha_fin" in promo:
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

    # --- Formato real (ingest_real_data.py) ---
    # Revisar si hay bonos/descuentos activos y una fecha de vencimiento cercana.
    info_factura = conceptos.get("info_factura", {})
    fecha_vencimiento_str = info_factura.get("FECHA_VENCIMIENTO", "").strip()

    # Solo generamos alertas si hay bonos/descuentos activos en el periodo
    cargos = _extraer_cargos(conceptos)
    _CLASIFICACIONES_BONO = {
        "bono recurrente negativo",
        "descuento cargo recurrente",
        "descuento fija",
        "cargo recurrente de plan neg",
    }
    bonos_activos = [
        c for c in cargos
        if c.get("CHARGE_CODE_CLASSIFICATION", "").lower() in _CLASIFICACIONES_BONO
        or c.get("SUB_GRUPO", "").upper() in ("DESCUENTO CARGO RECURRENTE", "DESCUENTO CARGO RECURRENTE - RV", "CHURN / FIDELIZACION")
    ]

    if not bonos_activos:
        return []

    # Si tenemos una fecha de vencimiento de la factura, usarla como proxy de
    # "próximo ciclo". Alertamos si un bono podría no renovarse.
    # En datos reales sin campo explícito de fecha_fin de promo, no podemos
    # garantizar una alerta sin inventar datos. Retornamos [] si no hay certeza.
    # Esto se puede mejorar cuando exista un catálogo de ofertas con fechas.
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
        r"\b(no creo que un bot|dudo que un bot|eres un bot|seguro que un bot|no vas a entender|hablar con un humano|pasame con un humano|bot pueda)\b",
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
