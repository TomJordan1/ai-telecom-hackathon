import json
from typing import Dict, Any, List
from app.core.schemas import ChatResponse, MessageChunk, PlanOptimizerSuggestion, RecommendedPlan, BillSummary
from app.core.config import settings

# Attempt to import LangChain; will be used if API Key is present
try:
    from langchain_openai import ChatOpenAI
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import PydanticOutputParser
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False


def _generate_mock_response(
    session_id: str, 
    user_message: str, 
    deterministic_payload: Dict[str, Any], 
    rag_context: str,
    cross_sell_eligible: bool,
    pending_emotions: List[Dict]
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
        plan_optimizer_suggestion=suggestion
    )


def generate_response(
    session_id: str, 
    user_message: str, 
    deterministic_payload: Dict[str, Any], 
    rag_context: str,
    cross_sell_eligible: bool,
    pending_emotions: List[Dict]
) -> ChatResponse:
    """
    Simula la generación de lenguaje por el LLM o usa LangChain real con DeepSeek 
    si hay una API Key configurada.
    """
    
    # Check if we should use the real LLM
    if settings.DEEPSEEK_API_KEY and not settings.USE_MOCK_LLM and LANGCHAIN_AVAILABLE:
        # 1. Configurar el LLM apuntando a DeepSeek vía la interfaz de OpenAI
        llm = ChatOpenAI(
            model="deepseek-chat", # Asumiendo el modelo general de DeepSeek (V3/Coder)
            api_key=settings.DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com", # Base URL oficial de DeepSeek
            max_tokens=1000,
            temperature=0.3 # Baja temperatura para que respete estrictamente los datos
        )
        
        # 2. Configurar el Output Parser para garantizar la estructura
        parser = PydanticOutputParser(pydantic_object=ChatResponse)
        
        # 3. Diseñar el Prompt
        system_template = """
        Eres Lucía, la asistente de facturación B2C de una empresa de telecomunicaciones. 
        Tu objetivo es explicar variaciones de recibos de manera empática y clara.
        REGLA DE ORO: NO PUEDES hacer cálculos matemáticos. Toda la información de montos, 
        fechas y variaciones DEBE salir estrictamente del 'Deterministic Payload'. No inventes números.
        
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
                "session_id": session_id,
                "user_message": user_message
            })
            return response_obj
            
        except Exception as e:
            # Si el LLM falla, hace fallback al mock
            print(f"Error con LLM DeepSeek: {e}. Fallback a Mock.")
            return _generate_mock_response(session_id, user_message, deterministic_payload, rag_context, cross_sell_eligible, pending_emotions)
    else:
        # Usar el mock por defecto si no hay API KEY
        return _generate_mock_response(session_id, user_message, deterministic_payload, rag_context, cross_sell_eligible, pending_emotions)
