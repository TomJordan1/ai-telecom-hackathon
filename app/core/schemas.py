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
    change_reason: Optional[str] = None

class UpcomingAlert(BaseModel):
    concepto: str
    fecha_fin: str
    impacto_estimado: str
    tipo: str
    dias_restantes: int

class RecommendedPlan(BaseModel):
    nombre: str
    precio: float
    beneficios: str

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
    upcoming_alerts: List[UpcomingAlert] = []
    plan_optimizer_suggestion: PlanOptimizerSuggestion = PlanOptimizerSuggestion()
    personality_metadata: PersonalityMetadata = PersonalityMetadata()
    handoff_context: Optional[Any] = None
    confidence_score: int = Field(99, ge=0, le=100)
    compliance_triggered: bool = False
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
