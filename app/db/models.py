from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, JSON
import uuid
from datetime import datetime
from app.db.database import Base

class ContactoUsuario(Base):
    """
    Mapeo entre el user_id interno y sus canales de contacto reales.
    Necesario para las alertas proactivas: Lucía no puede escribirle primero
    a un usuario si no sabe a qué número de WhatsApp o chat de Telegram enviarle.
    En este mock, se puebla manualmente; en producción vendría del CRM/BrainyBill.
    """
    __tablename__ = "contactos_usuario"

    user_id = Column(String, primary_key=True, index=True)
    whatsapp_number = Column(String, nullable=True)
    telegram_chat_id = Column(String, nullable=True)


class MensajeProcesado(Base):
    """
    Registro de idempotencia para los mensajes entrantes de canales externos.

    WhatsApp Cloud API reintenta la entrega del mismo evento cuando el webhook
    no responde 200 con rapidez (o ante cualquier error de red), y cada reintento
    llega con el mismo message_id (wamid). Sin este registro, un único "hola" del
    usuario se procesaba y se respondía varias veces.
    """
    __tablename__ = "mensajes_procesados"

    message_id = Column(String, primary_key=True, index=True)
    canal = Column(String, default="whatsapp")
    recibido_at = Column(DateTime, default=datetime.utcnow, index=True)


class ReciboCliente(Base):
    __tablename__ = "recibos_cliente"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True)
    mes_emision = Column(String) # e.g. '2026-07'
    monto_total = Column(Float)
    fecha_emision = Column(DateTime)
    conceptos_facturados = Column(JSON) # e.g. {"cargo_fijo": 99.90, "cuota_equipo": 20.00}
    plan_actual = Column(String, nullable=True)
    
class HistorialInteracciones(Base):
    __tablename__ = "historial_interacciones"
    
    session_id = Column(String, primary_key=True, index=True)
    user_id = Column(String, index=True)
    comentarios_emocionales = Column(JSON, default=list) 
    # [{"id": 1, "text": "...", "timestamp": "...", "importance": 3, "reference_count": 0, "expires_at": "...", "referenciado": False}]
    score_sentimiento = Column(Integer, default=3)
    perfil_lexico_usuario = Column(String, default="CASUAL") # FORMAL, CASUAL, USO_JERGAS
    estado_resolucion = Column(Boolean, default=False)
    historial_conversacion = Column(JSON, default=list)
    # Bitácora acotada de turnos recientes (no todo el historial, solo lo necesario
    # para dar continuidad): [{"role": "user"|"lucia", "text": "...", "intent": "..."}]
    # Sin esto el modelo no tiene forma de saber qué ya explicó en la sesión.
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class CatalogoPlanes(Base):
    __tablename__ = "catalogo_planes"
    
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String)
    precio = Column(Float)
    beneficios = Column(String)
    activo = Column(Boolean, default=True)

class TerminosRestringidos(Base):
    __tablename__ = "terminos_restringidos"
    
    id = Column(Integer, primary_key=True, index=True)
    patron_regex = Column(String)
    accion_disparador = Column(String) # LEGAL_RIESGO, INSULTO, DATOS_SENSIBLES
    mensaje_bloqueo = Column(String)

class BaseCasos(Base):
    """
    Conocimiento validado y reutilizable.
    Solo los casos que han pasado por cuarentena y fueron aprobados llegan aquí.
    """
    __tablename__ = "base_casos"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    patron_problema = Column(String, index=True) # e.g. 'FIN_PROMOCION'
    condiciones = Column(JSON)  # Firma determinista del caso
    solucion_estructurada = Column(JSON)  # Qué decir, qué evidencia mostrar, qué ofrecer
    veces_aplicado = Column(Integer, default=0)
    tasa_exito = Column(Float, default=0.0) # % de follow-ups positivos
    fecha_validacion = Column(DateTime, default=datetime.utcnow)
    validado_por = Column(String, default="SISTEMA") # SISTEMA | AGENTE_MOVISTAR
    activo = Column(Boolean, default=True)

class CuarentenaCasos(Base):
    """
    Sala de espera para casos nuevos que el sistema no reconoció.
    Requieren validación antes de convertirse en conocimiento confiable.
    """
    __tablename__ = "cuarentena_casos"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, index=True)
    patron_detectado = Column(String)
    evidencias = Column(JSON)   # Deterministic Fact Payload completo
    solucion_propuesta = Column(JSON)  # Lo que Lucía respondió
    feedback_inmediato = Column(String, default="SIN_RESPUESTA")  # POSITIVO | NEGATIVO | SIN_RESPUESTA
    feedback_posterior = Column(String, default="PENDIENTE")  # SOLUCIONADO | NO_SOLUCIONADO | PENDIENTE
    fecha_consulta = Column(DateTime, default=datetime.utcnow)
    fecha_followup = Column(DateTime, nullable=True)
    estado_validacion = Column(String, default="PENDIENTE")  # PENDIENTE | APROBADO | RECHAZADO
    incertidumbre_score = Column(Float, default=0.5)


class AuditLog(Base):
    """
    Registro estructurado de cada decisión del orquestador: qué se detectó,
    qué componentes se invocaron, qué se decidió y cuánto tardó. Permite
    reconstruir el flujo completo para auditoría, tal como exige el diseño
    original. No almacena el texto de las respuestas, solo metadatos de decisión.
    """
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    intent_category = Column(String, nullable=True)
    detected_event = Column(String, nullable=True)
    compliance_triggered = Column(Boolean, default=False)
    requires_human_intervention = Column(Boolean, default=False)
    cross_sell_eligible = Column(Boolean, default=False)
    confidence_score = Column(Integer, nullable=True)
    uncertainty_score = Column(Float, nullable=True)
    components_invoked = Column(JSON, default=list)
    evidence = Column(JSON, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    # Contexto de derivación (solo se llena cuando requires_human_intervention=True).
    # Permite construir la cola de atención humana sin reconstruir todo el flujo.
    handoff_context = Column(JSON, nullable=True)
    atendido = Column(Boolean, default=False)
