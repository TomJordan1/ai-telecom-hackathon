from sqlalchemy.orm import Session
from app.db import crud
from app.services.deterministic import validate_compliance, calculate_billing_facts, evaluate_cross_sell_eligibility
from app.services.rag import retrieve_context
from app.services.llm import generate_response
from app.services.case_matcher import match_caso
from app.services.uncertainty_calculator import calculate_uncertainty, requires_handoff
from app.services.feedback_handler import register_new_case
from app.services.intent_classifier import classify_intent, get_conversational_response
from app.core.schemas import ChatRequest, ChatResponse, MessageChunk

def process_message(request: ChatRequest, db: Session) -> ChatResponse:
    """
    Orquesta el ciclo completo de vida de la petición (8 pasos + 2 nuevos).
    """
    # Paso 1: Recepción, Carga de Estado y Memoria
    historial = crud.get_or_create_historial(db, request.session_id, request.user_id)
    comentarios = historial.comentarios_emocionales or []
    pending_emotions = [e for e in comentarios if not e.get("referenciado", False)]

    # Paso 2: Pre-filtro de Compliance (Regex)
    blocked_message = validate_compliance(request.message, db)
    if blocked_message:
        return ChatResponse(
            session_id=request.session_id,
            intent_category="BLOQUEO_COMPLIANCE",
            sentiment_score=1,
            compliance_triggered=True,
            messages=[MessageChunk(text=blocked_message, type="explanation")]
        )

    # Paso 2.5: Clasificación de Intención (Determinista)
    # Si el mensaje no es de facturación, responder conversacionalmente sin activar el pipeline pesado.
    intent, sub_intent = classify_intent(request.message)
    if intent != "FACTURACION":
        conversational_text = get_conversational_response(intent, sub_intent)
        return ChatResponse(
            session_id=request.session_id,
            intent_category=f"{intent}_{sub_intent}",
            sentiment_score=historial.score_sentimiento,
            confidence_score=99,
            messages=[MessageChunk(text=conversational_text, type="hook")]
        )

    # Paso 3: Motor Investigador (Determinista)
    fact_payload = calculate_billing_facts(request.user_id, db)

    # Paso 3.5 [NUEVO]: Case Matcher — ¿existe una solución validada para este patrón?
    caso_match = match_caso(db, fact_payload)
    caso_id_origen = None  # Se usará para registrar feedback después

    if caso_match:
        caso_id_origen, solucion_conocida = caso_match
        # Inyectar la solución verificada al payload para que el LLM solo la personalice
        fact_payload["solucion_conocida"] = solucion_conocida
        fact_payload["caso_id"] = caso_id_origen

    # Paso 3.6 [NUEVO]: Índice de Incertidumbre Determinístico
    uncertainty_score = calculate_uncertainty(
        fact_payload=fact_payload,
        caso_conocido=caso_match,
        rag_context=None,
        compliance_triggered=False
    )

    # Si la incertidumbre supera el umbral → handoff inmediato
    if requires_handoff(uncertainty_score):
        return ChatResponse(
            session_id=request.session_id,
            intent_category="DERIVACION_INCERTIDUMBRE",
            sentiment_score=historial.score_sentimiento,
            requires_human_intervention=True,
            confidence_score=int((1 - uncertainty_score) * 100),
            messages=[
                MessageChunk(
                    text="Para darte la mejor respuesta posible, necesito revisar tu caso con más detalle. "
                         "Un agente especializado te contactará en breve. 🙏",
                    type="explanation"
                )
            ]
        )

    # Paso 4: Búsqueda de Conocimiento (RAG) — solo si no hay caso conocido
    rag_context = retrieve_context(request.message) if not caso_match else "Caso conocido — RAG omitido."

    # Paso 5: Evaluación de Sentimiento y Gatillo Comercial
    current_sentiment = historial.score_sentimiento
    msg_lower = request.message.lower()
    if any(w in msg_lower for w in ["gracias", "genial", "perfecto", "excelente"]):
        current_sentiment = 5
    elif any(w in msg_lower for w in ["mal", "estafa", "terrible", "molesto"]):
        current_sentiment = 1

    cross_sell_eligible = evaluate_cross_sell_eligibility(
        sentiment_score=current_sentiment,
        estado_resolucion=True,
        intent_category=fact_payload.get("detected_event", "GENERAL"),
        no_preguntas_pendientes=True
    )

    # Paso 6: Generación Segura con LLM (solo recibe datos verificados)
    response = generate_response(
        session_id=request.session_id,
        user_message=request.message,
        deterministic_payload=fact_payload,
        rag_context=rag_context,
        cross_sell_eligible=cross_sell_eligible,
        pending_emotions=pending_emotions
    )

    # Adjuntar el confidence_score (inverso de incertidumbre)
    response.confidence_score = int((1 - uncertainty_score) * 100)

    # Paso 7: Validación y Actualización de Memoria
    if pending_emotions:
        for emotion in comentarios:
            emotion["referenciado"] = True

    crud.update_historial(db, request.session_id, {
        "comentarios_emocionales": comentarios,
        "score_sentimiento": current_sentiment
    })

    # Paso 7.5 [NUEVO]: Si era un caso nuevo (sin match), registrar en cuarentena
    if not caso_match:
        solucion_serializada = {
            "intent_category": response.intent_category,
            "messages": [m.model_dump() for m in response.messages]
        }
        register_new_case(
            db=db,
            session_id=request.session_id,
            fact_payload=fact_payload,
            solucion_propuesta=solucion_serializada,
            uncertainty_score=uncertainty_score
        )

    return response
