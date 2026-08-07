from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.schemas import ChatRequest, ChatResponse
from app.db.database import get_db
from app.services.orchestrator import process_message

router = APIRouter()

@router.post("/chat", response_model=ChatResponse)
async def process_chat(request: ChatRequest, db: Session = Depends(get_db)):
    try:
        response = process_message(request, db)
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
