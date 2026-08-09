import time
from typing import Optional, List, Dict, Any

from sqlalchemy.orm import Session
from app.db import crud
from app.services.deterministic import (
    validate_compliance,
    calculate_billing_facts,
    evaluate_cross_sell_eligibility,
    has_pending_followup_question,
    is_case_resolved,
    recommend_plan_upgrade,
    extract_emotional_comment,
)
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


def _hook_used(response: ChatResponse) -> Optional[str]:
    """Extrae el texto del primer chunk tipo 'hook' para personality_metadata."""
    for m in response.messages:
        if m.type == "hook" and m.text:
            return m.text
    return None


def _build_handoff_context(
    request: ChatRequest,
    historial,
    historial_conversacion: List[Dict[str, Any]],
    motivo: str,
    fact_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Empaqueta el contexto que un agente humano necesita para continuar la
    conversación sin que el cliente tenga que repetir todo desde cero.
    Sin esto, derivar a un humano descarta toda la investigación ya hecha.
    """
    contexto = {
        "motivo": motivo,
        "user_id": request.user_id,
        "ultimo_mensaje": request.message,
        "sentimiento_score": historial.score_sentimiento,
        "perfil_lexico": historial.perfil_lexico_usuario,
        "historial_reciente": historial_conversacion[-6:],
        "comentarios_emocionales_pendientes": [
            c.get("text") for c in (historial.comentarios_emocionales or [])
            if not c.get("referenciado", False)
        ],
    }
    if fact_payload:
        contexto["evidencia_determinista"] = {
            "detected_event": fact_payload.get("detected_event"),
            "evidence": fact_payload.get("evidence"),
            "variation_amount": fact_payload.get("variation_amount"),
        }
    return contexto


def _registrar_auditoria(
    db: Session,
    session_id: str,
    started_at: float,
    intent_category: str,
    components_invoked: List[str],
    detected_event: Optional[str] = None,
    compliance_triggered: bool = False,
    requires_human_intervention: bool = False,
    cross_sell_eligible: bool = False,
    confidence_score: Optional[int] = None,
    uncertainty_score: Optional[float] = None,
    evidence: Optional[List[str]] = None,
    handoff_context: Optional[Dict[str, Any]] = None,
):
    """
    Registra la decisión completa del turno para poder reconstruir el flujo
    en auditoría. Nunca debe interrumpir la respuesta al usuario si falla.
    Cuando hay derivación a humano, también guarda handoff_context: es lo
    que alimenta la cola de atención del panel de administración.
    """
    try:
        crud.create_audit_log(
            db,
            session_id=session_id,
            intent_category=intent_category,
            detected_event=detected_event,
            compliance_triggered=compliance_triggered,
            requires_human_intervention=requires_human_intervention,
            cross_sell_eligible=cross_sell_eligible,
            confidence_score=confidence_score,
            uncertainty_score=uncertainty_score,
            components_invoked=components_invoked,
            evidence=evidence,
            handoff_context=handoff_context,
            latency_ms=int((time.monotonic() - started_at) * 1000),
        )
    except Exception as e:
        print(f"[AUDIT ERROR] No se pudo registrar auditoría: {e}")


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
    started_at = time.monotonic()
    components_invoked = ["compliance_filter"]

    # Paso 1: Recepción, Carga de Estado y Memoria
    historial = crud.get_or_create_historial(db, request.session_id, request.user_id)
    comentarios = historial.comentarios_emocionales or []
    pending_emotions = [e for e in comentarios if not e.get("referenciado", False)]
    historial_conversacion = historial.historial_conversacion or []

    # Paso 2: Pre-filtro de Compliance (Regex, antes de cualquier IA)
    blocked_message = validate_compliance(request.message, db)
    if blocked_message:
        response = ChatResponse(
            session_id=request.session_id,
            intent_category="BLOQUEO_COMPLIANCE",
            sentiment_score=1,
            compliance_triggered=True,
            messages=[MessageChunk(text=blocked_message, type="explanation")]
        )
        _registrar_auditoria(
            db, request.session_id, started_at, response.intent_category, components_invoked,
            compliance_triggered=True, confidence_score=response.confidence_score,
        )
        return _finalizar(db, request, response)

    # Paso 2.5: Enrutamiento de intención y perfilado lingüístico
    components_invoked.append("intent_router")
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

    # Extracción de comentarios emocionales del mensaje actual (memoria emocional
    # real: antes solo se leían comentarios existentes, nunca se creaban nuevos).
    comentario_detectado = extract_emotional_comment(request.message)
    if comentario_detectado:
        crud.add_comentario_emocional(db, request.session_id, comentario_detectado)

    # Solicitud explícita de agente humano: máxima prioridad, no se improvisa
    # ni se re-explica facturación. Se deriva de inmediato con contexto completo.
    if decision.es_solicitud_agente:
        response = ChatResponse(
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
            handoff_context=_build_handoff_context(
                request, historial, historial_conversacion, motivo="SOLICITUD_EXPLICITA_USUARIO"
            ),
        )
        _registrar_auditoria(
            db, request.session_id, started_at, response.intent_category, components_invoked,
            requires_human_intervention=True, confidence_score=response.confidence_score,
            handoff_context=response.handoff_context,
        )
        return _finalizar(db, request, response)

    # Turno conversacional: se responde sin consultar datos de facturación.
    if not decision.es_facturacion:
        components_invoked.append("llm_conversational")
        sentimiento = historial.score_sentimiento
        if decision.intent == "AGRADECIMIENTO":
            sentimiento = 5
            crud.update_historial(db, request.session_id, {"score_sentimiento": sentimiento})

        response = ChatResponse(
            session_id=request.session_id,
            intent_category=decision.intent,
            sentiment_score=sentimiento,
            # No se afirma ningún hecho de facturación en este turno.
            confidence_score=99,
            messages=[MessageChunk(text=decision.respuesta, type="hook")],
            personality_metadata=PersonalityMetadata(
                lucia_tone=persona.tono_para_metadata(perfil_lexico),
                hook_used=decision.respuesta,
            ),
        )
        _registrar_auditoria(
            db, request.session_id, started_at, response.intent_category, components_invoked,
            confidence_score=response.confidence_score,
        )
        return _finalizar(db, request, response)

    # Paso 3: Motor Investigador (Determinista)
    components_invoked.append("deterministic_engine")
    fact_payload = calculate_billing_facts(request.user_id, db)

    # Paso 3.5: Case Matcher — ¿existe una solución validada para este patrón?
    components_invoked.append("case_matcher")
    caso_match = match_caso(db, fact_payload)
    caso_id_origen = None  # Se usará para registrar feedback después

    if caso_match:
        caso_id_origen, solucion_conocida = caso_match
        # Inyectar la solución verificada al payload para que el LLM solo la personalice
        fact_payload["solucion_conocida"] = solucion_conocida
        fact_payload["caso_id"] = caso_id_origen

    # Paso 3.6: Índice de Incertidumbre Determinístico
    components_invoked.append("uncertainty_calculator")
    uncertainty_score = calculate_uncertainty(
        fact_payload=fact_payload,
        caso_conocido=caso_match,
        rag_context=None,
        compliance_triggered=False
    )

    # Si la incertidumbre supera el umbral → handoff inmediato, con contexto completo
    if requires_handoff(uncertainty_score):
        response = ChatResponse(
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
            handoff_context=_build_handoff_context(
                request, historial, historial_conversacion,
                motivo="INCERTIDUMBRE_ALTA", fact_payload=fact_payload
            ),
        )
        _registrar_auditoria(
            db, request.session_id, started_at, response.intent_category, components_invoked,
            detected_event=fact_payload.get("detected_event"), requires_human_intervention=True,
            confidence_score=response.confidence_score, uncertainty_score=uncertainty_score,
            evidence=fact_payload.get("evidence"), handoff_context=response.handoff_context,
        )
        return _finalizar(db, request, response)

    # Paso 4: Búsqueda de Conocimiento (RAG) — solo si no hay caso conocido
    if not caso_match:
        components_invoked.append("rag")
    rag_context = retrieve_context(request.message) if not caso_match else "Caso conocido — RAG omitido."

    # Paso 5: Evaluación de Sentimiento y Gatillo Comercial (señales reales, no asumidas)
    current_sentiment = historial.score_sentimiento
    msg_lower = request.message.lower()
    if any(w in msg_lower for w in ["gracias", "genial", "perfecto", "excelente"]):
        current_sentiment = 5
    elif any(w in msg_lower for w in ["mal", "estafa", "terrible", "molesto"]):
        current_sentiment = 1

    estado_resolucion = is_case_resolved(fact_payload.get("detected_event", ""))
    no_preguntas_pendientes = not has_pending_followup_question(request.message)

    cross_sell_eligible = evaluate_cross_sell_eligibility(
        sentiment_score=current_sentiment,
        estado_resolucion=estado_resolucion,
        intent_category=fact_payload.get("detected_event", "GENERAL"),
        no_preguntas_pendientes=no_preguntas_pendientes
    )

    # El plan recomendado se resuelve contra el catálogo real ANTES de llamar al LLM:
    # así el modelo nunca decide ni inventa qué plan ofrecer, solo redacta sobre el dato.
    recommended_plan = None
    if cross_sell_eligible:
        components_invoked.append("plan_catalog")
        recommended_plan = recommend_plan_upgrade(db, fact_payload.get("plan_actual"))
        if not recommended_plan:
            cross_sell_eligible = False  # sin candidato real verificado, no se ofrece nada

    # Paso 6: Generación Segura con LLM (solo recibe datos verificados)
    components_invoked.append("llm_billing")
    response = generate_response(
        session_id=request.session_id,
        user_message=request.message,
        deterministic_payload=fact_payload,
        rag_context=rag_context,
        cross_sell_eligible=cross_sell_eligible,
        pending_emotions=pending_emotions,
        perfil_lexico=perfil_lexico,
        historial_conversacion=historial_conversacion,
        recommended_plan=recommended_plan,
    )

    # Adjuntar el confidence_score (inverso de incertidumbre)
    response.confidence_score = int((1 - uncertainty_score) * 100)
    if response.personality_metadata and not response.personality_metadata.hook_used:
        response.personality_metadata.hook_used = _hook_used(response)

    # Paso 7: Validación y Actualización de Memoria
    if pending_emotions:
        for emotion in comentarios:
            emotion["referenciado"] = True

    crud.update_historial(db, request.session_id, {
        "comentarios_emocionales": comentarios,
        "score_sentimiento": current_sentiment,
        "estado_resolucion": estado_resolucion,
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

    _registrar_auditoria(
        db, request.session_id, started_at, response.intent_category, components_invoked,
        detected_event=fact_payload.get("detected_event"), cross_sell_eligible=cross_sell_eligible,
        confidence_score=response.confidence_score, uncertainty_score=uncertainty_score,
        evidence=fact_payload.get("evidence"),
    )

    return _finalizar(db, request, response)
