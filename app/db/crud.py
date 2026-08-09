from sqlalchemy.orm import Session
from app.db import models

def get_recibos_by_user(db: Session, user_id: str, limit: int = 6):
    return db.query(models.ReciboCliente)\
             .filter(models.ReciboCliente.user_id == user_id)\
             .order_by(models.ReciboCliente.fecha_emision.desc())\
             .limit(limit)\
             .all()

def get_all_user_ids(db: Session):
    """Lista todos los user_id distintos que tienen al menos un recibo."""
    rows = db.query(models.ReciboCliente.user_id).distinct().all()
    return [r[0] for r in rows]


def get_contacto_usuario(db: Session, user_id: str):
    return db.query(models.ContactoUsuario).filter(models.ContactoUsuario.user_id == user_id).first()


def upsert_contacto_usuario(db: Session, user_id: str, whatsapp_number: str = None, telegram_chat_id: str = None):
    contacto = get_contacto_usuario(db, user_id)
    if not contacto:
        contacto = models.ContactoUsuario(user_id=user_id)
        db.add(contacto)
    if whatsapp_number is not None:
        contacto.whatsapp_number = whatsapp_number
    if telegram_chat_id is not None:
        contacto.telegram_chat_id = telegram_chat_id
    db.commit()
    db.refresh(contacto)
    return contacto


def get_or_create_historial(db: Session, session_id: str, user_id: str):
    historial = db.query(models.HistorialInteracciones).filter(models.HistorialInteracciones.session_id == session_id).first()
    if not historial:
        historial = models.HistorialInteracciones(session_id=session_id, user_id=user_id)
        db.add(historial)
        db.commit()
        db.refresh(historial)
    return historial

EMOTIONAL_COMMENT_TTL_DAYS = 14  # expiración: no crecer el contexto indefinidamente
MAX_COMENTARIOS_EMOCIONALES = 5  # consolidación: solo se conservan los más recientes


def add_comentario_emocional(db: Session, session_id: str, text: str, importance: int = 3):
    """
    Registra una nueva frase emocional detectada en el mensaje del usuario.
    Es la contraparte de creación que faltaba: hasta ahora solo se leían y
    marcaban como referenciados comentarios existentes, nunca se generaban.
    Aplica expiración (TTL) y un tope de cantidad para evitar crecimiento
    indefinido del contexto, tal como especifica el diseño original.
    """
    from datetime import datetime, timedelta

    historial = db.query(models.HistorialInteracciones).filter(
        models.HistorialInteracciones.session_id == session_id
    ).first()
    if not historial:
        return None

    ahora = datetime.utcnow()
    comentarios = list(historial.comentarios_emocionales or [])

    # Consolidación: descartar los ya vencidos antes de agregar uno nuevo.
    vigentes = []
    for c in comentarios:
        expira = c.get("expires_at")
        try:
            if expira and datetime.fromisoformat(expira) < ahora:
                continue
        except ValueError:
            pass
        vigentes.append(c)

    nuevo_id = (max((c.get("id", 0) for c in vigentes), default=0)) + 1
    vigentes.append({
        "id": nuevo_id,
        "text": text,
        "timestamp": ahora.isoformat(),
        "importance": importance,
        "reference_count": 0,
        "expires_at": (ahora + timedelta(days=EMOTIONAL_COMMENT_TTL_DAYS)).isoformat(),
        "referenciado": False,
    })

    # Resumen/consolidación: si se excede el tope, se conservan los más recientes.
    if len(vigentes) > MAX_COMENTARIOS_EMOCIONALES:
        vigentes = vigentes[-MAX_COMENTARIOS_EMOCIONALES:]

    historial.comentarios_emocionales = vigentes
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

MAX_TURNOS_HISTORIAL = 12  # ~6 intercambios usuario/Lucía; suficiente para dar
                           # continuidad sin acumular contexto indefinidamente.


def append_turno_conversacion(db: Session, session_id: str, role: str, text: str, intent: str = ""):
    """
    Añade un turno a la bitácora acotada de la sesión y recorta al límite.
    role: 'user' | 'lucia'.
    """
    historial = db.query(models.HistorialInteracciones).filter(
        models.HistorialInteracciones.session_id == session_id
    ).first()
    if not historial:
        return None

    turnos = list(historial.historial_conversacion or [])
    turnos.append({"role": role, "text": text, "intent": intent})
    if len(turnos) > MAX_TURNOS_HISTORIAL:
        turnos = turnos[-MAX_TURNOS_HISTORIAL:]

    historial.historial_conversacion = turnos
    db.commit()
    db.refresh(historial)
    return historial


def get_terminos_restringidos(db: Session):
    return db.query(models.TerminosRestringidos).all()

def get_catalogo_planes(db: Session):
    return db.query(models.CatalogoPlanes).filter(models.CatalogoPlanes.activo == True).all()

# --- Observabilidad ---

def create_audit_log(db: Session, **kwargs):
    """
    Registra una decisión del orquestador. Nunca debe bloquear el flujo
    principal si falla: la observabilidad es secundaria a responder al usuario.
    """
    log = models.AuditLog(**kwargs)
    db.add(log)
    db.commit()
    return log


def get_handoff_queue(db: Session, solo_pendientes: bool = True):
    """
    Lista los turnos que requirieron intervención humana, con el contexto
    empaquetado para que un agente pueda continuar sin pedirle al cliente
    que repita todo. Es la cola de atención del panel de administración.
    """
    query = db.query(models.AuditLog).filter(models.AuditLog.requires_human_intervention == True)
    if solo_pendientes:
        query = query.filter(models.AuditLog.atendido == False)
    return query.order_by(models.AuditLog.timestamp.desc()).all()


def marcar_handoff_atendido(db: Session, audit_log_id: int):
    entrada = db.query(models.AuditLog).filter(models.AuditLog.id == audit_log_id).first()
    if entrada:
        entrada.atendido = True
        db.commit()
        db.refresh(entrada)
    return entrada

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

