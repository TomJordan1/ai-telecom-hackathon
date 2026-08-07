from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey, DateTime, JSON
from datetime import datetime
from app.db.database import Base

class ReciboCliente(Base):
    __tablename__ = "recibos_cliente"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True)
    mes_emision = Column(String) # e.g. '2026-07'
    monto_total = Column(Float)
    fecha_emision = Column(DateTime)
    conceptos_facturados = Column(JSON) # e.g. {"cargo_fijo": 99.90, "cuota_equipo": 20.00}
    plan_actual = Column(String)
    
class HistorialInteracciones(Base):
    __tablename__ = "historial_interacciones"
    
    session_id = Column(String, primary_key=True, index=True)
    user_id = Column(String, index=True)
    comentarios_emocionales = Column(JSON, default=list) 
    # [{"id": 1, "text": "...", "timestamp": "...", "importance": 3, "reference_count": 0, "expires_at": "...", "referenciado": False}]
    score_sentimiento = Column(Integer, default=3)
    perfil_lexico_usuario = Column(String, default="CASUAL") # FORMAL, CASUAL, USO_JERGAS
    estado_resolucion = Column(Boolean, default=False)
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
