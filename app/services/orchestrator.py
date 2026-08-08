from typing import Optional

from sqlalchemy.orm import Session
from app.db import crud
from app.services.deterministic import validate_compliance, calculate_billing_facts, evaluate_cross_sell_eligibility
from app.services.rag import retrieve_context
from app.services.llm import generate_response
from app.services.case_matcher import match_caso
from app.services.uncertainty_calculator import calculate_uncertainty, requires_handoff
from app.services.feedback_handler import register_new_case
from app.services.intent_classifier import route
from app.services import persona
from app.core.schemas import ChatRequest, ChatResponse, MessageChunk, PersonalityMetadata


def _texto_completo(response: ChatResponse) -> str:
    """Concatena los chunks de una respuesta para guardarlos en la bitácora."""
    return " ".join(m.text for m in response.messages if m.text)


def _finalizar(db: Session, request: ChatRequest, response: ChatResponse) -> ChatResponse:
    """
    Punto único de salida: registra el turno (usuario + Lucía) en la bitácora
    acotada de la sesión. Sin este registro, el siguiente turno no tiene forma
    de saber qué ya se dijo y el modelo termina repitiéndose.
    """
    crud.append_turno_conversacion(db, request.session_id, "user", request.message)
    crud.append_turno_conversacion(
        db, request.session_id, "lucia", _texto_completo(response), response.intent_category
    )
    return response


def process_message(request: ChatRequest, db: Session) -> ChatResponse:
    """
    Orquesta el ciclo completo de vida de la petición.

    Solo los turnos de facturación atraviesan el motor determinista, el case
    matcher, el índice de incertidumbre y el RAG. Los turnos conversacionales se
    resuelven sin tocar datos de facturación.
    """
    # Paso 1: Recepción, Carga de Estado y Memoria
    historial = crud.get_or_create_historial(db, request.session_id, request.user_id)
    comentarios = historial.comentarios_emocionales or []
    pending_emotions = [e for e in comentarios if not e.get("referenciado", False)]
    historial_conversacion = historial.historial_conversacion or []

    # Paso 2: Pre-filtro de Compliance (Regex, antes de cualquier IA)
    blocked_message = validate_compliance(request.message, db)
    if blocked_message:
        return _finalizar(db, request, ChatResponse(
            session_id=request.session_id,
            intent_category="BLOQUEO_COMPLIANCE",
            sentiment_score=1,
            compliance_triggered=True,
            messages=[MessageChunk(text=blocked_message, type="explanation")]
        ))

    # Paso 2.5: Enrutamiento de intención y perfilado lingüístico
    decision = route(
        message=request.message,
        perfil_previo=historial.perfil_lexico_usuario,
        pending_emotions=pending_emotions,
        historial_conversacion=historial_conversacion,
    )

    # El registro lingüístico observado se persiste para dar continuidad a la sesión.
    perfil_lexico = decision.perfil_lexico
    if perfil_lexico != historial.perfil_lexico_usuario:
        crud.update_historial(db, request.session_id, {"perfil_lexico_usuario": perfil_lexico})

    print(
        f"[routing] session={request.session_id} intent={decision.intent} "
        f"perfil={perfil_lexico} fuente={decision.fuente}"
    )

    # Solicitud explícita de agente humano: máxima prioridad, no se improvisa
    # ni se re-explica facturación. Se deriva de inmediato.
    if decision.es_solicitud_agente:
        return _finalizar(db, request, ChatResponse(
            session_id=request.session_id,
            intent_category="SOLICITUD_AGENTE",
            sentiment_score=historial.score_sentimiento,
            requires_human_intervention=True,
            confidence_score=99,
            messages=[
                MessageChunk(
                    text="Entendido, te comunico con un asesor. En un momento un agente "
                         "humano continuará contigo con todo el contexto de tu consulta. 🙏",
                    type="explanation",
                )
            ],
            personality_metadata=PersonalityMetadata(
                lucia_tone=persona.tono_para_metadata(perfil_lexico)
            ),
        ))

    # Turno conversacional: se responde sin consultar datos de facturación.
    if not decision.es_facturacion:
        sentimiento = historial.score_sentimiento
        if decision.intent == "AGRADECIMIENTO":
            sentimiento = 5
            crud.update_historial(db, request.session_id, {"score_sentimiento": sentimiento})

        return _finalizar(db, request, ChatResponse(
            session_id=request.session_id,
            intent_category=decision.intent,
            sentiment_score=sentimiento,
            # No se afirma ningún hecho de facturación en este turno.
            confidence_score=99,
            messages=[MessageChunk(text=decision.respuesta, type="hook")],
            personality_metadata=PersonalityMetadata(
                lucia_tone=persona.tono_para_metadata(perfil_lexico)
            ),
        ))

    # Paso 3: Motor Investigador (Determinista)
    fact_payload = calculate_billing_facts(request.user_id, db)

    # Paso 3.5: Case Matcher — ¿existe una solución validada para este patrón?
    caso_match = match_caso(db, fact_payload)
    caso_id_origen = None  # Se usará para registrar feedback después

    if caso_match:
        caso_id_origen, solucion_conocida = caso_match
        # Inyectar la solución verificada al payload para que el LLM solo la personalice
        fact_payload["solucion_conocida"] = solucion_conocida
        fact_payload["caso_id"] = caso_id_origen

    # Paso 3.6: Índice de Incertidumbre Determinístico
    uncertainty_score = calculate_uncertainty(
        fact_payload=fact_payload,
        caso_conocido=caso_match,
        rag_context=None,
        compliance_triggered=False
    )

    # Si la incertidumbre supera el umbral → handoff inmediato
    if requires_handoff(uncertainty_score):
        return _finalizar(db, request, ChatResponse(
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
            ],
            personality_metadata=PersonalityMetadata(
                lucia_tone=persona.tono_para_metadata(perfil_lexico)
            ),
        ))

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
        pending_emotions=pending_emotions,
        perfil_lexico=perfil_lexico,
        historial_conversacion=historial_conversacion,
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

    # Paso 7.5: Si era un caso nuevo (sin match), registrar en cuarentena
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

    return _finalizar(db, request, response)
