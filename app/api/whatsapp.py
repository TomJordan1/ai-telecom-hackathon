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
from app.db.database import SessionLocal

router = APIRouter()

# No dependemos de un fallback fijo. Si el usuario no está registrado,
# se creará una sesión temporal.
# La cuenta de respaldo se resuelve contra la base real (crud.get_cuenta_demo):
# se elige una cuenta financiera con historial suficiente para explicar una
# variación. Así un jurado puede escribir desde cualquier celular y ver una
# demo coherente, sin depender de un identificador escrito en el código.


def _extraer_texto(message: dict) -> str:
    """Devuelve el texto del mensaje o el título del botón pulsado."""
    tipo = message.get("type")
    if tipo == "text":
        return message.get("text", {}).get("body", "")
    if tipo == "interactive":
        interactive = message.get("interactive", {})
        if interactive.get("type") == "button_reply":
            return interactive.get("button_reply", {}).get("title", "")
    return ""


def _detectar_intencion_vinculacion(user_text: str) -> str | None:
    """
    Detecta si el mensaje es una instrucción de vinculación o un número de cuenta suelto.
    Retorna el string de la cuenta encontrada o None.
    """
    import re
    texto = user_text.strip()
    # 1. Patrones explícitos: 'cuenta 102968745', 'mi cuenta es 102968745', 'vincular 102968745'
    match_explicito = re.search(
        r"\b(?:cuenta|mi\s+cuenta(?:\s+es)?|vincular(?:\s+cuenta)?|asociar|id|cliente|codigo|c[oó]digo)\s*[:=]?\s*([0-9]{6,12})\b",
        texto,
        re.IGNORECASE
    )
    if match_explicito:
        return match_explicito.group(1)

    # 2. Solo dígitos (el usuario escribió únicamente el número de cuenta de 6 a 12 dígitos)
    if re.fullmatch(r"[0-9]{6,12}", texto):
        return texto

    return None


def _procesar_y_responder(phone_number: str, user_text: str, message_id: str):
    """
    Orquesta la respuesta y la envía, ya fuera del ciclo de vida de la petición
    de Meta. Se ejecuta en background con su propia sesión de BD: la sesión
    inyectada por Depends(get_db) ya está cerrada cuando corre esta tarea.
    """
    from app.services.whatsapp_sender import send_whatsapp_text
    from app.services.deterministic import calculate_billing_facts

    db = SessionLocal()
    try:
        # 1. Comprobar si el mensaje es una solicitud de vinculación de cuenta
        cuenta_candidata = _detectar_intencion_vinculacion(user_text)
        if cuenta_candidata:
            if crud.verificar_existe_cuenta(db, cuenta_candidata):
                crud.upsert_contacto_usuario(db, user_id=cuenta_candidata, whatsapp_number=phone_number)
                fact_payload = calculate_billing_facts(cuenta_candidata, db)
                plan_nom = fact_payload.get("plan_actual") or "Plan Movistar"
                current_bill = fact_payload.get("current_bill") or {}
                monto = current_bill.get("amount", 0.0)
                fecha = current_bill.get("issue_date", "")

                msg_exito = (
                    f"✅ *¡Cuenta Vinculada con Éxito!*\n\n"
                    f"He asociado tu WhatsApp (+{phone_number}) a tu cuenta financiera *{cuenta_candidata}*.\n"
                    f"📱 *Plan actual:* {plan_nom}\n"
                    f"📄 *Último recibo ({fecha}):* S/ {monto:.2f}\n\n"
                    f"A partir de ahora, cuando me escribas consultaré automáticamente tus recibos y te avisaré proactivamente de vencimientos de promociones. ✨\n\n"
                    f"¿En qué te puedo ayudar hoy con tu recibo?"
                )
                send_whatsapp_text(phone_number, msg_exito)
                print(f"[WA WEBHOOK] Cuenta {cuenta_candidata} vinculada a WhatsApp {phone_number}")
                return
            else:
                msg_error = (
                    f"⚠️ No encontré la cuenta financiera *{cuenta_candidata}* en la base de datos.\n\n"
                    f"Por favor verifica los dígitos de tu cuenta financiera (aparece en la cabecera de tu recibo o en la App Mi Movistar) y vuelve a escribirla (ej: *cuenta 102968745*)."
                )
                send_whatsapp_text(phone_number, msg_error)
                print(f"[WA WEBHOOK] Intento de vinculación fallido: cuenta {cuenta_candidata} no existe.")
                return

        # 2. El número entrante se resuelve contra contactos_usuario:
        user_id = crud.get_user_id_por_whatsapp(db, phone_number)
        es_visitante = False

        if user_id:
            print(f"[WA WEBHOOK] Numero {phone_number} -> cuenta vinculada={user_id}")
        else:
            es_visitante = True
            texto_limpio = user_text.lower().strip()
            es_saludo = any(
                texto_limpio.startswith(saludo) or texto_limpio == saludo
                for saludo in ["hola", "buenas", "buenos dias", "buenas tardes", "buenas noches", "inicio", "empezar", "menu", "hi", "hey"]
            )

            # Si es saludo inicial de un usuario no vinculado, pedir número de cuenta o consulta
            if es_saludo:
                msg_bienvenida = (
                    "¡Hola! 👋 Soy *Alonza*, tu copiloto de facturación y servicios de Movistar.\n\n"
                    "Para consultar tus recibos, revisar cobros y recibir alertas sobre tu línea, "
                    "por favor envíame tu *número de cuenta financiera* (ejemplo: *cuenta 102968745* o solo los dígitos *102968745*).\n\n"
                    "Si aún no eres cliente y deseas conocer nuestros planes de internet fibra o telefonía móvil, "
                    "puedes escribirme tu consulta directamente. 😊"
                )
                send_whatsapp_text(phone_number, msg_bienvenida)
                print(f"[WA WEBHOOK] Saludo y solicitud de cuenta enviada a visitante {phone_number}")
                return

            # Para consultas informativas de no clientes, usar sesión de invitado
            user_id = f"invitado_{phone_number}"
            print(f"[WA WEBHOOK] Numero {phone_number} no registrado, procesando como visitante.")

        session_id = f"wa_{phone_number}"

        # Intercepción: Si un humano está atendiendo, guardar el mensaje y no responder.
        if crud.is_en_atencion_humana(db, session_id):
            print(f"[WA WEBHOOK] Sesión {session_id} en atención humana. Mensaje interceptado.")
            crud.append_turno_conversacion(db, session_id, "user", user_text)
            return

        chat_request = ChatRequest(
            session_id=session_id,
            user_id=user_id,
            message=user_text,
            channel="whatsapp",
        )

        chat_response = process_message(chat_request, db)
        print(f"[WA WEBHOOK] Respuesta generada para {message_id}: "
              f"{len(chat_response.messages)} chunks")

        process_and_send_whatsapp(phone_number, chat_response)
        print(f"[WA WEBHOOK] Respuesta enviada a {phone_number}")

        # Si es visitante y preguntó por catálogo, agregar tip de vinculación
        if es_visitante and len(chat_response.messages) > 0:
            tip = (
                "💡 *Tip:* Cuando tengas tu cuenta Movistar a mano, envíala con: *cuenta <número>* "
                "para activar tus alertas y consultar tus recibos."
            )
            send_whatsapp_text(phone_number, tip)


    except Exception as e:
        import traceback
        print(f"[WA WEBHOOK ERROR] Fallo procesando {message_id}: {e}")
        traceback.print_exc()
    finally:
        db.close()


def _firma_valida(cuerpo: bytes, firma_header: str | None) -> bool:
    """
    Verifica el header X-Hub-Signature-256 que Meta firma con el App Secret.
    Sin esta validación, cualquiera que conozca la URL pública del webhook puede
    inyectar eventos falsos y hacer que Alonza escriba a los destinatarios.

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
    Recibe los mensajes entrantes desde WhatsApp y delega TODO el procesamiento a
    una tarea en segundo plano, devolviendo 200 de inmediato.

    Dos protecciones contra respuestas duplicadas:
    1. Idempotencia por message_id (wamid): si Meta reentrega el mismo evento, se
       descarta en lugar de volver a responder.
    2. Respuesta 200 inmediata: orquestar y enviar tarda varios segundos (LLM +
       delays de tipeo); si eso ocurre antes del 200, Meta considera el webhook
       caído y reintenta, lo que generaba respuestas repetidas.
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
                    
                    for message in messages:
                        phone_number = message.get("from")
                        message_id = message.get("id", "")
                        print(f"[WA WEBHOOK] Mensaje de {phone_number}: "
                              f"tipo={message.get('type')} id={message_id}")

                        user_text = _extraer_texto(message)
                        if not user_text:
                            print(f"[WA WEBHOOK] No se pudo extraer texto del mensaje "
                                  f"tipo={message.get('type')}")
                            continue

                        # Idempotencia: el mismo wamid solo se atiende una vez, sin
                        # importar cuántas veces Meta reentregue el evento.
                        if not crud.reclamar_mensaje_entrante(db, message_id, canal="whatsapp"):
                            print(f"[WA WEBHOOK] Mensaje {message_id} ya procesado: "
                                  f"reentrega descartada (sin respuesta duplicada).")
                            continue

                        print(f"[WA WEBHOOK] Texto extraido: '{user_text}' -> encolando...")
                        # Orquestación y envío ocurren fuera de la petición para
                        # devolver 200 antes de que Meta agote su timeout.
                        background_tasks.add_task(
                            _procesar_y_responder, phone_number, user_text, message_id
                        )
                        print(f"[WA WEBHOOK] Tarea programada para {phone_number}")

        return Response(content="EVENT_RECEIVED", status_code=200)
        
    except Exception as e:
        import traceback
        print(f"[WA WEBHOOK ERROR] {e}")
        traceback.print_exc()
        return Response(content="ERROR", status_code=500)

