from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from app.db import crud

FOLLOWUP_DAYS = 3  # Días después para preguntar si el problema se resolvió

def register_new_case(
    db: Session,
    session_id: str,
    fact_payload: dict,
    solucion_propuesta: dict,
    uncertainty_score: float,
    folio: str = None,
) -> tuple:
    """
    Registra un caso nuevo en cuarentena cuando el sistema no encontró
    un patrón conocido o la incertidumbre es alta.
    Retorna (caso_id, folio).
    """
    if not folio:
        folio = crud.generar_folio()

    caso = crud.create_caso_cuarentena(db, {
        "session_id": session_id,
        "folio": folio,
        "patron_detectado": fact_payload.get("detected_event", "DESCONOCIDO"),
        "evidencias": fact_payload,
        "solucion_propuesta": solucion_propuesta,
        "fecha_followup": datetime.utcnow() + timedelta(days=FOLLOWUP_DAYS),
        "incertidumbre_score": uncertainty_score
    })
    return caso.id, caso.folio

def register_feedback(db: Session, caso_id: str, tipo: str, es_posterior: bool = False):
    """
    Registra el feedback del usuario (inmediato o posterior al follow-up).
    tipo: 'POSITIVO' | 'NEGATIVO'
    """
    if es_posterior:
        valor = "SOLUCIONADO" if tipo == "POSITIVO" else "NO_SOLUCIONADO"
        crud.update_caso_cuarentena(db, caso_id, {"feedback_posterior": valor})

        # Evaluar si el caso merece ser promovido a base_casos
        caso = crud.get_caso_cuarentena(db, caso_id)
        if caso and valor == "SOLUCIONADO" and caso.feedback_inmediato == "POSITIVO":
            crud.promover_caso_a_base(db, caso_id, validado_por="SISTEMA")
    else:
        crud.update_caso_cuarentena(db, caso_id, {"feedback_inmediato": tipo})

def get_followup_message(patron: str) -> str:
    """
    Genera el mensaje de seguimiento apropiado según el tipo de problema.
    """
    mensajes = {
        "FIN_PROMOCION": "¡Hola! Hace unos días revisamos juntos el aumento en tu recibo por el fin de tu promoción. ¿Todo quedó claro o tienes alguna duda adicional?",
        "PRORRATEO_CAMBIO_PLAN": "¡Hola! Te escribo para saber si el cobro proporcional de tu cambio de plan quedó bien explicado. ¿Todo en orden?",
        "CUOTA_EQUIPO": "¡Hola! Revisamos juntos la cuota de tu equipo hace unos días. ¿Sigue todo correcto en tu recibo?",
    }
    return mensajes.get(patron, "¡Hola! Hace unos días resolvimos una consulta juntos. ¿El problema quedó resuelto o necesitas algo más?")
