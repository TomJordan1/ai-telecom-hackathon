import re
import time
from datetime import datetime
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
    buscar_cargo_especifico,
)
from app.services.rag import retrieve_context
from app.services.llm import generate_response
from app.services.case_matcher import match_caso
from app.services.uncertainty_calculator import (
    calculate_uncertainty,
    calculate_uncertainty_with_reasons,
    requires_handoff,
)
from app.services.feedback_handler import register_new_case
from app.services.intent_classifier import route, PATRONES_SENSIBLES, has_billing_signals
from app.services.next_actions import resolve_next_actions
from app.services import persona
from app.core.schemas import (
    ChatRequest,
    ChatResponse,
    MessageChunk,
    NextBestAction,
    PersonalityMetadata,
    BillSummary,
    ChargeBreakdownItem,
    VariationBreakdownItem,
    AuditorEquation,
    BillingAdjustments,
)


def _detectar_loop_sin_resolver(historial_conversacion: List[Dict[str, Any]], user_message: str) -> bool:
    """
    Detecta si la conversación ha acumulado intentos no resueltos o ambiguos.
    Límite duro de la industria (anti-loop) para transferir a humano con prontitud.
    """
    if not historial_conversacion or len(historial_conversacion) < 2:
        return False

    marcadores_duda = bool(re.search(
        r"\b(sigo sin|no entiendo|no me queda claro|sigues sin|no respondes|otra vez|no era eso|pero por qu[eé]|por qu[eé] subi[oó]|aclarar)\b",
        user_message,
        re.IGNORECASE
    ))

    turnos_ambiguos = 0
    for t in reversed(historial_conversacion[-6:]):
        if t.get("role") == "lucia":
            intent = t.get("intent") or ""
            if intent in ("INCREMENTO_OTROS", "CONSULTA_GENERAL", "DERIVACION_INCERTIDUMBRE"):
                turnos_ambiguos += 1

    if turnos_ambiguos >= 2 or (turnos_ambiguos >= 1 and marcadores_duda):
        return True

    return False


def _adjuntar_desgloses(response: ChatResponse, fact_payload: Dict[str, Any]) -> None:
    """
    Sobrescribe los desgloses de la respuesta con los del payload determinista.

    Se hace SIEMPRE después de generar el texto, incluso cuando el LLM devolvió
    algo en estos campos: son cifras y no se acepta la versión del modelo. Es la
    misma política que ya se aplica a upcoming_alerts y al plan recomendado.
    """
    current_bill = fact_payload.get("current_bill") or {}
    response.current_bill_breakdown = [
        ChargeBreakdownItem(**item) for item in (current_bill.get("desglose") or [])
    ]
    response.variation_breakdown = [
        VariationBreakdownItem(**item) for item in (fact_payload.get("variacion_por_categoria") or [])
    ]

    auditor = fact_payload.get("auditor_breakdown")
    if auditor:
        response.auditor_breakdown = AuditorEquation(**auditor)

    ajustes = fact_payload.get("ajustes_facturacion")
    response.billing_adjustments = (
        BillingAdjustments(
            cantidad=ajustes.get("cantidad", 0),
            total_notas_credito=ajustes.get("total_notas_credito", 0.0),
            total_notas_debito=ajustes.get("total_notas_debito", 0.0),
        )
        if ajustes else None
    )

    # El historial de recibos también es un hecho verificado: se reconstruye del
    # payload para que el LLM no pueda omitir ciclos ni alterar montos.
    response.historical_bills_summary = [
        BillSummary(month=pb.get("month", ""), amount=pb.get("amount", 0.0), ciclo=pb.get("ciclo"))
        for pb in (fact_payload.get("previous_bills") or [])
    ]


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
    confidence_reasons: Optional[List[str]] = None,
    confidence_score: Optional[int] = None,
    components_invoked: Optional[List[str]] = None,
    canal_preferido: str = "CHAT",
) -> Dict[str, Any]:
    """
    Empaqueta el contexto enriquecido que un agente humano necesita para continuar
    la conversación sin que el cliente tenga que repetir todo desde cero (PoC de integración CRM).
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
        "confidence_score": confidence_score,
        "confidence_reasons": confidence_reasons or [],
        "audit_trail_components": components_invoked or [],
        "canal_preferido": canal_preferido,
        "mensaje_tranquilidad_cliente": persona.MENSAJE_HANDOFF_TRANQUILIDAD,
    }
    if fact_payload:
        current_bill = fact_payload.get("current_bill") or {}
        contexto["evidencia_determinista"] = {
            "detected_event": fact_payload.get("detected_event"),
            "evidence": fact_payload.get("evidence"),
            "variation_amount": fact_payload.get("variation_amount"),
            "current_bill_amount": current_bill.get("amount"),
            "auditor_breakdown": fact_payload.get("auditor_breakdown"),
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


def _finalizar(
    db: Session,
    request: ChatRequest,
    response: ChatResponse,
    fact_payload: Optional[Dict[str, Any]] = None,
) -> ChatResponse:
    """
    Punto único de salida: registra el turno (usuario + Lucía) en la bitácora
    acotada de la sesión y garantiza la inclusión de Siguientes Acciones Recomendadas (Next Best Actions).
    """
    if not response.next_best_actions:
        response.next_best_actions = resolve_next_actions(fact_payload, response, request)

    crud.append_turno_conversacion(db, request.session_id, "user", request.message)
    crud.append_turno_conversacion(
        db, request.session_id, "lucia", _texto_completo(response), response.intent_category
    )
    if response.requires_human_intervention:
        crud.update_historial(db, request.session_id, {"en_atencion_humana": True})
    return response


# Marcadores de que el cliente pregunta por una CAUSA o una VARIACIÓN, no por un
# dato puntual de su cuenta. Si aparecen, la consulta debe recorrer el pipeline
# completo de explicación, no el atajo de lectura directa.
#
# Sin este control, "¿por qué cambió el monto de mi plan?" caía en el atajo por
# contener "mi plan" y se respondía "tu plan es X", que no contesta la pregunta.
_MARCADORES_CAUSA = re.compile(
    r"\b(por\s*qu[eé]|porqu[eé]|raz[oó]n|motivo|caus[ao]|"
    r"cambi[oó]|cambio|vari[oó]|subi[oó]|sub[ií]|baj[oó]|"
    r"aument[oó]|increment[oó]|difer(?:encia|ente)|m[aá]s\s+car[oa]|"
    r"explic[aá]|no\s+entiendo)\b",
    re.IGNORECASE,
)


def _tipo_consulta_directa(message: str) -> Optional[str]:
    """
    Identifica consultas que se responden solo con un hecho verificado del
    recibo actual, sin pasar por RAG ni por el modelo.

    Solo aplica a preguntas por un DATO ("¿tengo deuda?", "¿qué plan tengo?").
    Una pregunta por la CAUSA de un cambio comparte vocabulario con estas pero
    necesita la explicación completa, así que se descarta primero.

    Nota: no hace falta excluir aquí "cancelar mi plan" ni similares — esas
    frases ya se capturan antes en intent_classifier.route() como
    SOLICITUD_SENSIBLE (máxima prioridad) y nunca llegan a este punto del
    pipeline de facturación.
    """
    if _MARCADORES_CAUSA.search(message):
        return None

    texto = message.lower()
    if any(termino in texto for termino in ("deuda", "deudas", "saldo pendiente", "pendiente de pago")):
        return "DEUDA"
    if "plan actual" in texto or "mi plan" in texto or "qué plan tengo" in texto or "que plan tengo" in texto:
        return "PLAN_ACTUAL"
    return None


# Estos hechos se leen directo de un campo de la base de datos, sin ninguna
# heurística de detección de evento de por medio: no hay ambigüedad sobre si
# "se reconoció el patrón" (que es lo que mide uncertainty_score). O el dato
# está en el recibo, o se declara explícitamente que no se puede verificar.
# Por eso NO reciben el uncertainty_score del motor de variación de recibo.
CONFIANZA_CONSULTA_DIRECTA_VERIFICADA = 99
CONFIANZA_CONSULTA_DIRECTA_SIN_DATO = 90


def _respuesta_consulta_directa(
    request: ChatRequest,
    fact_payload: Dict[str, Any],
    perfil_lexico: str,
) -> Optional[ChatResponse]:
    """
    Responde consultas de deuda y plan desde campos verificados del recibo.
    No delega estos hechos al LLM y nunca infiere saldo o plan ausente.
    """
    consulta = _tipo_consulta_directa(request.message)
    if consulta == "DEUDA":
        estado_deuda = fact_payload.get("estado_deuda")
        if estado_deuda:
            periodo = estado_deuda.get("periodo") or fact_payload["current_bill"]["issue_date"]
            if estado_deuda.get("estado") == "SIN_DEUDA":
                texto = f"Según tu factura del período {periodo}, no registras deuda pendiente."
            else:
                texto = (
                    f"Según tu factura del período {periodo}, el estado de deuda registrado es: "
                    f"{estado_deuda.get('valor')}."
                )
            confianza = CONFIANZA_CONSULTA_DIRECTA_VERIFICADA
        else:
            texto = "Tu recibo disponible no informa un estado de deuda verificable, así que no puedo confirmar un saldo pendiente."
            confianza = CONFIANZA_CONSULTA_DIRECTA_SIN_DATO

        return ChatResponse(
            session_id=request.session_id,
            intent_category="CONSULTA_DEUDA",
            sentiment_score=3,
            confidence_score=confianza,
            messages=[MessageChunk(text=texto, type="explanation")],
            personality_metadata=PersonalityMetadata(
                lucia_tone=persona.tono_para_metadata(perfil_lexico)
            ),
        )

    if consulta == "PLAN_ACTUAL":
        plan_actual = fact_payload.get("plan_actual")
        if plan_actual:
            texto = f"El plan identificado en tu recibo actual es: {plan_actual}."
            confianza = CONFIANZA_CONSULTA_DIRECTA_VERIFICADA
        else:
            texto = "Tu recibo disponible no permite identificar con certeza el nombre de tu plan actual."
            confianza = CONFIANZA_CONSULTA_DIRECTA_SIN_DATO

        return ChatResponse(
            session_id=request.session_id,
            intent_category="CONSULTA_PLAN_ACTUAL",
            sentiment_score=3,
            confidence_score=confianza,
            messages=[MessageChunk(text=texto, type="explanation")],
            personality_metadata=PersonalityMetadata(
                lucia_tone=persona.tono_para_metadata(perfil_lexico)
            ),
        )

    return None


def process_message(request: ChatRequest, db: Session) -> ChatResponse:
    """
    Orquesta el ciclo completo de vida de la petición.

    Solo los turnos de facturación atraviesan el motor determinista, el case
    matcher, el índice de incertidumbre y el RAG. Los turnos conversacionales se
    resuelven sin tocar datos de facturación.
    """
    started_at = time.monotonic()
    components_invoked = ["compliance_filter"]

    # Se lee la última actividad ANTES de tocar el historial: get_or_create_historial
    # puede hacer un commit (transferencia de session_id) que ya actualizaría
    # updated_at, perdiendo la señal de "cuánto tiempo pasó desde el turno anterior".
    ultima_actividad_previa = crud.peek_ultima_actividad(db, request.session_id, request.user_id)

    # Paso 1: Recepción, Carga de Estado y Memoria
    historial = crud.get_or_create_historial(db, request.session_id, request.user_id)
    comentarios = historial.comentarios_emocionales or []
    pending_emotions = [e for e in comentarios if not e.get("referenciado", False)]
    historial_conversacion = historial.historial_conversacion or []

    # Seguimiento de pendientes: solo tiene sentido si la sesión se RETOMÓ tras
    # una brecha real de inactividad, no en cada turno dentro de la misma
    # conversación activa. Sin este control, cualquier turno que deje
    # estado_resolucion=False (p. ej. un mes sin variación de recibo) hacía que
    # el turno inmediatamente siguiente preguntara "¿quedó resuelto lo anterior?",
    # aunque hubieran pasado 5 segundos.
    UMBRAL_SESION_RETOMADA_MINUTOS = 30
    pending_issue_followup = False
    if historial.estado_resolucion is False and len(historial_conversacion) > 0 and ultima_actividad_previa:
        minutos_inactivo = (datetime.utcnow() - ultima_actividad_previa).total_seconds() / 60
        if minutos_inactivo >= UMBRAL_SESION_RETOMADA_MINUTOS:
            pending_issue_followup = True

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

    # Continuidad de derivación sensible: si el último turno de Lucía ya derivó
    # por cancelación/portabilidad/nueva línea, un mensaje corto de seguimiento
    # ("para mí", "sí", "ok") no debe reclasificarse desde cero. Sin este check,
    # ese turno caía al pipeline de facturación (porque no repite las palabras
    # clave del patrón sensible) y el LLM real volvía a improvisar el proceso
    # comercial completo (pedir DNI, preguntar plan, etc.) en vez de simplemente
    # confirmar que ya está siendo gestionado por un asesor.
    #
    # IMPORTANTE: se acota a la MISMA sesión activa (< 30 min de inactividad),
    # igual que pending_issue_followup. El historial se vincula por user_id
    # (memoria de largo plazo), así que sin este límite un usuario que vuelve
    # días después con un mensaje sin señales de facturación quedaría atrapado
    # repitiendo la respuesta de una solicitud sensible ya vieja y resuelta.
    sesion_activa = bool(
        ultima_actividad_previa
        and (datetime.utcnow() - ultima_actividad_previa).total_seconds() / 60 < UMBRAL_SESION_RETOMADA_MINUTOS
    )
    ultimo_turno_lucia = next(
        (t for t in reversed(historial_conversacion) if t.get("role") == "lucia"), None
    )
    patron_en_gestion = (
        ultimo_turno_lucia.get("intent") if ultimo_turno_lucia else None
    )
    if sesion_activa and patron_en_gestion in PATRONES_SENSIBLES and not has_billing_signals(request.message):
        response = ChatResponse(
            session_id=request.session_id,
            intent_category=patron_en_gestion,
            sentiment_score=historial.score_sentimiento,
            requires_human_intervention=True,
            confidence_score=95,
            messages=[
                MessageChunk(
                    text="Ya quedó registrada tu solicitud y un asesor la está gestionando "
                         "con la información que me compartiste. En cuanto tenga novedades "
                         "te aviso, o si prefieres puedo derivarte ahora mismo. 🙏",
                    type="explanation",
                )
            ],
            personality_metadata=PersonalityMetadata(
                lucia_tone=persona.tono_para_metadata(historial.perfil_lexico_usuario)
            ),
        )
        _registrar_auditoria(
            db, request.session_id, started_at, response.intent_category, ["compliance_filter", "continuidad_sensible"],
            requires_human_intervention=True, confidence_score=response.confidence_score,
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

    # Solicitud estructural sensible (cancelación, portabilidad): entra al MISMO
    # banco de soluciones validadas por asesores que los eventos de facturación, en vez de
    # dejar que el LLM improvise un proceso no verificado.
    #
    # 1. Si ya hay una solución validada en base_casos para este patrón
    #    (un agente aprobó "CANCELACION_PLAN" antes), se reutiliza — confianza
    #    alta, sin derivar de nuevo.
    # 2. Si no, se deriva a un humano Y se registra en cuarentena con ese
    #    patrón. Así, cuando un agente la valide desde el panel, la próxima
    #    consulta de cancelación ya no vuelve a derivar.
    if decision.es_solicitud_sensible:
        components_invoked.append("case_matcher_sensible")
        caso_sensible = crud.get_caso_conocido(db, decision.patron_sensible)

        if caso_sensible:
            crud.increment_caso_aplicado(db, caso_sensible.id)
            solucion = caso_sensible.solucion_estructurada or {}
            texto = solucion.get("texto") or (
                "Ya tengo el proceso verificado para esto. Un asesor lo revisará "
                "contigo para completarlo con tus datos. 🙏"
            )
            response = ChatResponse(
                session_id=request.session_id,
                intent_category=decision.patron_sensible,
                sentiment_score=historial.score_sentimiento,
                requires_human_intervention=True,
                confidence_score=99,
                caso_validado=True,
                messages=[MessageChunk(text=texto, type="explanation")],
                personality_metadata=PersonalityMetadata(
                    lucia_tone=persona.tono_para_metadata(perfil_lexico)
                ),
                handoff_context=_build_handoff_context(
                    request, historial, historial_conversacion,
                    motivo=f"SOLICITUD_SENSIBLE_{decision.patron_sensible}"
                ),
            )
            _registrar_auditoria(
                db, request.session_id, started_at, response.intent_category, components_invoked,
                detected_event=decision.patron_sensible, requires_human_intervention=True,
                confidence_score=response.confidence_score, handoff_context=response.handoff_context,
            )
            return _finalizar(db, request, response)

        # Caso nuevo: se deriva Y se registra en cuarentena para que un agente
        # lo valide y quede disponible para la próxima consulta similar.
        reasons_sensible = ["Trámite sensible regulado: requiere validación formal por un asesor."]
        response = ChatResponse(
            session_id=request.session_id,
            intent_category=decision.patron_sensible,
            sentiment_score=historial.score_sentimiento,
            requires_human_intervention=True,
            confidence_score=80,
            confidence_reasons=reasons_sensible,
            caso_validado=False,
            messages=[
                MessageChunk(
                    text="Entiendo tu solicitud. Ya envié a tu asesor el expediente con todo el detalle de tu recibo y lo que acabamos de revisar, así que no vas a tener que repetir nada. En un momento un asesor continuará contigo directamente. 🙏",
                    type="explanation",
                )
            ],
            personality_metadata=PersonalityMetadata(
                lucia_tone=persona.tono_para_metadata(perfil_lexico)
            ),
            handoff_context=_build_handoff_context(
                request, historial, historial_conversacion,
                motivo=f"SOLICITUD_SENSIBLE_{decision.patron_sensible}",
                confidence_reasons=reasons_sensible,
                confidence_score=80,
                components_invoked=components_invoked,
            ),
        )
        register_new_case(
            db=db,
            session_id=request.session_id,
            fact_payload={"detected_event": decision.patron_sensible, "origen": "SOLICITUD_SENSIBLE", "user_message": request.message},
            solucion_propuesta={
                "intent_category": decision.patron_sensible,
                "messages": [m.model_dump() for m in response.messages],
            },
            uncertainty_score=0.5,
        )
        _registrar_auditoria(
            db, request.session_id, started_at, response.intent_category, components_invoked,
            detected_event=decision.patron_sensible, requires_human_intervention=True,
            confidence_score=response.confidence_score, handoff_context=response.handoff_context,
        )
        return _finalizar(db, request, response)

    # Solicitud explícita de agente humano: máxima prioridad, no se improvisa
    # ni se re-explica facturación. Se deriva de inmediato con contexto completo.
    if decision.es_solicitud_agente:
        reasons_agente = ["Solicitud explícita del cliente para comunicarse con un asesor."]
        response = ChatResponse(
            session_id=request.session_id,
            intent_category="SOLICITUD_AGENTE",
            sentiment_score=historial.score_sentimiento,
            requires_human_intervention=True,
            confidence_score=99,
            confidence_reasons=reasons_agente,
            messages=[
                MessageChunk(
                    text="Entendido. Ya le compartí al asesor el expediente con el detalle de tus recibos y lo que conversamos para que no tengas que repetir nada. Un agente humano continuará contigo de inmediato. 🙏",
                    type="explanation",
                )
            ],
            personality_metadata=PersonalityMetadata(
                lucia_tone=persona.tono_para_metadata(perfil_lexico)
            ),
            handoff_context=_build_handoff_context(
                request, historial, historial_conversacion,
                motivo="SOLICITUD_EXPLICITA_USUARIO",
                confidence_reasons=reasons_agente,
                confidence_score=99,
                components_invoked=components_invoked,
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

    # Detección de cuenta en el texto del mensaje para auto-asociar la sesión
    match_cuenta = re.search(
        r"\b(?:cuenta|mi\s+cuenta(?:\s+es)?|vincular(?:\s+cuenta)?|asociar|id|cliente|codigo|c[oó]digo)\s*[:=]?\s*([0-9]{6,12})\b",
        request.message,
        re.IGNORECASE
    )
    if not match_cuenta and re.fullmatch(r"[0-9]{6,12}", request.message.strip()):
        match_cuenta = re.search(r"([0-9]{6,12})", request.message.strip())

    if match_cuenta:
        cuenta_detectada = match_cuenta.group(1)
        if crud.verificar_existe_cuenta(db, cuenta_detectada):
            request.user_id = cuenta_detectada
            crud.update_historial(db, request.session_id, {"user_id": cuenta_detectada})

    fact_payload = calculate_billing_facts(request.user_id, db)

    # Manejo de usuarios visitantes (no clientes o sin cuenta identificada aún)
    if "error" in fact_payload:
        # 1. Si preguntan explícitamente por un recibo específico sin cuenta vinculada:
        pregunta_facturacion_personal = any(
            w in request.message.lower()
            for w in ["recibo", "factura", "mi cobro", "deuda", "por qué subió", "por que subio", "cuanto debo", "mi saldo", "desglose"]
        )
        if pregunta_facturacion_personal:
            response = ChatResponse(
                session_id=request.session_id,
                intent_category="CONSULTA_SIN_CUENTA",
                sentiment_score=3,
                confidence_score=95,
                messages=[
                    MessageChunk(
                        text="Para revisar el detalle y la variación de tus recibos específicos, por favor indícame tu número de cuenta financiera (ej. `102968745`) o selecciona una de nuestras cuentas de prueba. Si no eres cliente, dime qué planes te gustaría conocer. 😊",
                        type="explanation"
                    )
                ],
                personality_metadata=PersonalityMetadata(
                    lucia_tone=persona.tono_para_metadata(perfil_lexico)
                )
            )
            _registrar_auditoria(
                db, request.session_id, started_at, response.intent_category, components_invoked,
                confidence_score=response.confidence_score
            )
            return _finalizar(db, request, response)

        # 2. Si es una consulta informativa general (planes, servicios, fibra, etc.):
        rag_context = retrieve_context(request.message)
        response = generate_response(
            session_id=request.session_id,
            user_message=request.message,
            deterministic_payload={
                "moneda": "PEN",
                "simbolo_moneda": "S/",
                "current_bill": {"amount": 0.0, "issue_date": "N/A", "desglose": []},
                "variation_amount": 0.0,
                "detected_event": "CONSULTA_GENERAL_PLANES",
                "evidence": ["Consulta informativa de visitante sobre catálogo y servicios."]
            },
            rag_context=rag_context,
            cross_sell_eligible=False,
            pending_emotions=pending_emotions,
            perfil_lexico=perfil_lexico,
            historial_conversacion=historial_conversacion,
        )
        response.confidence_score = 95
        response.intent_category = "CONSULTA_GENERAL_PLANES"
        _registrar_auditoria(
            db, request.session_id, started_at, response.intent_category, components_invoked,
            confidence_score=response.confidence_score
        )
        return _finalizar(db, request, response, fact_payload)

    # Paso 3.5: Case Matcher — ¿existe una solución validada para este patrón o consulta?
    components_invoked.append("case_matcher")
    caso_match = match_caso(db, fact_payload, user_message=request.message)
    caso_id_origen = None  # Se usará para registrar feedback después

    if caso_match:
        caso_id_origen, solucion_conocida = caso_match
        # Inyectar la solución verificada al payload para que el LLM solo la personalice
        fact_payload["solucion_conocida"] = solucion_conocida
        fact_payload["caso_id"] = caso_id_origen

    # Paso 3.6: Índice de Incertidumbre Determinístico con Razones Explícitas
    components_invoked.append("uncertainty_calculator")
    uncertainty_score, confidence_reasons = calculate_uncertainty_with_reasons(
        fact_payload=fact_payload,
        caso_conocido=caso_match,
        rag_context=None,
        compliance_triggered=False
    )

    # Paso 3.7: Drill-Down Conversacional sobre cargos específicos
    cargos_actuales = fact_payload.get("cargos_actuales") or []
    cargos_pasados = fact_payload.get("cargos_pasados") or []
    cargo_especifico = buscar_cargo_especifico(cargos_actuales, cargos_pasados, request.message)
    if cargo_especifico:
        components_invoked.append("drill_down_lookup")
        texto_drill = (
            f"El cargo consultado corresponde a **{cargo_especifico['descripcion']}** "
            f"(Código oficial: `{cargo_especifico['codigo_cargo']}`) por un importe de "
            f"**{fact_payload.get('simbolo_moneda', 'S/')} {abs(cargo_especifico['monto']):.2f}** "
            f"facturado bajo la categoría de {cargo_especifico['etiqueta']}.\n\n"
            f"📌 **Motivo del cobro:** {cargo_especifico['motivo_negocio']}"
        )
        response = ChatResponse(
            session_id=request.session_id,
            intent_category="DRILL_DOWN_CARGO",
            sentiment_score=historial.score_sentimiento,
            confidence_score=99,
            confidence_reasons=["Concepto localizado y verificado directamente en el detalle de facturación."],
            messages=[
                MessageChunk(text="Revisando el detalle del cargo que me consultas:", type="hook"),
                MessageChunk(text=texto_drill, type="explanation"),
            ],
            personality_metadata=PersonalityMetadata(
                lucia_tone=persona.tono_para_metadata(perfil_lexico)
            ),
        )
        _adjuntar_desgloses(response, fact_payload)
        _registrar_auditoria(
            db, request.session_id, started_at, response.intent_category, components_invoked,
            detected_event=fact_payload.get("detected_event"), confidence_score=response.confidence_score,
            evidence=[f"{cargo_especifico['codigo_cargo']} - {cargo_especifico['descripcion']}"],
        )
        return _finalizar(db, request, response, fact_payload)

    # Paso 3.8: Límite duro anti-loop: si la sesión acumula intentos no resueltos o ambiguos
    es_loop_sin_resolver = _detectar_loop_sin_resolver(historial_conversacion, request.message)
    if es_loop_sin_resolver and (fact_payload.get("detected_event") in ("INCREMENTO_OTROS", "CONSULTA_GENERAL") or uncertainty_score >= 0.5):
        reasons_loop = ["Límite anti-loop de seguridad alcanzado: derivación proactiva con expediente preparado."]
        response = ChatResponse(
            session_id=request.session_id,
            intent_category="LIMITE_LOOP_DERIVACION_HUMANA",
            sentiment_score=historial.score_sentimiento,
            requires_human_intervention=True,
            confidence_score=75,
            confidence_reasons=reasons_loop,
            messages=[
                MessageChunk(
                    text="He revisado el detalle de tu factura, pero para asegurarme de que recibas una solución exacta y evitarte más demoras, ya transferí tu caso a un asesor especializado con todo el expediente de tu recibo y lo que conversamos para que no tengas que repetir nada. 🙏",
                    type="explanation"
                )
            ],
            personality_metadata=PersonalityMetadata(
                lucia_tone=persona.tono_para_metadata(perfil_lexico)
            ),
            handoff_context=_build_handoff_context(
                request, historial, historial_conversacion,
                motivo="LIMITE_LOOP_INTENTOS_EXCEDIDOS", fact_payload=fact_payload,
                confidence_reasons=reasons_loop, confidence_score=75,
                components_invoked=components_invoked,
            ),
        )
        _adjuntar_desgloses(response, fact_payload)
        _registrar_auditoria(
            db, request.session_id, started_at, response.intent_category, components_invoked,
            detected_event=fact_payload.get("detected_event"), requires_human_intervention=True,
            confidence_score=response.confidence_score, uncertainty_score=uncertainty_score,
            handoff_context=response.handoff_context,
        )
        return _finalizar(db, request, response, fact_payload)

    # Si la incertidumbre supera el umbral → handoff inmediato, con contexto completo
    if requires_handoff(uncertainty_score):
        conf_score_calc = int((1 - uncertainty_score) * 100)
        verbalizacion = persona.verbalizar_certeza_y_limites(
            fact_payload.get("detected_event", "INCREMENTO_OTROS"),
            conf_score_calc,
            confidence_reasons,
        )
        texto_handoff = (
            f"{verbalizacion}\n\n"
            f"Ya envié a tu asesor el expediente con todo el detalle de tu recibo y lo que acabamos "
            f"de revisar, así que no vas a tener que repetir nada. 🙏"
        )
        response = ChatResponse(
            session_id=request.session_id,
            intent_category="DERIVACION_INCERTIDUMBRE",
            sentiment_score=historial.score_sentimiento,
            requires_human_intervention=True,
            confidence_score=conf_score_calc,
            confidence_reasons=confidence_reasons,
            messages=[
                MessageChunk(
                    text=texto_handoff,
                    type="explanation"
                )
            ],
            personality_metadata=PersonalityMetadata(
                lucia_tone=persona.tono_para_metadata(perfil_lexico)
            ),
            handoff_context=_build_handoff_context(
                request, historial, historial_conversacion,
                motivo="INCERTIDUMBRE_ALTA", fact_payload=fact_payload,
                confidence_reasons=confidence_reasons, confidence_score=conf_score_calc,
                components_invoked=components_invoked,
            ),
        )
        _adjuntar_desgloses(response, fact_payload)
        # Sin este registro, cualquier caso que derivara por incertidumbre alta
        # (justo los casos genuinamente difíciles) desaparecía sin dejar rastro
        # en el panel de cuarentena. No hay banco de soluciones si el caso más
        # necesitado de revisión humana nunca llega a la cola de validación.
        eventos_ignorados = ("SIN_CAMBIOS", "NUEVO_CLIENTE", "CONSULTA_GENERAL")
        if not caso_match and fact_payload.get("detected_event") not in eventos_ignorados:
            solucion_serializada = {
                "intent_category": response.intent_category,
                "messages": [m.model_dump() for m in response.messages],
            }
            fact_payload_con_contexto = {**fact_payload, "user_message": request.message}
            register_new_case(
                db=db,
                session_id=request.session_id,
                fact_payload=fact_payload_con_contexto,
                solucion_propuesta=solucion_serializada,
                uncertainty_score=uncertainty_score,
            )
        _registrar_auditoria(
            db, request.session_id, started_at, response.intent_category, components_invoked,
            detected_event=fact_payload.get("detected_event"), requires_human_intervention=True,
            confidence_score=response.confidence_score, uncertainty_score=uncertainty_score,
            evidence=fact_payload.get("evidence"), handoff_context=response.handoff_context,
        )
        return _finalizar(db, request, response, fact_payload)

    # Consultas puntuales sobre deuda y plan: se responden directamente con
    # campos verificados del recibo, antes de consultar RAG o el LLM.
    # La derivación por ausencia de recibos ya ocurrió arriba, así que esta
    # rama nunca oculta falta de datos con una respuesta inventada.
    respuesta_directa = _respuesta_consulta_directa(
        request=request,
        fact_payload=fact_payload,
        perfil_lexico=perfil_lexico,
    )
    if respuesta_directa:
        components_invoked.append("verified_account_lookup")
        _adjuntar_desgloses(respuesta_directa, fact_payload)
        campo_verificado = (
            bool(fact_payload.get("estado_deuda"))
            if respuesta_directa.intent_category == "CONSULTA_DEUDA"
            else bool(fact_payload.get("plan_actual"))
        )
        crud.update_historial(
            db,
            request.session_id,
            {"estado_resolucion": campo_verificado},
        )
        respuesta_directa.confidence_reasons = confidence_reasons
        _registrar_auditoria(
            db, request.session_id, started_at, respuesta_directa.intent_category, components_invoked,
            detected_event=fact_payload.get("detected_event"),
            confidence_score=respuesta_directa.confidence_score,
            evidence=fact_payload.get("evidence"),
        )
        return _finalizar(db, request, respuesta_directa, fact_payload)

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
        recommended_plan = recommend_plan_upgrade(db, fact_payload.get("plan_charge_code"))
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
        pending_issue_followup=pending_issue_followup,
    )

    # Adjuntar el confidence_score (inverso de incertidumbre) y motivos verificables
    response.confidence_score = int((1 - uncertainty_score) * 100)
    response.confidence_reasons = confidence_reasons
    # Señal visible del diferenciador: ¿esta respuesta reutilizó conocimiento ya
    # validado por un humano/feedback, o se generó desde cero (caso nuevo)?
    response.caso_validado = caso_match is not None

    # Los desgloses y el historial se imponen desde el payload verificado.
    _adjuntar_desgloses(response, fact_payload)

    # La clasificación del turno es un hecho determinista, no una opinión del
    # modelo: si se deja la que devuelve el LLM aparecen etiquetas inventadas
    # ("consulta_cargo", "BILL_VARIATION_EXPLANATION"), que rompen el contrato de
    # la API y ensucian el audit_log. Se fuerza el evento detectado.
    response.intent_category = fact_payload.get("detected_event") or "CONSULTA_GENERAL"
    if response.personality_metadata and not response.personality_metadata.hook_used:
        response.personality_metadata.hook_used = _hook_used(response)

    # Paso 7: Validación y Actualización de Memoria
    updates = {
        "score_sentimiento": current_sentiment,
        "estado_resolucion": estado_resolucion,
    }

    # Los comentarios emocionales solo se reescriben si había alguno pendiente que
    # marcar como referenciado, y releyéndolos de la BD en ese momento. Escribir el
    # snapshot tomado al inicio del turno borraría el comentario que se acaba de
    # detectar en este mismo mensaje (add_comentario_emocional), que es justo lo
    # que hace que la memoria emocional persista entre turnos.
    if pending_emotions:
        ids_referenciados = {e.get("id") for e in pending_emotions}
        historial_actual = crud.get_or_create_historial(db, request.session_id, request.user_id)
        # Se construyen dicts NUEVOS en lugar de mutar los existentes: SQLAlchemy no
        # detecta mutaciones en sitio dentro de una columna JSON, así que modificar
        # los dicts cargados no generaría ningún UPDATE y el cambio se perdería.
        comentarios_actuales = []
        for emotion in (historial_actual.comentarios_emocionales or []):
            copia = dict(emotion)
            if copia.get("id") in ids_referenciados:
                copia["referenciado"] = True
                copia["reference_count"] = copia.get("reference_count", 0) + 1
            comentarios_actuales.append(copia)
        updates["comentarios_emocionales"] = comentarios_actuales

    crud.update_historial(db, request.session_id, updates)

    # Paso 7.5: Si era un caso nuevo (sin match), registrar en cuarentena.
    # Se incluye user_message en las evidencias para que en el panel se vea
    # qué preguntó el usuario, no solo el patrón técnico.
    eventos_ignorados = ("SIN_CAMBIOS", "NUEVO_CLIENTE", "CONSULTA_GENERAL")
    if not caso_match and fact_payload.get("detected_event") not in eventos_ignorados:
        solucion_serializada = {
            "intent_category": response.intent_category,
            "messages": [m.model_dump() for m in response.messages]
        }
        fact_payload_con_contexto = {**fact_payload, "user_message": request.message}
        register_new_case(
            db=db,
            session_id=request.session_id,
            fact_payload=fact_payload_con_contexto,
            solucion_propuesta=solucion_serializada,
            uncertainty_score=uncertainty_score
        )

    _registrar_auditoria(
        db, request.session_id, started_at, response.intent_category, components_invoked,
        detected_event=fact_payload.get("detected_event"), cross_sell_eligible=cross_sell_eligible,
        confidence_score=response.confidence_score, uncertainty_score=uncertainty_score,
        evidence=fact_payload.get("evidence"),
    )

    return _finalizar(db, request, response, fact_payload)
