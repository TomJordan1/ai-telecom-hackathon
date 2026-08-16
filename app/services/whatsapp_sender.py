import re
import requests
import time
from app.core.config import settings
from app.core.schemas import ChatResponse
from app.services.image_renderer import select_and_render_visual

def _endpoint_mensajes() -> str:
    """
    URL del endpoint de envío. La versión de la Graph API es configurable porque
    Meta retira las versiones antiguas y una versión deprecada devuelve error.
    """
    return (
        f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}"
        f"/{settings.WHATSAPP_PHONE_ID}/messages"
    )


def _endpoint_media() -> str:
    return (
        f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}"
        f"/{settings.WHATSAPP_PHONE_ID}/media"
    )


def send_whatsapp_text(to_number: str, text: str):
    """Envía un mensaje de texto simple vía WhatsApp Cloud API."""
    if not settings.WHATSAPP_TOKEN or not settings.WHATSAPP_PHONE_ID:
        print(f"[MOCK WA] A {to_number}: {text}")
        return

    clean_number = "".join(filter(str.isdigit, str(to_number)))
    url = _endpoint_mensajes()
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": clean_number,
        "type": "text",
        "text": {"body": text}
    }

    
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
    except Exception as e:
        print(f"Error enviando WhatsApp: {e}")
        try:
            print(f"  Detalle de Meta: {response.text}")
        except Exception:
            pass

def send_whatsapp_interactive(to_number: str, text: str, buttons: list):
    """Envía un mensaje con botones interactivos vía WhatsApp Cloud API."""
    if not settings.WHATSAPP_TOKEN or not settings.WHATSAPP_PHONE_ID:
        print(f"[MOCK WA] A {to_number}: {text} [Botones: {buttons}]")
        return

    url = _endpoint_mensajes()
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    
    # Formatear botones para Meta API
    wa_buttons = []
    for btn in buttons:
        wa_buttons.append({
            "type": "reply",
            "reply": {
                "id": btn["id"],
                "title": btn["title"][:20] # Límite de 20 caracteres de WhatsApp
            }
        })

    clean_number = "".join(filter(str.isdigit, str(to_number)))
    payload = {
        "messaging_product": "whatsapp",
        "to": clean_number,
        "type": "interactive",
        "interactive": {

            "type": "button",
            "body": {"text": text},
            "action": {"buttons": wa_buttons}
        }
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
    except Exception as e:
        print(f"Error enviando WhatsApp interactivo: {e}")


def _upload_media(image_bytes: bytes, filename: str = "recibo.png", mime_type: str = "image/png") -> str | None:
    """
    Sube el binario de la imagen directamente a Meta (endpoint /media) y
    devuelve el media_id para referenciarlo al enviar el mensaje.

    Se elige subir el binario en vez de alojar la imagen en una URL pública
    propia: así no hace falta exponer una carpeta estática nueva ni depender
    de que el túnel (ngrok) sirva también archivos, solo el webhook.
    """
    if not settings.WHATSAPP_TOKEN or not settings.WHATSAPP_PHONE_ID:
        print(f"[MOCK WA] Subiendo imagen simulada ({len(image_bytes)} bytes, {filename})")
        return "mock_media_id"

    url = _endpoint_media()
    headers = {"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}"}
    files = {"file": (filename, image_bytes, mime_type)}
    data = {"messaging_product": "whatsapp", "type": mime_type}

    try:
        response = requests.post(url, headers=headers, files=files, data=data)
        response.raise_for_status()
        return response.json().get("id")
    except Exception as e:
        print(f"Error subiendo imagen a WhatsApp: {e}")
        try:
            print(f"  Detalle de Meta: {response.text}")
        except Exception:
            pass
        return None


def send_whatsapp_image(to_number: str, image_bytes: bytes, caption: str | None = None):
    """Sube una imagen PNG (bytes) y la envía como mensaje de imagen."""
    media_id = _upload_media(image_bytes)
    if not media_id:
        return

    if media_id == "mock_media_id":
        print(f"[MOCK WA] Imagen enviada a {to_number} (media_id={media_id}). Caption: {caption}")
        return

    clean_number = "".join(filter(str.isdigit, str(to_number)))
    url = _endpoint_mensajes()
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    image_payload = {"id": media_id}
    if caption:
        image_payload["caption"] = caption

    payload = {
        "messaging_product": "whatsapp",
        "to": clean_number,
        "type": "image",
        "image": image_payload,
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
    except Exception as e:
        print(f"Error enviando imagen por WhatsApp: {e}")
        try:
            print(f"  Detalle de Meta: {response.text}")
        except Exception:
            pass


def _es_detalle_facturacion(text: str) -> bool:
    """
    Verifica que el texto realmente sea un desglose o detalle estructurado
    de pagos, recibos o planes antes de anteponer '📊 *Detalle:*'.
    Evita que conclusiones o textos conversacionales queden formateados como detalle.
    """
    texto_lower = text.lower().strip()
    # Frases de cierre conversacional no son detalle
    if any(p in texto_lower for p in [
        "así que", "asi que", "tranqui", "aquí estoy", "aqui estoy",
        "si necesitas", "cualquier duda", "cualquier consulta",
        "estoy para ayudarte", "espero haberte", "que tengas",
        "nada más que pagar", "nada mas que pagar"
    ]):
        return False

    tiene_monto = bool(re.search(r"S/\.?\s*\d+|\b\d+(?:[\.,]\d{2})?\b", text))
    tiene_concepto = any(k in texto_lower for k in [
        "recibo", "factura", "cargo", "cuota", "plan", "desglose",
        "descuento", "deuda", "monto", "total", "saldo"
    ])
    return tiene_monto and tiene_concepto


def process_and_send_whatsapp(to_number: str, chat_response: ChatResponse):
    """
    Desempaca la respuesta unificada del orquestador y la envía a WhatsApp
    respetando los tiempos y aplicando formato de negritas.
    """
    for msg in chat_response.messages:
        delay_sec = msg.delay_ms / 1000.0
        if delay_sec > 0:
            time.sleep(delay_sec)
            
        text = msg.text
        if msg.type == "evidence" and _es_detalle_facturacion(text):
            # WhatsApp usa asteriscos para negritas
            text = f"📊 *Detalle:*\n```{text}```"
            
        send_whatsapp_text(to_number, text)

    # Infografía de apoyo (a lo más una por turno, misma prioridad que en
    # visuals.js): variación del recibo > desglose por categoría > histórico.
    # Se envuelve en try/except a propósito: si el render falla, la
    # conversación de texto ya enviada arriba no debe verse interrumpida.
    try:
        imagen = select_and_render_visual(chat_response)
        if imagen:
            time.sleep(0.6)
            send_whatsapp_image(to_number, imagen)
    except Exception as e:
        print(f"[WA VISUAL] No se pudo generar/enviar la infografía: {e}")
        
    # 1. Sugerencia comercial (Upsell) convertida a botones de WhatsApp
    suggestion = chat_response.plan_optimizer_suggestion
    if suggestion and suggestion.available and suggestion.plan_recomendado:
        time.sleep(1)
        plan = suggestion.plan_recomendado
        texto_upsell = (
            f"✨ *Sugerencia Comercial*\n"
            f"{suggestion.mensaje_comercial}\n\n"
            f"*{plan.nombre}* por solo S/ {plan.precio}\n"
            f"_{plan.beneficios}_"
        )
        
        botones = [
            {"id": "buy_yes", "title": "¡Me interesa!"},
            {"id": "buy_no", "title": "No, gracias"}
        ]
        send_whatsapp_interactive(to_number, texto_upsell, botones)
    elif chat_response.next_best_actions:
        # 2. Siguientes mejores acciones recomendadas (Next Best Actions)
        # WhatsApp permite un máximo de 3 botones interactivos de respuesta rápida
        wa_botones = []
        for action in chat_response.next_best_actions[:3]:
            titulo_btn = action.titulo
            if len(titulo_btn) > 20:
                if action.id == "PAY_BILL":
                    titulo_btn = "💳 Pagar recibo"
                elif action.id == "VIEW_BREAKDOWN":
                    titulo_btn = "📊 Ver desglose"
                elif action.id == "HANDOFF_AGENT":
                    titulo_btn = "👤 Hablar con asesor"
                elif action.id == "EXPLORE_PLANS":
                    titulo_btn = "✨ Ver planes"
                elif action.id == "REGISTER_RESOLVED":
                    titulo_btn = "✅ Todo claro"
                elif action.id == "VINCULAR_CUENTA":
                    titulo_btn = "🔑 Vincular cuenta"
                else:
                    titulo_btn = titulo_btn[:20]
            wa_botones.append({"id": f"action_{action.id.lower()}", "title": titulo_btn[:20]})

        if wa_botones:
            time.sleep(1)
            send_whatsapp_interactive(to_number, "💡 *Siguientes acciones sugeridas:*", wa_botones)
