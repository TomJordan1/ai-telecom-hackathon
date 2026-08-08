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

# --- Base de Casos ---

def get_caso_conocido(db: Session, patron_problema: str):
    """Busca una solución validada para un patrón dado."""
    return db.query(models.BaseCasos).filter(
        models.BaseCasos.patron_problema == patron_problema,
        models.BaseCasos.activo == True
    ).first()

def increment_caso_aplicado(db: Session, caso_id: str):
    caso = db.query(models.BaseCasos).filter(models.BaseCasos.id == caso_id).first()
    if caso:
        caso.veces_aplicado += 1
        db.commit()

def create_caso_base(db: Session, data: dict):
    caso = models.BaseCasos(**data)
    db.add(caso)
    db.commit()
    db.refresh(caso)
    return caso

# --- Cuarentena de Casos ---

def create_caso_cuarentena(db: Session, data: dict):
    caso = models.CuarentenaCasos(**data)
    db.add(caso)
    db.commit()
    db.refresh(caso)
    return caso

def get_caso_cuarentena(db: Session, caso_id: str):
    return db.query(models.CuarentenaCasos).filter(models.CuarentenaCasos.id == caso_id).first()

def get_cuarentena_pendiente(db: Session):
    return db.query(models.CuarentenaCasos).filter(
        models.CuarentenaCasos.estado_validacion == "PENDIENTE"
    ).all()

def update_caso_cuarentena(db: Session, caso_id: str, updates: dict):
    caso = db.query(models.CuarentenaCasos).filter(models.CuarentenaCasos.id == caso_id).first()
    if caso:
        for key, value in updates.items():
            setattr(caso, key, value)
        db.commit()
        db.refresh(caso)
    return caso

def promover_caso_a_base(db: Session, caso_id: str, validado_por: str = "AGENTE_MOVISTAR"):
    """Mueve un caso aprobado de cuarentena a base_casos."""
    caso = get_caso_cuarentena(db, caso_id)
    if not caso:
        return None
    nuevo_caso_base = models.BaseCasos(
        patron_problema=caso.patron_detectado,
        condiciones=caso.evidencias,
        solucion_estructurada=caso.solucion_propuesta,
        validado_por=validado_por
    )
    db.add(nuevo_caso_base)
    caso.estado_validacion = "APROBADO"
    db.commit()
    db.refresh(nuevo_caso_base)
    return nuevo_caso_base

