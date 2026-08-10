import hashlib
import hmac
import json

from fastapi import APIRouter, Request, Response, Depends, BackgroundTasks
from sqlalchemy.orm import Session
from app.core.config import settings
from app.db import crud
from app.db.database import get_db
from app.services.orchestrator import process_message
from app.core.schemas import ChatRequest
from app.services.whatsapp_sender import process_and_send_whatsapp

router = APIRouter()

# Usuario de respaldo cuando el número entrante no está registrado en
# contactos_usuario. Permite que un jurado escriba desde cualquier celular y
# vea una demo coherente en lugar de un error.
USER_ID_FALLBACK = "user_a_fin_promo"


def _firma_valida(cuerpo: bytes, firma_header: str | None) -> bool:
    """
    Verifica el header X-Hub-Signature-256 que Meta firma con el App Secret.
    Sin esta validación, cualquiera que conozca la URL pública del webhook puede
    inyectar eventos falsos y hacer que Lucía escriba a los destinatarios.

    Si WHATSAPP_APP_SECRET no está configurado, no se bloquea (para no romper un
    entorno de demo ya funcionando), pero se avisa en el log.
    """
    if not settings.WHATSAPP_APP_SECRET:
        print("[WA WEBHOOK] AVISO: WHATSAPP_APP_SECRET no configurado, "
              "se acepta el evento sin verificar la firma de Meta.")
        return True

    if not firma_header or not firma_header.startswith("sha256="):
        return False

    esperada = hmac.new(
        settings.WHATSAPP_APP_SECRET.encode("utf-8"), cuerpo, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(esperada, firma_header.split("=", 1)[1])

@router.get("/webhook/whatsapp")
async def verify_webhook(request: Request):
    """
    Endpoint obligatorio de validación (Hub Challenge) de Meta.
    WhatsApp envía un GET a esta URL cuando configuras el Webhook.
    """
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode and token:
        if mode == "subscribe" and token == settings.WHATSAPP_VERIFY_TOKEN:
            # Meta requiere que devuelvas el challenge como texto plano directamente
            return Response(content=challenge, status_code=200, media_type="text/plain")
    return Response(content="Forbidden", status_code=403)

@router.post("/webhook/whatsapp")
async def receive_whatsapp_message(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Recibe los mensajes de texto entrantes desde WhatsApp, los envía al orquestador,
    y programa el envío de las respuestas en segundo plano para no bloquear a Meta.
    """
    try:
        # Se lee el cuerpo crudo porque la firma se calcula sobre los bytes
        # exactos que envió Meta; re-serializar el JSON la invalidaría.
        cuerpo = await request.body()
        if not _firma_valida(cuerpo, request.headers.get("X-Hub-Signature-256")):
            print("[WA WEBHOOK] Firma inválida: evento descartado.")
            return Response(content="INVALID_SIGNATURE", status_code=403)

        body = json.loads(cuerpo)
        print(f"[WA WEBHOOK] Body recibido: {body}")
        
        # Parsear el JSON crudo que envía Meta
        if body.get("object"):
            for entry in body.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    messages = value.get("messages", [])
                    
                    # Meta envía notificaciones de status (delivered, read) que no son mensajes
                    if not messages:
                        print(f"[WA WEBHOOK] No hay mensajes en este evento (probablemente un status update)")
                        continue
                    
                    if messages:
                        message = messages[0]
                        phone_number = message["from"]
                        print(f"[WA WEBHOOK] Mensaje de {phone_number}: tipo={message['type']}")
                        
                        # Extraer el texto o el botón presionado
                        user_text = ""
                        if message["type"] == "text":
                            user_text = message["text"]["body"]
                        elif message["type"] == "interactive":
                            interactive = message["interactive"]
                            if interactive["type"] == "button_reply":
                                user_text = interactive["button_reply"]["title"]
                                
                        if user_text:
                            print(f"[WA WEBHOOK] Texto extraido: '{user_text}' -> procesando...")

                            # El número entrante se resuelve contra contactos_usuario:
                            # cada cliente ve sus propios recibos, no los de un mock fijo.
                            user_id = crud.get_user_id_por_whatsapp(db, phone_number)
                            if user_id:
                                print(f"[WA WEBHOOK] Numero {phone_number} -> user_id={user_id}")
                            else:
                                user_id = USER_ID_FALLBACK
                                print(f"[WA WEBHOOK] Numero {phone_number} no registrado en "
                                      f"contactos_usuario, se usa fallback user_id={user_id}")

                            chat_request = ChatRequest(
                                session_id=f"wa_{phone_number}",
                                user_id=user_id,
                                message=user_text,
                                channel="whatsapp"
                            )
                            
                            # 2. Orquestar (Pasar por Determinismo, RAG, y LLM)
                            chat_response = process_message(chat_request, db)
                            print(f"[WA WEBHOOK] Respuesta generada: {len(chat_response.messages)} chunks")
                            
                            # 3. Enviar la respuesta vía WhatsApp de forma asíncrona (Background Task)
                            background_tasks.add_task(process_and_send_whatsapp, phone_number, chat_response)
                            print(f"[WA WEBHOOK] Tarea de envio programada para {phone_number}")
                        else:
                            print(f"[WA WEBHOOK] No se pudo extraer texto del mensaje tipo={message['type']}")
                            
        return Response(content="EVENT_RECEIVED", status_code=200)
        
    except Exception as e:
        import traceback
        print(f"[WA WEBHOOK ERROR] {e}")
        traceback.print_exc()
        return Response(content="ERROR", status_code=500)
