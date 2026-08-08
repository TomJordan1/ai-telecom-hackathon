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


def _generate_mock_response(
    session_id: str, 
    user_message: str, 
    deterministic_payload: Dict[str, Any], 
    rag_context: str,
    cross_sell_eligible: bool,
    pending_emotions: List[Dict],
    perfil_lexico: Optional[str] = None
) -> ChatResponse:
    """Fallback if no API key is provided."""
    intent_category = deterministic_payload.get("detected_event", "CONSULTA_GENERAL")
    if "error" in deterministic_payload:
        intent_category = "NUEVO_CLIENTE"
    
    messages = []
    if pending_emotions:
        messages.append(MessageChunk(text="Por cierto, entiendo tu preocupación anterior y estoy aquí para aclarar todo detalle 😊.", type="hook", delay_ms=0))
    else:
        messages.append(MessageChunk(text="¡Hola! Soy Lucía. He analizado tus recibos al detalle para explicarte qué pasó.", type="hook", delay_ms=0))
            
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

    suggestion = PlanOptimizerSuggestion()
    if cross_sell_eligible:
        suggestion = PlanOptimizerSuggestion(
            available=True,
            mensaje_comercial="Dato curioso: Lucía encontró un plan con mayor velocidad. ¿Te ayudo a activarlo?",
            plan_recomendado=RecommendedPlan(nombre="Internet 600 Mbps", precio=99.90, beneficios="Doble velocidad, Movistar Play incluido (Mock)")
        )
        
    historial = [BillSummary(month=pb['month'], amount=pb['amount']) for pb in deterministic_payload.get('previous_bills', [])]
        
    return ChatResponse(
        session_id=session_id,
        intent_category=intent_category,
        sentiment_score=3,
        messages=messages,
        historical_bills_summary=historial,
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
    perfil_lexico: Optional[str] = None
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

        {format_instructions}
        
        INFORMACIÓN DETERMINISTA (Verdad absoluta, no la modifiques):
        {deterministic_payload}
        
        CONTEXTO (Base de conocimiento / RAG):
        {rag_context}
        
        ESTADO COMERCIAL:
        ¿Es elegible para venta cruzada?: {cross_sell_eligible}
        Si es elegible (True), incluye obligatoriamente una sugerencia comercial atractiva en 'plan_optimizer_suggestion'.
        Si no es elegible (False), 'plan_optimizer_suggestion.available' DEBE ser False.
        
        EMOCIONES PENDIENTES DEL USUARIO:
        {pending_emotions}
        (Si hay emociones aquí, asegúrate de referenciarlas sutilmente en tus mensajes).

        REGISTRO LINGÜÍSTICO DEL USUARIO: {perfil_lexico}
        {instruccion_perfil}

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
                "pending_emotions": json.dumps(pending_emotions, indent=2) if pending_emotions else "Ninguna",
                "perfil_lexico": persona.normalizar_perfil(perfil_lexico),
                "instruccion_perfil": persona.instruccion_registro(perfil_lexico),
                "session_id": session_id,
                "user_message": user_message
            })
            # El tono es decisión de la capa de personalidad, no del modelo.
            response_obj.personality_metadata.lucia_tone = persona.tono_para_metadata(perfil_lexico)
            return response_obj
            
        except Exception as e:
            # Si el LLM falla, hace fallback al mock
            print(f"Error con LLM DeepSeek: {e}. Fallback a Mock.")
            return _generate_mock_response(session_id, user_message, deterministic_payload, rag_context, cross_sell_eligible, pending_emotions, perfil_lexico)
    else:
        # Usar el mock por defecto si no hay API KEY
        return _generate_mock_response(session_id, user_message, deterministic_payload, rag_context, cross_sell_eligible, pending_emotions, perfil_lexico)


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
- Si "intent" es "FACTURACION": devuelve exactamente "".
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
