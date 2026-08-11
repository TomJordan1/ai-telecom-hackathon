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
# Solo términos que difícilmente aparecen fuera de una consulta de facturación.
# Deliberadamente NO se incluyen expresiones ambiguas ("por qué", "caro", "no
# entiendo"): esas las resuelve el LLM con contexto.

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
]

# ---------------------------------------------------------------------------
# Solicitudes estructurales sensibles (cancelación, portabilidad, etc.)
# ---------------------------------------------------------------------------
# Estas NO son variaciones de recibo: el motor determinista no puede detectarlas
# (no hay ningún cálculo de ΔM que explique "quiero cancelar mi plan") y el RAG
# de políticas de facturación tampoco las cubre. Si se dejaran caer al pipeline
# de facturación, el LLM terminaría improvisando el proceso con su conocimiento
# general — justo la alucinación que el resto del diseño evita para montos y
# fechas, pero sin blindaje para procesos.
#
# Se evalúan con la MÁXIMA prioridad (antes que facturación y que el handoff
# genérico) para que cada patrón quede etiquetado con su propia categoría y
# pueda entrar al mismo ciclo de aprendizaje supervisado (cuarentena -> agente
# valida -> base_casos -> reutilización) que ya existe para eventos de
# facturación. Sin este paso, "cancelar mi plan" contiene la palabra "plan" y
# quedaría atrapado explicando la variación del recibo en vez de derivar.

_SOLICITUD_SENSIBLE_PATRONES: Dict[str, List[str]] = {
    "CANCELACION_PLAN": [
        r"\bcancel(ar|o|aci[oó]n)\b",
        r"\bdar\s+de\s+baja\b",
        r"\bbaja\s+(del|de\s+mi)?\s*(plan|servicio|l[ií]nea)\b",
        r"\bdesactivar\s+(mi\s+)?(plan|l[ií]nea|servicio)\b",
        r"\bdejar\s+(el\s+)?servicio\b",
        r"\bterminar\s+(mi\s+)?contrato\b",
    ],
    "PORTABILIDAD": [
        r"\bportabilidad\b",
        r"\bportar\s+(mi\s+)?(n[uú]mero|l[ií]nea)\b",
        r"\bcambiar(me)?\s+de\s+operador\b",
        r"\bmigrar\s+a\s+otra\s+(compa[ñn][ií]a|operadora)\b",
    ],
}


def detectar_solicitud_sensible(message: str) -> Optional[str]:
    """
    ¿El mensaje pide una acción estructural que el motor determinista no puede
    resolver (cancelación, portabilidad)? Retorna el patrón detectado o None.
    """
    texto = message.lower()
    for patron, patrones_regex in _SOLICITUD_SENSIBLE_PATRONES.items():
        if any(re.search(p, texto) for p in patrones_regex):
            return patron
    return None


# ---------------------------------------------------------------------------
# Solicitud explícita de agente humano (máxima prioridad)
# ---------------------------------------------------------------------------
# Es una señal estructural, no de contenido: independientemente del tema, si el
# usuario pide hablar con una persona hay que honrarlo. Por eso se evalúa ANTES
# que las señales de facturación (evita que "pásame con un asesor sobre mi plan"
# se quede atrapado en el pipeline de facturación).

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
# Pistas léxicas (SOLO una heurística de apoyo)
# ---------------------------------------------------------------------------
# Esta lista no pretende ser exhaustiva ni autoritativa: el LLM es quien decide
# el perfil léxico. Sirve para dos casos concretos donde no hay llamada al modelo:
#   1. El camino rápido de facturación.
#   2. El modo degradado sin LLM.

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

# Mensaje único de modo degradado (sin LLM disponible).
# No es un catálogo de respuestas: es el salvavidas para que el servicio no caiga.
_FALLBACK_CONVERSACIONAL = (
    "¡Hola! Soy Lucía y te ayudo con todo lo relacionado a tu recibo y tu plan. "
    "¿Qué te gustaría revisar?"
)


@dataclass
class RoutingDecision:
    """Resultado del enrutamiento de un turno."""
    intent: str                      # FACTURACION | SOLICITUD_AGENTE | SOLICITUD_SENSIBLE | SALUDO | DESPEDIDA | AGRADECIMIENTO | FUERA_DE_DOMINIO
    perfil_lexico: str               # FORMAL | CASUAL | USO_JERGAS
    respuesta: Optional[str] = None  # Texto ya redactado (solo turnos no-facturación)
    fuente: str = "DETERMINISTA"     # LLM | DETERMINISTA — para observabilidad
    patron_sensible: Optional[str] = None  # CANCELACION_PLAN | PORTABILIDAD — clave para cuarentena/base_casos

    @property
    def es_facturacion(self) -> bool:
        return self.intent == "FACTURACION"

    @property
    def es_solicitud_agente(self) -> bool:
        return self.intent == "SOLICITUD_AGENTE"

    @property
    def es_solicitud_sensible(self) -> bool:
        return self.intent == "SOLICITUD_SENSIBLE"


def has_billing_signals(message: str) -> bool:
    """¿El mensaje contiene vocabulario inequívoco de facturación?"""
    texto = message.lower()
    return any(re.search(p, texto) for p in _BILLING_PATTERNS)


def has_handoff_signals(message: str) -> bool:
    """¿El usuario está pidiendo explícitamente hablar con un agente humano?"""
    texto = message.lower()
    return any(re.search(p, texto) for p in _HANDOFF_PATTERNS)


def detectar_perfil_lexico_heuristico(message: str, perfil_previo: Optional[str] = None) -> str:
    """
    Estimación de registro sin llamar al modelo. Heurística de apoyo:
    se usa en el camino rápido de facturación y en modo degradado.
    """
    texto = message.lower()

    if any(re.search(p, texto) for p in _JERGA_MARKERS):
        return persona.PERFIL_JERGAS
    if any(re.search(p, texto) for p in _FORMAL_MARKERS):
        return persona.PERFIL_FORMAL

    # Sin señales claras: se conserva el registro ya observado en la sesión.
    if perfil_previo:
        return persona.normalizar_perfil(perfil_previo)
    return persona.PERFIL_POR_DEFECTO


def route(
    message: str,
    perfil_previo: Optional[str] = None,
    pending_emotions: Optional[List[Dict]] = None,
    historial_conversacion: Optional[List[Dict]] = None,
) -> RoutingDecision:
    """
    Decide si el turno va al pipeline de facturación o se resuelve conversacionalmente.

    Para turnos conversacionales la respuesta ya viene redactada por el LLM, con la
    personalidad de Lucía y adaptada al registro del usuario.
    """
    # 1. Máxima prioridad absoluta: solicitudes estructurales sensibles
    #    (cancelación, portabilidad). Van ANTES que el handoff genérico y que
    #    facturación: "cancelar mi plan" contiene "plan" y quedaría atrapado
    #    explicando la variación del recibo si no se detecta aquí primero.
    patron_sensible = detectar_solicitud_sensible(message)
    if patron_sensible:
        return RoutingDecision(
            intent="SOLICITUD_SENSIBLE",
            perfil_lexico=detectar_perfil_lexico_heuristico(message, perfil_previo),
            fuente="DETERMINISTA",
            patron_sensible=patron_sensible,
        )

    # 2. Solicitud explícita de agente humano.
    #    Va antes que facturación: "pásame con un asesor sobre mi plan" no debe
    #    quedar atrapado explicando el recibo otra vez.
    if has_handoff_signals(message):
        return RoutingDecision(
            intent="SOLICITUD_AGENTE",
            perfil_lexico=detectar_perfil_lexico_heuristico(message, perfil_previo),
            fuente="DETERMINISTA",
        )

    # 3. Camino rápido: señales claras de facturación → sin llamada extra al modelo.
    if has_billing_signals(message):
        return RoutingDecision(
            intent="FACTURACION",
            perfil_lexico=detectar_perfil_lexico_heuristico(message, perfil_previo),
            fuente="DETERMINISTA",
        )

    # 4. Sin señales claras (o mensaje corto ambiguo): decide el LLM, que ve el
    #    historial reciente y entiende jerga, ironía y contexto.
    resultado = llm_service.classify_and_reply(
        user_message=message,
        perfil_previo=perfil_previo,
        pending_emotions=pending_emotions,
        historial_conversacion=historial_conversacion,
    )

    if resultado:
        intent = resultado["intent"]
        perfil = resultado["perfil_lexico"]

        # El LLM detectó una consulta de facturación expresada sin vocabulario técnico
        # (p. ej. jerga: "oe, mi luz salió salada este mes").
        if intent in ("FACTURACION", "SOLICITUD_AGENTE"):
            return RoutingDecision(intent=intent, perfil_lexico=perfil, fuente="LLM")

        respuesta = resultado.get("respuesta") or _FALLBACK_CONVERSACIONAL
        return RoutingDecision(
            intent=intent,
            perfil_lexico=perfil,
            respuesta=respuesta,
            fuente="LLM",
        )

    # 3. Modo degradado (sin LLM). Ante la duda, tratar como facturación:
    #    el índice de incertidumbre derivará a un humano si no hay datos suficientes,
    #    lo cual es preferible a ignorar una consulta real.
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
    """
    Heurística mínima de modo degradado: mensajes muy cortos con raíces sociales
    evidentes. Solo se usa cuando el LLM no está disponible.
    """
    texto = message.strip().lower()
    if len(texto.split()) > 8:
        return False
    return bool(re.search(
        r"\b(hola|hey|buenas|buenos|hi|hello|saludos|gracias|adi[oó]s|chao|chau|bye|"
        r"hasta luego|nos vemos|ok|vale|listo|acuerdas|recuerdas|eres|bot|ia|quien|"
        r"quién|c[oó]mo|tal|que tal|q tal|ayuda|dime|sabes|puedes)\b",
        texto,
    ))
