from sqlalchemy.orm import Session
from app.db import crud
from app.services.deterministic import validate_compliance, calculate_billing_facts, evaluate_cross_sell_eligibility
from app.services.rag import retrieve_context
from app.services.llm import generate_response
from app.core.schemas import ChatRequest, ChatResponse, MessageChunk

def process_message(request: ChatRequest, db: Session) -> ChatResponse:
    """
    Orquesta los 8 pasos del ciclo de vida de la petición.
    """
    # Paso 1: Recepción, Carga de Estado y Memoria
    historial = crud.get_or_create_historial(db, request.session_id, request.user_id)
    # Filtrar emociones pendientes
    comentarios = historial.comentarios_emocionales or []
    pending_emotions = [e for e in comentarios if not e.get("referenciado", False)]
    
    # Paso 2: Análisis de Intención, Perfilado y Cumplimiento
    blocked_message = validate_compliance(request.message, db)
    if blocked_message:
        return ChatResponse(
            session_id=request.session_id,
            intent_category="BLOQUEO_COMPLIANCE",
            sentiment_score=1,
            compliance_triggered=True,
            messages=[MessageChunk(text=blocked_message, type="explanation")]
        )
        
    # Paso 3: Motor Investigador (Determinista)
    fact_payload = calculate_billing_facts(request.user_id, db)
    
    # Paso 4: Búsqueda de Conocimiento (RAG)
    rag_context = retrieve_context(request.message)
    
    # Paso 5: Evaluación Comercial
    # Simulación de un score de sentimiento básico basado en el input actual
    current_sentiment = historial.score_sentimiento
    msg_lower = request.message.lower()
    if "gracias" in msg_lower or "genial" in msg_lower:
        current_sentiment = 5
    elif "mal" in msg_lower or "estafa" in msg_lower:
        current_sentiment = 1
        
    estado_resolucion = True # Simulado
    
    cross_sell_eligible = evaluate_cross_sell_eligibility(
        sentiment_score=current_sentiment,
        estado_resolucion=estado_resolucion,
        intent_category=fact_payload.get("detected_event", "GENERAL"),
        no_preguntas_pendientes=True
    )
    
    # Paso 6 y 7: Generación Segura LLM e inyección de memoria
    response = generate_response(
        session_id=request.session_id,
        user_message=request.message,
        deterministic_payload=fact_payload,
        rag_context=rag_context,
        cross_sell_eligible=cross_sell_eligible,
        pending_emotions=pending_emotions
    )
    
    # Actualizar SQLite: marcar emociones como referenciadas y guardar estado
    if pending_emotions:
        for emotion in comentarios:
            emotion["referenciado"] = True
            
    crud.update_historial(db, request.session_id, {
        "comentarios_emocionales": comentarios, 
        "score_sentimiento": current_sentiment
    })
        
    return response
