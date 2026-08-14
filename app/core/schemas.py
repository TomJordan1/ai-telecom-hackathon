from pydantic import BaseModel, Field
from typing import List, Optional, Any
from datetime import datetime

# --- Request Models ---

class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Unique identifier for the session/conversation")
    user_id: str = Field(..., description="Unique identifier for the user/client")
    message: str = Field(..., description="The message sent by the user")
    channel: Optional[str] = Field("web", description="The channel origin (web, whatsapp, app)")

# --- Response Models ---

class MessageChunk(BaseModel):
    text: str
    delay_ms: int = 0
    type: str = Field(..., description="hook, explanation, or evidence")

class BillSummary(BaseModel):
    month: str
    amount: float
    ciclo: Optional[str] = Field(
        None, description="Ciclo de facturación real (YYYYMMDD) del que proviene el monto"
    )
    change_reason: Optional[str] = None


class ChargeBreakdownItem(BaseModel):
    """
    Una línea del desglose del recibo por categoría de cargo. Responde a la
    pregunta '¿qué me están cobrando?' sin exponer los códigos internos de
    facturación: `etiqueta` ya viene en lenguaje cliente y `conceptos` cita las
    descripciones reales de los cargos que componen el monto.
    """
    categoria: str
    etiqueta: str
    monto: float
    conceptos: List[str] = []


class VariationBreakdownItem(BaseModel):
    """
    Aporte de una categoría a la variación entre el recibo actual y el previo.
    La suma de los `impacto` de todos los ítems reproduce exactamente la
    variación total, así que la explicación queda respaldada al céntimo.
    """
    categoria: str
    etiqueta: str
    monto_actual: float
    monto_anterior: float
    impacto: float
    conceptos: List[str] = []


class BillingAdjustments(BaseModel):
    """Notas de crédito y débito emitidas en el ciclo consultado."""
    cantidad: int = 0
    total_notas_credito: float = 0.0
    total_notas_debito: float = 0.0

class UpcomingAlert(BaseModel):
    concepto: str
    fecha_fin: str
    impacto_estimado: str
    tipo: str
    # El dataset declara la duración del beneficio en meses (dentro de la
    # descripción del cargo), no una fecha de corte exacta. Por eso el aviso se
    # expresa en ciclos facturados frente a la duración pactada, y `dias_restantes`
    # queda como opcional: se informa solo si existe una fecha real de corte.
    dias_restantes: Optional[int] = None
    duracion_pactada_meses: Optional[int] = None
    ciclos_facturados: Optional[int] = None

class RecommendedPlan(BaseModel):
    nombre: str
    precio: float
    beneficios: str
    motivo: Optional[str] = Field(
        None,
        description="Criterio verificable por el que se considera una mejora: "
                    "MAS_CAPACIDAD (más GB por igual o menor tarifa) o "
                    "MENOR_TARIFA (misma modalidad de renta a menor precio).",
    )

class PlanOptimizerSuggestion(BaseModel):
    available: bool = False
    mensaje_comercial: Optional[str] = None
    plan_recomendado: Optional[RecommendedPlan] = None

class PersonalityMetadata(BaseModel):
    hook_used: Optional[str] = None
    lucia_tone: str = "EMPATICA_Y_CLARA"

class ChatResponse(BaseModel):
    session_id: str
    intent_category: str
    requires_human_intervention: bool = False
    sentiment_score: int = Field(..., ge=1, le=5)
    messages: List[MessageChunk]
    historical_bills_summary: List[BillSummary] = []
    # Desgloses deterministas: los rellena el orquestador a partir del payload
    # verificado, nunca el LLM. Permiten a la App mostrar el detalle del recibo
    # y el porqué de la variación sin volver a consultar el backend.
    current_bill_breakdown: List[ChargeBreakdownItem] = []
    variation_breakdown: List[VariationBreakdownItem] = []
    billing_adjustments: Optional[BillingAdjustments] = None
    upcoming_alerts: List[UpcomingAlert] = []
    plan_optimizer_suggestion: PlanOptimizerSuggestion = PlanOptimizerSuggestion()
    personality_metadata: PersonalityMetadata = PersonalityMetadata()
    handoff_context: Optional[Any] = None
    confidence_score: int = Field(99, ge=0, le=100)
    caso_validado: bool = Field(
        False,
        description="True si la respuesta reutilizó una solución ya validada en base_casos, "
                    "en vez de generarse desde cero. Es la señal visible del ciclo de aprendizaje."
    )
    compliance_triggered: bool = False
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
