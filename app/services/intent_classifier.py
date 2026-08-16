"""
Enrutamiento de intención.

Reparto de responsabilidades:

- DETERMINISTA (regex): detectar señales inequívocas de facturación. El vocabulario
  financiero es finito y predecible, así que aquí el regex es fiable y además evita
  una llamada al modelo en el camino caliente.

- LLM: todo lo demás. Es imposible enumerar todas las formas en que alguien puede
  saludar, bromear, quejarse o usar jerga peruana, así que no se intenta. El modelo
  entiende el lenguaje —que es justo su trabajo— y redacta la respuesta.

Lo que el LLM NO hace aquí: calcular montos, decidir qué ocurrió en el recibo ni
validar soluciones. Eso sigue siendo exclusivamente determinista.

Red de seguridad: si el LLM no está disponible o falla, ante ambigüedad se asume
FACTURACION. El índice de incertidumbre y el handoff a humano ya cubren ese caso,
así que nunca se descarta una consulta legítima.
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from app.services import llm as llm_service
from app.services import persona

# ---------------------------------------------------------------------------
# Señales deterministas de facturación (alta precisión)
# ---------------------------------------------------------------------------
_BILLING_PATTERNS = [
    r"\b(recibo|recibos|factura|facturas|facturaci[oó]n|boleta)\b",
    r"\bcobr\w*",
    r"\b(monto|saldo|deuda|tarifa|cargo|cargos|consumo)\b",
    r"\bpag(o|os|ar|u[eé]|amos|aste)\b",
    r"\b(plan|planes|megas?|mbps|fibra|internet|velocidad)\b",
    r"\b(promo|promoci[oó]n|descuento|oferta)\b",
    r"\b(cuota|cuotas|financ\w*|router|repetidor|equipo)\b",
    r"\b(prorrat\w*|proporcional)\b",
    r"\b(suspensi[oó]n|reconex\w*|moroso|cortaron|corte del servicio)\b",
    r"\bS/\s*\d+",
    r"\b\d+\s*soles\b",
    # --- Categorías adicionales ---
    r"\bnota\s+de\s+(cr[eé]dito|d[eé]bito)\b",
    r"\b(NC|ND)\b",
    r"\bcargo\s+(fijo|recurrente|[uú]nico)\b",
    r"\bpaquete\b",
    r"\bajuste\s+(por|de)\s+(suspensi[oó]n|d[ií]as)\b",
    r"\brenta\s+(adelantada|vencida)\b",
    r"\bvencimient\w*\b",
]

# ---------------------------------------------------------------------------
# Solicitudes estructurales sensibles (bajas: cancelación, portabilidad;
# altas: nueva línea, nuevo servicio)
# ---------------------------------------------------------------------------
_SOLICITUD_SENSIBLE_PATRONES: Dict[str, List[str]] = {
    "CANCELACION_PLAN": [
        r"\bcancel(ar|o|aci[oó]n)\b",
        r"\bdar\s+de\s+baja\b",
        r"\bbaja\s+(del|de\s+mi)?\s*(plan|servicio|l[ií]nea)\b",
        r"\bdesactivar\s+(mi\s+)?(plan|l[ií]nea|servicio)\b",
        r"\bdejar\s+(el\s+)?servicio\b",
        r"\bterminar\s+(mi\s+)?contrato\b",
    ],
    "CAMBIO_PLAN": [
        r"\bcambiar(me)?\s+(de|mi|el)\s+plan\b",
        r"\bmigrar(me)?\s+(de|a\s+otro)\s+plan\b",
        r"\bquiero\s+otro\s+plan\b",
        r"\bpasar(me)?\s+a\s+otro\s+plan\b",
        r"\bcambio\s+de\s+plan\b",
    ],
    "PORTABILIDAD": [
        r"\bportabilidad\b",
        r"\bportar\s+(mi\s+)?(n[uú]mero|l[ií]nea)\b",
        r"\bcambiar(me)?\s+de\s+operador\b",
        r"\bmigrar\s+a\s+otra\s+(compa[ñn][ií]a|operadora)\b",
    ],
    "NUEVA_LINEA": [
        r"\babrir\s+(otra|una|otra\s+m[aá]s|nueva)?\s*l[ií]nea\b",
        r"\b(adquirir|contratar|sacar|activar)\s+(otra|una)?\s*l[ií]nea\s+(nueva|adicional)?\b",
        r"\bl[ií]nea\s+(nueva|adicional)\b",
        r"\bnuevo\s+plan\s+adicional\b",
        r"\bcontratar\s+(un\s+)?nuevo\s+servicio\b",
        r"\bagregar\s+una\s+l[ií]nea\b",
    ],
}

PATRONES_SENSIBLES = frozenset(_SOLICITUD_SENSIBLE_PATRONES.keys())


def detectar_solicitud_sensible(message: str) -> Optional[str]:
    texto = message.lower()
    for patron, patrones_regex in _SOLICITUD_SENSIBLE_PATRONES.items():
        if any(re.search(p, texto) for p in patrones_regex):
            return patron
    return None


# ---------------------------------------------------------------------------
# Solicitud explícita de agente humano
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Solicitud explícita y palabras clave exactas de escalamiento directo
# ---------------------------------------------------------------------------
_EXACT_ESCALATION_REGEX = re.compile(
    r"^(0|asesor|asesora|humano|humana|agente|operador|operadora|ayuda|hablar con alguien|persona real|atencion humana)$",
    re.IGNORECASE
)

_CONSULTA_CASO_REGEX = re.compile(
    r"\b(c[oó]mo\s+va\s+mi\s+caso|estado\s+(de\s+mi\s+)?caso|qu[eé]\s+pas[oó]\s+con\s+mi\s+caso|revisar\s+mi\s+caso|seguimiento\s+(de\s+)?caso|mi\s+folio|CASO-[A-Z0-9]{4,8})\b",
    re.IGNORECASE
)

_HANDOFF_PATTERNS = [
    r"\b(asesor|agente|representante)(a|es)?\b",
    r"\bpersona\s+(real|humana)\b",
    r"\bhablar\s+con\s+(alguien|un\s+humano|una\s+persona)\b",
    r"\b(comun[ií]cam?e|transfi[ée]r[ée]m?e|pas[aá]m?e|deriv[aá]m?e)\s+(con|a)\b",
    r"\bhumano\b",
    r"\batenci[oó]n\s+humana\b",
    r"\bquiero\s+hablar\s+con\s+alguien\b",
]

# ---------------------------------------------------------------------------
# Pistas léxicas (sólo heurística de apoyo)
# ---------------------------------------------------------------------------
_JERGA_MARKERS = [
    r"\b(pe|pue|causa|compadre|choch(era|o)|manyas?|chibol[oa]|flaco)\b",
    r"\b(bravazo|paja|chevere|ch[eé]vere|yapa|al toque|chamba|luca|plata)\b",
    r"\b(misio|salad[oa]|roche|ya fue|habla|oe|oye pe|asu|caserit[oa])\b",
    r"\b(ntp|xq|pq|tqm|bcn|q tal|ta bien|nada q ver)\b",
    r"\b(huevad|cojud|jodid)\w*",
]

_FORMAL_MARKERS = [
    r"\b(usted|ustedes|estimad[oa]s?|cordialmente|atentamente|agradecer[eé]|agradecer[ií]a)\b",
    r"\b(quisiera|desear[ií]a|solicito|le agradezco|por favor tenga|s[ií]rvase)\b",
    r"\b(buenos d[ií]as|buenas tardes|buenas noches)\b",
]

_FALLBACK_CONVERSACIONAL = (
    "¡Hola! Soy Lucía y te ayudo con todo lo relacionado a tu recibo y tu plan. "
    "¿Qué te gustaría revisar?"
)


@dataclass
class RoutingDecision:
    intent: str
    perfil_lexico: str
    respuesta: Optional[str] = None
    fuente: str = "DETERMINISTA"
    patron_sensible: Optional[str] = None

    @property
    def es_facturacion(self) -> bool:
        return self.intent == "FACTURACION"

    @property
    def es_solicitud_agente(self) -> bool:
        return self.intent == "SOLICITUD_AGENTE"

    @property
    def es_solicitud_sensible(self) -> bool:
        return self.intent == "SOLICITUD_SENSIBLE"

    @property
    def es_consulta_caso(self) -> bool:
        return self.intent == "CONSULTA_ESTADO_CASO"


def has_billing_signals(message: str) -> bool:
    texto = message.lower()
    return any(re.search(p, texto) for p in _BILLING_PATTERNS)


def has_handoff_signals(message: str) -> bool:
    texto = message.lower().strip()
    if _EXACT_ESCALATION_REGEX.match(texto):
        return True
    return any(re.search(p, texto) for p in _HANDOFF_PATTERNS)


def has_case_status_signals(message: str) -> bool:
    return bool(_CONSULTA_CASO_REGEX.search(message.strip()))


def detectar_perfil_lexico_heuristico(message: str, perfil_previo: Optional[str] = None) -> str:
    texto = message.lower()
    if any(re.search(p, texto) for p in _JERGA_MARKERS):
        return persona.PERFIL_JERGAS
    if any(re.search(p, texto) for p in _FORMAL_MARKERS):
        return persona.PERFIL_FORMAL
    if perfil_previo:
        return persona.normalizar_perfil(perfil_previo)
    return persona.PERFIL_POR_DEFECTO


def route(
    message: str,
    perfil_previo: Optional[str] = None,
    pending_emotions: Optional[List[Dict]] = None,
    historial_conversacion: Optional[List[Dict]] = None,
) -> RoutingDecision:
    msg_clean = message.strip()

    # 1. Salida de escalamiento garantizada inmediata (sin pasar por LLM)
    if _EXACT_ESCALATION_REGEX.match(msg_clean) or has_handoff_signals(msg_clean):
        return RoutingDecision(
            intent="SOLICITUD_AGENTE",
            perfil_lexico=detectar_perfil_lexico_heuristico(msg_clean, perfil_previo),
            fuente="DETERMINISTA",
        )

    # 2. Consulta de estado de caso por folio o pregunta de seguimiento
    if has_case_status_signals(msg_clean):
        return RoutingDecision(
            intent="CONSULTA_ESTADO_CASO",
            perfil_lexico=detectar_perfil_lexico_heuristico(msg_clean, perfil_previo),
            fuente="DETERMINISTA",
        )

    patron_sensible = detectar_solicitud_sensible(msg_clean)
    if patron_sensible:
        return RoutingDecision(
            intent="SOLICITUD_SENSIBLE",
            perfil_lexico=detectar_perfil_lexico_heuristico(msg_clean, perfil_previo),
            fuente="DETERMINISTA",
            patron_sensible=patron_sensible,
        )

    if has_billing_signals(msg_clean):
        return RoutingDecision(
            intent="FACTURACION",
            perfil_lexico=detectar_perfil_lexico_heuristico(msg_clean, perfil_previo),
            fuente="DETERMINISTA",
        )

    resultado = llm_service.classify_and_reply(
        user_message=message,
        perfil_previo=perfil_previo,
        pending_emotions=pending_emotions,
        historial_conversacion=historial_conversacion,
    )

    if resultado:
        intent = resultado["intent"]
        perfil = resultado["perfil_lexico"]
        if intent in ("FACTURACION", "SOLICITUD_AGENTE"):
            return RoutingDecision(intent=intent, perfil_lexico=perfil, fuente="LLM")
        respuesta = resultado.get("respuesta") or _FALLBACK_CONVERSACIONAL
        return RoutingDecision(
            intent=intent,
            perfil_lexico=perfil,
            respuesta=respuesta,
            fuente="LLM",
        )

    perfil = detectar_perfil_lexico_heuristico(message, perfil_previo)
    if _parece_social_simple(message):
        return RoutingDecision(
            intent="FUERA_DE_DOMINIO",
            perfil_lexico=perfil,
            respuesta=_FALLBACK_CONVERSACIONAL,
            fuente="DETERMINISTA",
        )

    return RoutingDecision(intent="FACTURACION", perfil_lexico=perfil, fuente="DETERMINISTA")


def _parece_social_simple(message: str) -> bool:
    texto = message.strip().lower()
    if len(texto.split()) > 8:
        return False
    return bool(re.search(
        r"\b(hola|hey|buenas|buenos|hi|hello|saludos|gracias|adi[oó]s|chao|chau|bye|"
        r"hasta luego|nos vemos|ok|vale|listo|acuerdas|recuerdas|eres|bot|ia|quien|"
        r"quién|c[oó]mo|tal|que tal|q tal|ayuda|dime|sabes|puedes)\b",
        texto,
    ))