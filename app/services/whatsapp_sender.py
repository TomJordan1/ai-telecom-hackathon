import re
import requests
import time
from app.core.config import settings
from app.core.schemas import ChatResponse

def _endpoint_mensajes() -> str:
    """
    URL del endpoint de envío. La versión de la Graph API es configurable porque
    Meta retira las versiones antiguas y una versión deprecada devuelve error.
    """
    return (
        f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}"
        f"/{settings.WHATSAPP_PHONE_ID}/messages"
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
        
    # Sugerencia comercial (Upsell) convertida a botones de WhatsApp
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
