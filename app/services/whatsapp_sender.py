import requests
import time
from app.core.config import settings
from app.core.schemas import ChatResponse

def send_whatsapp_text(to_number: str, text: str):
    """Envía un mensaje de texto simple vía WhatsApp Cloud API."""
    if not settings.WHATSAPP_TOKEN or not settings.WHATSAPP_PHONE_ID:
        print(f"[MOCK WA] A {to_number}: {text}")
        return

    url = f"https://graph.facebook.com/v17.0/{settings.WHATSAPP_PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": text}
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
    except Exception as e:
        print(f"Error enviando WhatsApp: {e}")

def send_whatsapp_interactive(to_number: str, text: str, buttons: list):
    """Envía un mensaje con botones interactivos vía WhatsApp Cloud API."""
    if not settings.WHATSAPP_TOKEN or not settings.WHATSAPP_PHONE_ID:
        print(f"[MOCK WA] A {to_number}: {text} [Botones: {buttons}]")
        return

    url = f"https://graph.facebook.com/v17.0/{settings.WHATSAPP_PHONE_ID}/messages"
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

    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
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
        if msg.type == "evidence":
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
