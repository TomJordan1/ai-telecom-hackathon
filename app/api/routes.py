from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.schemas import ChatRequest, ChatResponse
from app.db.database import get_db
from app.services.orchestrator import process_message
import traceback
import random

router = APIRouter()

# Probabilidad de ejecutar la purga lazy en cada request (1/50 = 2%).
# Cubre los días en que el servidor no se reinicia sin añadir latencia
# perceptible: la purga es una sola query DELETE y se ejecuta de forma
# síncrona pero no bloquea porque tarda < 5 ms en condiciones normales.
_PURGA_LAZY_PROBABILIDAD = 1 / 50


@router.post("/chat", response_model=ChatResponse)
async def process_chat(request: ChatRequest, db: Session = Depends(get_db)):
    from app.db import crud

    # Purga lazy: ~2% de los requests eliminan sesiones de visitantes caducadas.
    # Se ejecuta ANTES del try principal para no enmascarar errores de negocio,
    # y su propio try/except garantiza que un fallo aquí nunca rompa el chat.
    if random.random() < _PURGA_LAZY_PROBABILIDAD:
        try:
            crud.purgar_sesiones_visitantes_caducadas(db)
        except Exception as purga_err:
            print(f"[PURGA LAZY] Error no crítico, se omite: {purga_err}")

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
        print(f"[WEB CHAT ERROR] {type(e).__name__}: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

