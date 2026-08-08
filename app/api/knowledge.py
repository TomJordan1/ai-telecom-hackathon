from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.db.database import get_db
from app.db import crud
from app.services.feedback_handler import register_feedback, get_followup_message

router = APIRouter(prefix="/api/v1", tags=["Feedback & Admin"])

# --- Schemas ---

class FeedbackRequest(BaseModel):
    session_id: str
    caso_id: str
    tipo: str  # POSITIVO | NEGATIVO
    es_posterior: bool = False

class ValidarCasoRequest(BaseModel):
    validado_por: Optional[str] = "AGENTE_MOVISTAR"

# --- Endpoints de Feedback ---

@router.post("/feedback")
def submit_feedback(request: FeedbackRequest, db: Session = Depends(get_db)):
    """
    Recibe el feedback del usuario (👍 / 👎), inmediato o posterior.
    Si es posterior y positivo, el sistema puede promover el caso a base_casos.
    """
    if request.tipo not in ("POSITIVO", "NEGATIVO"):
        raise HTTPException(status_code=400, detail="tipo debe ser POSITIVO o NEGATIVO")

    register_feedback(db, request.caso_id, request.tipo, request.es_posterior)
    return {"status": "ok", "mensaje": "Feedback registrado exitosamente."}

@router.post("/followup/{caso_id}")
def trigger_followup(caso_id: str, db: Session = Depends(get_db)):
    """
    Endpoint para disparar el mensaje de seguimiento.
    En producción, sería invocado por un cron job o scheduler externo.
    """
    caso = crud.get_caso_cuarentena(db, caso_id)
    if not caso:
        raise HTTPException(status_code=404, detail="Caso no encontrado")

    mensaje = get_followup_message(caso.patron_detectado)
    # En producción aquí se enviaría por el canal original (WhatsApp/Telegram/Web)
    return {
        "status": "ok",
        "mensaje_followup": mensaje,
        "caso_id": caso_id,
        "patron": caso.patron_detectado,
        "nota": "En producción este mensaje se enviará automáticamente al canal de origen."
    }

# --- Endpoints de Administración (Panel Movistar) ---

@router.get("/admin/cuarentena")
def list_cuarentena(db: Session = Depends(get_db)):
    """Lista todos los casos en cuarentena pendientes de validación."""
    casos = crud.get_cuarentena_pendiente(db)
    return {
        "total": len(casos),
        "casos": [
            {
                "id": c.id,
                "patron": c.patron_detectado,
                "incertidumbre": c.incertidumbre_score,
                "feedback_inmediato": c.feedback_inmediato,
                "feedback_posterior": c.feedback_posterior,
                "fecha": c.fecha_consulta.isoformat() if c.fecha_consulta else None,
                "fecha_followup": c.fecha_followup.isoformat() if c.fecha_followup else None,
            }
            for c in casos
        ]
    }

@router.post("/admin/validar/{caso_id}")
def validate_case(caso_id: str, request: ValidarCasoRequest, db: Session = Depends(get_db)):
    """
    Promueve un caso de cuarentena a base_casos (conocimiento validado).
    Solo accesible por personal de Movistar.
    """
    nuevo_caso = crud.promover_caso_a_base(db, caso_id, request.validado_por)
    if not nuevo_caso:
        raise HTTPException(status_code=404, detail="Caso no encontrado en cuarentena")
    return {
        "status": "ok",
        "mensaje": f"Caso promovido exitosamente a base_casos.",
        "nuevo_caso_id": nuevo_caso.id,
        "patron": nuevo_caso.patron_problema
    }

@router.get("/admin/base-casos")
def list_base_casos(db: Session = Depends(get_db)):
    """Lista todos los casos validados en la base de conocimiento."""
    from app.db.models import BaseCasos
    casos = db.query(BaseCasos).filter(BaseCasos.activo == True).all()
    return {
        "total": len(casos),
        "casos": [
            {
                "id": c.id,
                "patron": c.patron_problema,
                "veces_aplicado": c.veces_aplicado,
                "tasa_exito": c.tasa_exito,
                "validado_por": c.validado_por,
                "fecha_validacion": c.fecha_validacion.isoformat() if c.fecha_validacion else None
            }
            for c in casos
        ]
    }
