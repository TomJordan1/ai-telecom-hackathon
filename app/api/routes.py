from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.schemas import ChatRequest, ChatResponse
from app.db.database import get_db
from app.services.orchestrator import process_message

router = APIRouter()

@router.post("/chat", response_model=ChatResponse)
async def process_chat(request: ChatRequest, db: Session = Depends(get_db)):
    from app.db import crud
    try:
        # Intercepción si está en atención humana
        if crud.is_en_atencion_humana(db, request.session_id):
            print(f"[WEB CHAT] Sesión {request.session_id} en atención humana. Mensaje interceptado.")
            crud.append_turno_conversacion(db, request.session_id, "user", request.message)
            return ChatResponse(
                session_id=request.session_id,
                intent_category="AGENTE_HUMANO",
                sentiment_score=3,
                messages=[],
                en_atencion_humana=True
            )
            
        response = process_message(request, db)
        
        # In case process_message triggers handoff, ensure we set en_atencion_humana=True
        if response.requires_human_intervention:
            response.en_atencion_humana = True
            
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
