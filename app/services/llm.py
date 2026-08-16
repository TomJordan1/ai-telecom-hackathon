import json
import re
from typing import Dict, Any, List, Optional
from app.core.schemas import (
    ChatResponse,
    MessageChunk,
    PlanOptimizerSuggestion,
    RecommendedPlan,
    BillSummary,
    PersonalityMetadata,
    UpcomingAlert,
)
from app.core.config import settings
from app.services import persona

# Attempt to import LangChain; will be used if API Key is present
try:
    from langchain_openai import ChatOpenAI
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import PydanticOutputParser
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False


def llm_is_available() -> bool:
    """¿Hay un LLM real configurado y utilizable?"""
    return bool(settings.DEEPSEEK_API_KEY) and not settings.USE_MOCK_LLM and LANGCHAIN_AVAILABLE


def _build_llm(max_tokens: int = 1000, temperature: float = 0.3):
    """Construye el cliente de DeepSeek vía la interfaz compatible con OpenAI."""
    return ChatOpenAI(
        model="deepseek-chat",
        api_key=settings.DEEPSEEK_API_KEY,
        base_url="https://api.deepseek.com",
        max_tokens=max_tokens,
        temperature=temperature,
    )


def _formatear_historial(turnos: Optional[List[Dict[str, Any]]], max_turnos: int = 8) -> str:
    """
    Convierte la bitácora acotada de la sesión en texto legible para el prompt.

    Sin esto el modelo no tiene forma de saber qué ya se dijo en la sesión y
    termina re-explicando lo mismo turno tras turno.
    """
    if not turnos:
        return "Sin turnos previos: es el inicio de la conversación."

    recientes = turnos[-max_turnos:]
    lineas = []
    for t in recientes:
        rol = "Usuario" if t.get("role") == "user" else "Lucía"
        texto = (t.get("text") or "").strip()
        if not texto:
            continue
        etiqueta = f" [{t['intent']}]" if t.get("intent") else ""
        lineas.append(f"{rol}{etiqueta}: {texto}")
    return "\n".join(lineas) if lineas else "Sin turnos previos: es el inicio de la conversación."


def _sanitizar_message_chunks(messages: List[MessageChunk]) -> List[MessageChunk]:
    """
    Garantiza que ningún texto conversacional, despedida o conclusión sea
    etiquetado indebidamente como 'evidence'. El tipo 'evidence' queda
    estrictamente reservado para desgloses de cifras, montos o conceptos de pago.
    """
    for msg in messages:
        if msg.type == "evidence":
            texto_lower = msg.text.lower().strip()

            # Frases conversacionales de cierre, tranquilidad o despedida
            es_cierre_conversacional = any(
                p in texto_lower
                for p in [
                    "así que", "asi que", "tranqui", "aquí estoy", "aqui estoy",
                    "si necesitas", "cualquier duda", "cualquier consulta",
                    "estoy para ayudarte", "espero haberte", "que tengas",
                    "no dudes en", "un gusto", "buen día", "buen dia",
                    "nada más que pagar", "nada mas que pagar",
                    "todo está al día", "todo esta al dia",
                    "no te preocupes", "cuenta con nosotros"
                ]
            )

            # Debe contener cifras o conceptos de facturación reales
            tiene_cifras_o_desglose = bool(
                re.search(r"S/\.?\s*\d+|\b\d+(?:[\.,]\d{2})?\b", msg.text)
                or any(k in texto_lower for k in [
                    "cargo", "descuento", "cuota", "prorrateo", "tráfico adicional",
                    "recibo actual", "total a pagar", "saldo"
                ])
            )

            if es_cierre_conversacional or not tiene_cifras_o_desglose:
                msg.type = "explanation"

    return messages


def _generate_mock_response(
    session_id: str, 
    user_message: str, 
    deterministic_payload: Dict[str, Any], 
    rag_context: str,
    cross_sell_eligible: bool,
    pending_emotions: List[Dict],
    perfil_lexico: Optional[str] = None,
    historial_conversacion: Optional[List[Dict[str, Any]]] = None,
    recommended_plan: Optional[Dict[str, Any]] = None,
    pending_issue_followup: bool = False,
) -> ChatResponse:
    """Fallback if no API key is provided."""
    intent_category = deterministic_payload.get("detected_event", "CONSULTA_GENERAL")
    if "error" in deterministic_payload:
        intent_category = "NUEVO_CLIENTE"
    
    messages = []
    
    # 1. Seguimiento de pendientes (Follow-up)
    if pending_issue_followup:
        messages.append(MessageChunk(text="Antes de revisar lo de hoy, vi que tu consulta anterior quedó pendiente. ¿Lograron solucionarlo?", type="hook", delay_ms=0))
    # 2. Alertas proactivas
    upcoming_alerts_list = deterministic_payload.get("upcoming_alerts") or []
    if upcoming_alerts_list and not pending_issue_followup:
        alert = upcoming_alerts_list[0]
        messages.append(MessageChunk(text=f"Por cierto, noté que tu beneficio \"{alert.get('concepto')}\" ya llegó a su último ciclo facturado. ¡Avisado estás! Ahora, sobre tu consulta...", type="hook", delay_ms=0))
    elif pending_emotions and not pending_issue_followup:
        # 3. Empatía por dudar de IA (ejemplo)
        # Check if it's an AI doubt emotion
        em_text = " ".join([e.get("text", "") for e in pending_emotions])
        if "bot" in em_text or "humano" in em_text:
            messages.append(MessageChunk(text="Entiendo tus dudas sobre hablar con un asistente virtual, pero te aseguro que tengo acceso directo a tu facturación para ayudarte con precisión.", type="hook", delay_ms=0))
        else:
            messages.append(MessageChunk(text="Por cierto, entiendo tu preocupación anterior y estoy aquí para aclarar todo detalle 😊.", type="hook", delay_ms=0))
    elif not pending_issue_followup and not upcoming_alerts_list:
        tiene_historial = bool(historial_conversacion and len(historial_conversacion) > 0)
        if tiene_historial:
            messages.append(MessageChunk(text="Bien, déjame revisar tu estado de cuenta al detalle...", type="hook", delay_ms=0))
        else:
            messages.append(MessageChunk(text="Hola. Soy Lucía. He analizado tus recibos al detalle para explicarte qué pasó.", type="hook", delay_ms=0))

            
    delta = deterministic_payload.get('variation_amount', 0)
    evidence_list = deterministic_payload.get('evidence', [])
    evidence_str = ', '.join(evidence_list) if evidence_list else "Sin cambios."
    
    if delta > 0:
        messages.append(MessageChunk(text=f"Tu último recibo subió S/ {delta}. El motivo es: {evidence_str}.", type="explanation", delay_ms=1000))
    elif delta < 0:
        messages.append(MessageChunk(text=f"Tu recibo bajó S/ {abs(delta)}. {evidence_str}", type="explanation", delay_ms=1000))
    else:
        messages.append(MessageChunk(text="Tu recibo se mantiene igual al mes anterior. ¡Todo en orden!", type="explanation"))
            
    if "error" not in deterministic_payload:
        messages.append(MessageChunk(text=f"Recibo actual ({deterministic_payload['current_bill']['issue_date']}): S/ {deterministic_payload['current_bill']['amount']}", type="evidence", delay_ms=1500))

    # El plan recomendado SIEMPRE viene verificado del catálogo (recommend_plan_upgrade).
    # Si no hay un candidato real, no se ofrece nada, aunque cross_sell_eligible sea True.
    suggestion = PlanOptimizerSuggestion()
    if cross_sell_eligible and recommended_plan:
        suggestion = PlanOptimizerSuggestion(
            available=True,
            mensaje_comercial=f"Dato curioso: Lucía encontró el plan {recommended_plan['nombre']} que podría convenirte más. ¿Te ayudo a activarlo?",
            plan_recomendado=RecommendedPlan(**recommended_plan)
        )
    elif not cross_sell_eligible and intent_category != "BLOQUEO_COMPLIANCE":
        # Efecto efervescente en MOCK: solo si conocemos el plan del usuario
        plan_actual = deterministic_payload.get('plan_actual')
        if plan_actual:
            messages.append(MessageChunk(text=f"Recuerda que con {plan_actual} tienes grandes beneficios para seguir disfrutando. ¡Cualquier otra duda, aquí estoy!", type="explanation", delay_ms=1000))
        else:
            messages.append(MessageChunk(text="¡Cualquier otra duda sobre tu recibo, aquí estoy para ayudarte!", type="explanation", delay_ms=1000))
        
    historial = [
        BillSummary(month=pb['month'], amount=pb['amount'], ciclo=pb.get('ciclo'))
        for pb in deterministic_payload.get('previous_bills', [])
    ]

    upcoming_alerts = [UpcomingAlert(**a) for a in upcoming_alerts_list]

    return ChatResponse(
        session_id=session_id,
        intent_category=intent_category,
        sentiment_score=3,
        messages=_sanitizar_message_chunks(messages),
        historical_bills_summary=historial,
        upcoming_alerts=upcoming_alerts,
        plan_optimizer_suggestion=suggestion,
        personality_metadata=PersonalityMetadata(
            lucia_tone=persona.tono_para_metadata(perfil_lexico)
        )
    )


def generate_response(
    session_id: str, 
    user_message: str, 
    deterministic_payload: Dict[str, Any], 
    rag_context: str,
    cross_sell_eligible: bool,
    pending_emotions: List[Dict],
    perfil_lexico: Optional[str] = None,
    historial_conversacion: Optional[List[Dict[str, Any]]] = None,
    recommended_plan: Optional[Dict[str, Any]] = None,
    pending_issue_followup: bool = False,
) -> ChatResponse:
    """
    Simula la generación de lenguaje por el LLM o usa LangChain real con DeepSeek 
    si hay una API Key configurada.
    """
    
    # Check if we should use the real LLM
    if llm_is_available():
        # 1. Configurar el LLM apuntando a DeepSeek vía la interfaz de OpenAI
        llm = _build_llm(max_tokens=1000, temperature=0.3)
        
        # 2. Configurar el Output Parser para garantizar la estructura
        parser = PydanticOutputParser(pydantic_object=ChatResponse)
        
        # 3. Diseñar el Prompt
        system_template = """
        Eres Lucía, la asistente de facturación B2C de una empresa de telecomunicaciones en Perú.
        Tu objetivo es explicar variaciones de recibos de manera empática y clara.
        REGLA DE ORO: NO PUEDES hacer cálculos matemáticos. Toda la información de montos, 
        fechas y variaciones DEBE salir estrictamente del 'Deterministic Payload'. No inventes números.

        REGLA DE MONEDA: usa SIEMPRE el símbolo indicado en 'simbolo_moneda' del payload (soles peruanos).
        Está terminantemente prohibido usar cualquier otro símbolo o moneda (€, $, USD, EUR).
        Formato correcto: "S/ 119.90". Usa punto como separador decimal.

        REGLA DE CONTINUIDAD Y SALUDOS (CRÍTICA):
        - Si en 'HISTORIAL RECIENTE DE LA CONVERSACIÓN' ya hay mensajes previos (la conversación está en curso):
          * NUNCA saludes con "¡Hola!", "Hola", "¡Hola de nuevo!" ni te vuelvas a presentar ("Soy Lucía...").
          * Tu primer mensaje ("hook") debe ser una transición fluida y natural, por ejemplo:
            "Bien, déjame revisar tu estado de cuenta...", "Revisando el detalle de tus recibos...", "Claro, aquí tengo la información:", o ir directamente a la respuesta.
          * Repetir "¡Hola!" a mitad de una conversación suena robótico e interrumpido.
        - Solo puedes usar "¡Hola!" o presentarte si es estrictamente el PRIMER turno de toda la sesión (historial vacío).
        - Si ya le explicaste al usuario esta variación en un turno anterior, NO la repitas de nuevo:
          responde directamente a lo que pregunta AHORA o añade solo el detalle nuevo.

        REGLA DE ESTRUCTURA Y TIPOS DE MENSAJE ('messages') (CRÍTICA):
        Cada elemento en la lista 'messages' tiene un campo 'type'. Debes asignar los tipos con estricta precisión:
        - "hook": Solo para el primer mensaje breve de saludo o transición contextual (ej: "Revisando tu recibo...", "Claro, te confirmo:").
        - "explanation": Para TODO el cuerpo explicativo de la respuesta, motivos de variación, confirmaciones, estado de cuenta, efecto efervescente (beneficios de su plan) y despedidas, conclusiones o frases amables de cierre (ej: "Así que tranqui, no tienes nada más que pagar. Si necesitas revisar algo más...", "¡Cualquier otra duda aquí estoy!"). TODO texto conversacional, conclusión, frase tranquilizadora o despedida DEBE ser de tipo "explanation".
        - "evidence": ÚNICA Y EXCLUSIVAMENTE para desgloses numéricos puntuales de montos, cargos específicos, pagos o líneas técnicas de facturación (ej: "Recibo actual (2026-07-21): S/ 39.90" o desglose de conceptos facturados con sus cifras).
        NUNCA clasifiques como "evidence" textos de conclusión, frases tranquilizadoras, despedidas, recomendaciones o textos conversacionales. Si no contiene un desglose numérico o detalle técnico de pagos/planes/recibos, su tipo DEBE ser "explanation".

        HISTORIAL RECIENTE DE LA CONVERSACIÓN:
        {historial_conversacion}

        SEGUIMIENTO DE CASOS PENDIENTES: {pending_issue_followup}
        (Si es True, el usuario tuvo un problema que quedó sin resolver en el pasado. 
        Empieza tu respuesta preguntando proactivamente si lograron solucionarlo o cómo le fue con eso, antes de atender su consulta actual).

        {format_instructions}

        
        INFORMACIÓN DETERMINISTA (Verdad absoluta, no la modifiques):
        {deterministic_payload}
        
        CONTEXTO (Base de conocimiento / RAG):
        {rag_context}
        
        ESTADO COMERCIAL:
        ¿Es elegible para venta cruzada?: {cross_sell_eligible}
        PLAN RECOMENDADO VERIFICADO (dato de solo lectura, ya validado contra el catálogo real):
        {recommended_plan}
        Si es elegible (True) Y hay un plan recomendado verificado (no es null), completa
        'plan_optimizer_suggestion.available' = true y usa EXACTAMENTE el nombre, precio y
        beneficios del plan recomendado verificado — no inventes otro plan, otro precio ni
        otro nombre. Redacta solo el mensaje comercial en tono natural.
        Si no es elegible (False) o el plan recomendado verificado es null,
        'plan_optimizer_suggestion.available' DEBE ser False y no debes mencionar ningún plan.

        EFECTO EFERVESCENTE (MUY IMPORTANTE):
        Si la consulta o queja actual se ha resuelto positivamente y NO hay venta cruzada,
        cierra tu explicación recordando de forma natural y proactiva los beneficios actuales de su plan 
        (que figura en el Deterministic Payload). Haz que el usuario recuerde lo bueno de su plan.

        ALERTAS PROACTIVAS VERIFICADAS (si la lista no está vacía, menciona la más próxima a
        vencer de forma proactiva y amable; si está vacía, no inventes ninguna alerta):
        {upcoming_alerts}
        
        EMOCIONES PENDIENTES DEL USUARIO:
        {pending_emotions}
        (Si hay emociones de duda o miedo hacia la IA como "no creo que un bot pueda ayudarme",
        responde de manera muy empática y transparente, explicando amablemente que tú sí tienes acceso
        preciso a sus recibos y que puedes ayudarle).

        REGLA DE VERBALIZACIÓN DE CERTEZA Y LÍMITES EN LENGUAJE NATURAL:
        En lugar de actuar como un bot que solo entrega números, verbaliza activamente tu certeza y tus límites:
        - Si conoces con total certeza los conceptos porque coinciden con el recibo, explícalo con claridad y firmeza.
        - Si algún dato no está disponible (ej: acuerdos contractuales de renovación o fechas exactas de corte), di honestamente qué sabes con certeza matemática y cuál es el límite.
        
        REGLA DE TRANQUILIDAD EN DERIVACIÓN A ASESOR (CRÍTICA):
        Si la respuesta contempla derivación a un asesor o gestión humana, incluye SIEMPRE la frase tranquilizadora:
        "Ya envié a tu asesor el expediente con todo el detalle de tu recibo y lo que acabamos de revisar, así que no vas a tener que repetir nada. 🙏"

        SESIÓN ACTUAL: {session_id}
        """
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_template),
            ("user", "{user_message}")
        ])
        
        # 4. Ejecutar la cadena
        chain = prompt | llm | parser
        
        try:
            response_obj = chain.invoke({
                "format_instructions": parser.get_format_instructions(),
                "deterministic_payload": json.dumps(deterministic_payload, indent=2),
                "rag_context": rag_context,
                "cross_sell_eligible": str(cross_sell_eligible),
                "recommended_plan": json.dumps(recommended_plan) if recommended_plan else "null",
                "upcoming_alerts": json.dumps(deterministic_payload.get("upcoming_alerts") or []),
                "pending_emotions": json.dumps(pending_emotions, indent=2) if pending_emotions else "Ninguna",
                "perfil_lexico": persona.normalizar_perfil(perfil_lexico),
                "instruccion_perfil": persona.instruccion_registro(perfil_lexico),
                "historial_conversacion": _formatear_historial(historial_conversacion),
                "pending_issue_followup": "True" if pending_issue_followup else "False",
                "session_id": session_id,
                "user_message": user_message
            })
            # El tono es decisión de la capa de personalidad, no del modelo.
            response_obj.personality_metadata.lucia_tone = persona.tono_para_metadata(perfil_lexico)

            # Blindaje anti-alucinación: si el LLM propuso un plan igual estando
            # deshabilitado el cross-sell, o inventó datos distintos al verificado,
            # se corrige aquí en vez de confiar ciegamente en la salida del modelo.
            if not cross_sell_eligible or not recommended_plan:
                response_obj.plan_optimizer_suggestion = PlanOptimizerSuggestion()
            elif response_obj.plan_optimizer_suggestion.available:
                response_obj.plan_optimizer_suggestion.plan_recomendado = RecommendedPlan(**recommended_plan)

            # upcoming_alerts es un hecho determinista: no se deja que el LLM lo omita o invente.
            response_obj.upcoming_alerts = [UpcomingAlert(**a) for a in (deterministic_payload.get("upcoming_alerts") or [])]

            # Sanitizar chunks para que 'evidence' solo contenga datos de facturación/pagos/planes reales
            response_obj.messages = _sanitizar_message_chunks(response_obj.messages)

            return response_obj
            
        except Exception as e:
            # Si el LLM falla, hace fallback al mock
            print(f"Error con LLM DeepSeek: {e}. Fallback a Mock.")
            return _generate_mock_response(
                session_id, user_message, deterministic_payload, rag_context,
                cross_sell_eligible, pending_emotions, perfil_lexico, historial_conversacion, recommended_plan, pending_issue_followup
            )
    else:
        # Usar el mock por defecto si no hay API KEY
        return _generate_mock_response(
            session_id, user_message, deterministic_payload, rag_context,
            cross_sell_eligible, pending_emotions, perfil_lexico, historial_conversacion, recommended_plan, pending_issue_followup
        )



# ---------------------------------------------------------------------------
# Turnos conversacionales (no facturación)
# ---------------------------------------------------------------------------
#
# Aquí el LLM hace lo único que sabe hacer bien y le está permitido: entender
# lenguaje natural (incluidas jergas peruanas) y redactar. NO recibe ningún dato
# de facturación, porque no lo necesita para saludar o redirigir una conversación.
#
# Esto sustituye cualquier catálogo de frases prearmadas: no es posible enumerar
# todas las formas en que alguien puede escribir, así que no se intenta.

INTENTS_VALIDOS = (
    "FACTURACION",
    "SOLICITUD_AGENTE",
    "SALUDO",
    "DESPEDIDA",
    "AGRADECIMIENTO",
    "FUERA_DE_DOMINIO",
)


def _extraer_json(texto: str) -> Optional[Dict[str, Any]]:
    """
    Extrae el primer objeto JSON de la respuesta del modelo.
    Tolera bloques markdown y texto adicional alrededor.
    """
    if not texto:
        return None

    limpio = texto.strip()
    # Quitar cercos markdown (```json ... ```)
    limpio = re.sub(r"^```(?:json)?\s*", "", limpio)
    limpio = re.sub(r"\s*```$", "", limpio)

    try:
        return json.loads(limpio)
    except json.JSONDecodeError:
        pass

    # Buscar el primer objeto balanceado
    inicio = limpio.find("{")
    if inicio == -1:
        return None
    profundidad = 0
    for i, ch in enumerate(limpio[inicio:], start=inicio):
        if ch == "{":
            profundidad += 1
        elif ch == "}":
            profundidad -= 1
            if profundidad == 0:
                try:
                    return json.loads(limpio[inicio:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


_SYSTEM_CLASIFICACION = """{identidad}

TAREA
Analiza el mensaje del usuario y devuelve EXCLUSIVAMENTE un objeto JSON válido,
sin texto adicional y sin bloques de código, con esta forma:

{{"intent": "...", "perfil_lexico": "...", "respuesta": "..."}}

CAMPO "intent" — elige exactamente uno:
- "FACTURACION": el usuario pregunta o reclama algo sobre su recibo, montos,
  cobros, plan, promociones, cuotas, deuda o servicio. Incluye quejas por precio
  y consultas expresadas con jerga o de forma indirecta.
- "SOLICITUD_AGENTE": el MENSAJE ACTUAL pide explícitamente hablar con una
  persona, un asesor o un agente humano, o rechaza seguir hablando con un bot/IA.
  Esta intención tiene prioridad sobre FACTURACION si ambas aparecen juntas.
  IMPORTANTE: clasifica solo según el mensaje actual. Si en el historial Lucía
  ya derivó a un agente en un turno anterior pero el usuario sigue escribiendo
  con normalidad (porque en esta conversación aún no llegó ningún agente),
  NO vuelvas a clasificar como SOLICITUD_AGENTE salvo que el usuario lo esté
  pidiendo de nuevo en su mensaje actual. Una solicitud pasada no contamina
  la clasificación de los turnos siguientes.
- "SALUDO": solo saluda o inicia conversación sin plantear todavía una consulta.
- "DESPEDIDA": se está despidiendo o cerrando la conversación.
- "AGRADECIMIENTO": agradece o expresa conformidad, sin nueva consulta.
- "FUERA_DE_DOMINIO": cualquier otra cosa (charla personal, bromas, preguntas
  sobre ti, temas ajenos a telecomunicaciones).

Ante duda razonable entre FACTURACION y otra categoría, elige FACTURACION:
es mejor revisar el recibo que ignorar una consulta legítima.

CAMPO "perfil_lexico" — cómo escribe el usuario. Elige exactamente uno:
- "FORMAL": redacción cuidada, trato de usted, sin coloquialismos.
- "CASUAL": español natural y relajado, sin jergas marcadas.
- "USO_JERGAS": coloquialismos o jerga peruana (p. ej. "pe", "causa", "chibolo",
  "manyas", "ya fue", "oe", "bravazo", "yapa", "al toque", "chamba", "plata",
  "salado", "misio", "roche"), abreviaturas tipo "ntp", "xq", o escritura muy informal.

CAMPO "respuesta"
- Si "intent" es "FACTURACION" o "SOLICITUD_AGENTE": devuelve exactamente "".
  (Estos dos casos los responde otro componente del sistema, no tú en este paso).
- En cualquier otro caso: escribe la respuesta de Lucía, en primera persona,
  adaptada al perfil_lexico detectado.

REGLAS DE LA RESPUESTA CONVERSACIONAL
- Breve: 1 o 2 frases.
- Nunca menciones montos, recibos concretos, fechas ni cifras: en este turno no
  tienes acceso a datos de facturación y no debes inventarlos.
- No inventes información sobre la cuenta del usuario.
- Si el mensaje es ajeno a tu especialidad, responde con naturalidad y buen humor,
  y reconduce con amabilidad hacia lo que sí puedes resolver. No seas cortante ni
  repitas siempre la misma fórmula.
- Si el usuario pregunta algo personal sobre ti, respóndele con simpatía y sin
  fingir que eres humana.
- Adapta el registro al usuario sin perder nunca la cordialidad ni la corrección.

GUÍA DE REGISTRO PARA ESTE MENSAJE
{instruccion_registro_general}
"""


def classify_and_reply(
    user_message: str,
    perfil_previo: Optional[str] = None,
    pending_emotions: Optional[List[Dict]] = None,
    historial_conversacion: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Una sola llamada al LLM que clasifica la intención, detecta el registro
    lingüístico y, si el turno no es de facturación, redacta la respuesta de Lucía.

    Retorna un dict {intent, perfil_lexico, respuesta} o None si el LLM no está
    disponible o falla (el llamador decide el fallback).
    """
    if not llm_is_available():
        return None

    contexto_extra = ""
    if perfil_previo:
        contexto_extra += (
            f"\nRegistro detectado en turnos anteriores: {persona.normalizar_perfil(perfil_previo)} "
            "(úsalo como referencia, pero prioriza el mensaje actual).\n"
        )
    if pending_emotions:
        textos = [e.get("text", "") for e in pending_emotions if e.get("text")]
        if textos:
            contexto_extra += (
                "\nComentarios emocionales previos del usuario aún no reconocidos: "
                f"{'; '.join(textos)}. Si encaja con naturalidad, reconócelos brevemente.\n"
            )
    if historial_conversacion:
        contexto_extra += (
            "\nHISTORIAL RECIENTE DE LA CONVERSACIÓN (úsalo para entender el contexto; "
            "un mensaje corto como 'sí', 'ok' o 'por favor' normalmente responde al último "
            "turno de Lucía, no es un tema nuevo):\n"
            f"{_formatear_historial(historial_conversacion)}\n"
        )

    system_prompt = _SYSTEM_CLASIFICACION.format(
        identidad=persona.IDENTIDAD_LUCIA,
        instruccion_registro_general=persona.instruccion_registro(perfil_previo),
    ) + contexto_extra

    try:
        llm = _build_llm(max_tokens=400, temperature=0.6)
        resultado = llm.invoke([
            ("system", system_prompt),
            ("user", user_message),
        ])
        datos = _extraer_json(getattr(resultado, "content", "") or "")
        if not datos:
            return None

        intent = str(datos.get("intent", "")).strip().upper()
        if intent not in INTENTS_VALIDOS:
            return None

        return {
            "intent": intent,
            "perfil_lexico": persona.normalizar_perfil(datos.get("perfil_lexico")),
            "respuesta": (datos.get("respuesta") or "").strip(),
        }

    except Exception as e:
        print(f"Error clasificando intención con LLM: {e}. Se usará el fallback determinista.")
        return None


# ---------------------------------------------------------------------------
# Alertas proactivas
# ---------------------------------------------------------------------------
#
# A diferencia de generate_response (que reacciona a un mensaje del usuario),
# aquí Lucía escribe primero. El dato de la alerta (upcoming_alerts) ya viene
# calculado de forma determinista; el LLM solo lo traduce a un mensaje cálido
# y proactivo. Nunca inventa la fecha, el monto ni el concepto.

_SYSTEM_ALERTA_PROACTIVA = """{identidad}

DATOS VERIFICADOS DE LA ALERTA:
- Concepto: {concepto}
- Último ciclo facturado con el beneficio: {fecha_fin}
- Duración pactada del beneficio: {duracion_pactada} mes(es)
- Ciclos en que ya se facturó: {ciclos_facturados}
- Impacto estimado en el recibo: {impacto_estimado}

CONTEXTO DE LA CONVERSACIÓN:
{contexto_conversacion}

REGLAS DE REDACCIÓN:
{reglas_contexto}
- 1 a 2 frases, tono cercano y natural, sin tecnicismos ni frialdad.
- Menciona el impacto estimado usando exactamente el valor dado ({impacto_estimado}). No inventes ni redondees otro monto.
- El beneficio termina al completarse su duración pactada. Habla de "tu próximo recibo" o del ciclo indicado; NO inventes una fecha ni un número de días exactos.
- Cierra invitando a la persona a preguntar si quiere más detalle o ver opciones para su plan.
- No menciones puntajes, IDs, ni nada de la mecánica interna del sistema.
"""


def generate_proactive_alert_message(
    alert: Dict[str, Any],
    perfil_lexico: Optional[str] = None,
    historial_conversacion: Optional[List[Dict[str, Any]]] = None
) -> str:
    """
    Redacta el texto de una alerta proactiva a partir de un upcoming_alert.
    Si detecta que ya existe una conversación previa activa, adapta la redacción
    para no interrumpir con un '¡Hola!' frío y conectar naturalmente con el contexto.
    """
    tiene_historial = bool(historial_conversacion and len(historial_conversacion) > 0)
    duracion = alert.get("duracion_pactada_meses")
    detalle_duracion = f" (estaba pactado por {duracion} mes(es))" if duracion else ""

    if tiene_historial:
        fallback = (
            f"Por cierto, quería comentarte un detalle importante sobre tu línea: tu beneficio "
            f"\"{alert.get('concepto', 'promoción')}\"{detalle_duracion} finaliza en este ciclo ({alert.get('fecha_fin')}), "
            f"lo que representará un aumento estimado de {alert.get('impacto_estimado')} en tu próximo recibo. "
            "Si deseas, podemos revisar juntos alternativas para tu plan."
        )
        contexto_conversacion = (
            "Ya existe una conversación previa activa con este usuario en la sesión.\n"
            f"Últimos mensajes intercambiados:\n{_formatear_historial(historial_conversacion[-4:])}"
        )
        reglas_contexto = (
            "- NO saludes con un frío o desconectado '¡Hola!' ni actúes como si apenas estuvieras iniciando la conversación desde cero.\n"
            "- Conecta con fluidez y naturalidad con la conversación en curso usando conectores como 'Por cierto...', 'Aprovechando que estamos en contacto...', 'Un detalle importante sobre tu línea...', o 'A propósito de lo que veníamos revisando...'"
        )
    else:
        fallback = (
            f"¡Hola! Quería avisarte con tiempo: tu beneficio "
            f"\"{alert.get('concepto', 'promoción')}\"{detalle_duracion} ya llegó a su último "
            f"ciclo facturado ({alert.get('fecha_fin')}). "
            f"El impacto estimado en tu próximo recibo sería de {alert.get('impacto_estimado')}. "
            "¿Quieres que revisemos juntos tus opciones?"
        )
        contexto_conversacion = "El usuario no ha interactuado recientemente (primer contacto proactivo)."
        reglas_contexto = (
            "- Saluda amablemente al inicio ('¡Hola! Quería avisarte con tiempo sobre tu línea...')."
        )

    if not llm_is_available():
        return fallback

    try:
        llm = _build_llm(max_tokens=200, temperature=0.5)
        system_prompt = _SYSTEM_ALERTA_PROACTIVA.format(
            identidad=persona.IDENTIDAD_LUCIA,
            concepto=alert.get("concepto", "Descuento activo"),
            fecha_fin=alert.get("fecha_fin"),
            duracion_pactada=alert.get("duracion_pactada_meses") or "no informada",
            ciclos_facturados=alert.get("ciclos_facturados") or "no informado",
            impacto_estimado=alert.get("impacto_estimado"),
            contexto_conversacion=contexto_conversacion,
            reglas_contexto=reglas_contexto,
        )
        instruccion = persona.instruccion_registro(perfil_lexico)
        resultado = llm.invoke([
            ("system", system_prompt + f"\n\nGUÍA DE REGISTRO: {instruccion}"),
            ("user", "Genera el mensaje proactivo adaptado al contexto."),
        ])
        texto = (getattr(resultado, "content", "") or "").strip()
        return texto or fallback
    except Exception as e:
        print(f"Error generando alerta proactiva con LLM: {e}. Se usará fallback.")
        return fallback

