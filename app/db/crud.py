from sqlalchemy.orm import Session
from app.db import models

def get_recibos_by_user(db: Session, user_id: str, limit: int = 6):
    return db.query(models.ReciboCliente)\
             .filter(models.ReciboCliente.user_id == user_id)\
             .order_by(models.ReciboCliente.fecha_emision.desc())\
             .limit(limit)\
             .all()

def get_or_create_historial(db: Session, session_id: str, user_id: str):
    historial = db.query(models.HistorialInteracciones).filter(models.HistorialInteracciones.session_id == session_id).first()
    if not historial:
        historial = models.HistorialInteracciones(session_id=session_id, user_id=user_id)
        db.add(historial)
        db.commit()
        db.refresh(historial)
    return historial

def update_historial(db: Session, session_id: str, updates: dict):
    historial = db.query(models.HistorialInteracciones).filter(models.HistorialInteracciones.session_id == session_id).first()
    if historial:
        for key, value in updates.items():
            setattr(historial, key, value)
        db.commit()
        db.refresh(historial)
    return historial

def get_terminos_restringidos(db: Session):
    return db.query(models.TerminosRestringidos).all()

def get_catalogo_planes(db: Session):
    return db.query(models.CatalogoPlanes).filter(models.CatalogoPlanes.activo == True).all()
