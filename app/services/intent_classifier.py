import re
from typing import Tuple

# Clasificación de intención determinística (sin LLM).
# Retorna (intent, sub_intent) donde intent es uno de:
#   - "FACTURACION" → pasa al pipeline completo
#   - "SALUDO" → respuesta conversacional de bienvenida
#   - "DESPEDIDA" → respuesta de cierre
#   - "OFF_TOPIC" → redirección amable al tema de facturación
#   - "AGRADECIMIENTO" → respuesta breve + oferta de más ayuda

# Patrones ordenados por prioridad. Si el mensaje matchea facturación,
# SIEMPRE se prioriza sobre cualquier otro intent.

_BILLING_PATTERNS = [
    r"\b(recibo|factura|cobr\w*|pag\w*|monto|saldo|deuda)\b",
    r"\b(plan|megas?|mbps|fibra|internet|velocidad)\b",
    r"\b(promo|promoci[oó]n|descuento|oferta)\b",
    r"\b(sub[ií][oó]|aument[oó]|increment[oó]|baj[oó]|cambi[oó]|variaci[oó]n)\b",
    r"\b(cuota|equipo|financ|router|repetidor)\b",
    r"\b(corte|suspensi[oó]n|reconex|moroso)\b",
    r"\b(prorrat|proporcional)\b",
    r"\b(por\s*qu[eé]|explica|entiendo|no\s*entiendo|detalle)\b",
    r"\b(caro|barato|estafa|robo|abus[oa])\b",
    r"\b(mes pasado|mes anterior|este mes|julio|agosto|septiembre)\b",
    r"\bS/\s*\d+",
    r"\b\d+\s*soles\b",
]

_GREETING_PATTERNS = [
    r"^\s*(hola|hey|buenas?|buenos?\s*(d[ií]as?|tardes?|noches?)|hi|hello|qu[eé]\s*tal|saludos?)\s*[!.?]*\s*$",
    r"^\s*(hola|hey|buenas?\s*(tardes?|noches?|d[ií]as?)?)\s*[,!.]*\s*(lucia|luc[ií]a)?\s*[!.?]*\s*$",
]

_FAREWELL_PATTERNS = [
    r"^\s*(adi[oó]s|chao|chau|bye|hasta\s*luego|nos\s*vemos|gracias\s*adi[oó]s)\s*[,!.?]*\s*(lucia|luc[ií]a)?\s*[!.?]*\s*$",
]

_THANKS_PATTERNS = [
    r"^\s*(gracias|muchas\s*gracias|te\s*agradezco|genial|perfecto|excelente|ok\s*gracias)\s*[!.?]*\s*$",
]


def classify_intent(message: str) -> Tuple[str, str]:
    """
    Clasifica la intención del mensaje de forma determinística.
    Retorna (intent, sub_intent).
    
    Prioridad:
    1. Si contiene señales de facturación → FACTURACION (siempre gana)
    2. Si es saludo puro → SALUDO
    3. Si es despedida pura → DESPEDIDA
    4. Si es agradecimiento puro → AGRADECIMIENTO
    5. Todo lo demás → OFF_TOPIC
    """
    msg = message.strip()
    msg_lower = msg.lower()

    # 1. Facturación siempre tiene prioridad
    for pattern in _BILLING_PATTERNS:
        if re.search(pattern, msg_lower):
            return ("FACTURACION", "CONSULTA")

    # 2. Saludo puro (mensajes cortos sin contenido de facturación)
    for pattern in _GREETING_PATTERNS:
        if re.search(pattern, msg_lower):
            return ("SALUDO", "INICIAL")

    # 3. Despedida
    for pattern in _FAREWELL_PATTERNS:
        if re.search(pattern, msg_lower):
            return ("DESPEDIDA", "CIERRE")

    # 4. Agradecimiento
    for pattern in _THANKS_PATTERNS:
        if re.search(pattern, msg_lower):
            return ("AGRADECIMIENTO", "POSITIVO")

    # 5. Mensajes muy cortos (< 5 palabras) sin señales de facturación → OFF_TOPIC
    #    Mensajes largos sin señales → igual OFF_TOPIC pero podrían ser consultas ambiguas
    #    Para no perder consultas legítimas mal formuladas, solo clasificamos como OFF_TOPIC
    #    mensajes que claramente no tienen relación con facturación.
    word_count = len(msg_lower.split())
    if word_count <= 6:
        return ("OFF_TOPIC", "CONVERSACIONAL")

    # Mensajes más largos sin keywords de facturación: tratarlos como OFF_TOPIC
    # pero con sub_intent AMBIGUO para que la respuesta ofrezca ayuda
    return ("OFF_TOPIC", "AMBIGUO")


def get_conversational_response(intent: str, sub_intent: str) -> str:
    """
    Genera una respuesta conversacional apropiada para intents no-facturación.
    """
    responses = {
        ("SALUDO", "INICIAL"): (
            "¡Hola! 😊 Soy Lucía, tu asistente de facturación. "
            "Puedo ayudarte a entender tu recibo, explicarte cambios en tus montos "
            "o resolver dudas sobre tu plan. ¿En qué te puedo ayudar?"
        ),
        ("DESPEDIDA", "CIERRE"): (
            "¡Hasta pronto! Si en el futuro tienes alguna duda sobre tu recibo, "
            "aquí estaré para ayudarte. 👋"
        ),
        ("AGRADECIMIENTO", "POSITIVO"): (
            "¡De nada! Me alegra haberte ayudado. "
            "Si tienes otra consulta sobre tu recibo o plan, no dudes en escribirme. 😊"
        ),
        ("OFF_TOPIC", "CONVERSACIONAL"): (
            "¡Estoy aquí para ayudarte! 😊 Mi especialidad es explicarte todo lo relacionado "
            "con tu recibo y tu plan. ¿Tienes alguna consulta sobre tu facturación?"
        ),
        ("OFF_TOPIC", "AMBIGUO"): (
            "Mmm, no estoy segura de entender tu consulta. "
            "Puedo ayudarte con temas de facturación: explicarte por qué subió tu recibo, "
            "detallar los conceptos cobrados, o revisar tu plan actual. "
            "¿Hay algo de eso en lo que pueda asistirte?"
        ),
    }

    return responses.get((intent, sub_intent), responses[("OFF_TOPIC", "CONVERSACIONAL")])
