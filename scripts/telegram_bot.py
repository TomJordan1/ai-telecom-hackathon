import os
import re
import asyncio
import requests
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ChatAction

# Cargar variables de entorno
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
API_BASE = os.getenv("LUCIA_API_BASE", "http://127.0.0.1:8000/api/v1")
API_URL = f"{API_BASE}/chat"
CUENTA_DEMO_URL = f"{API_BASE}/cuenta-demo"

# Cuenta financiera con la que este bot consulta la API.
CUENTA_DEMO = os.getenv("DEMO_ACCOUNT_ID")


def resolver_cuenta_demo() -> str | None:
    """Pregunta al backend qué cuenta usar si no viene fijada por entorno."""
    global CUENTA_DEMO
    if CUENTA_DEMO:
        return CUENTA_DEMO
    try:
        respuesta = requests.get(CUENTA_DEMO_URL, timeout=10)
        respuesta.raise_for_status()
        CUENTA_DEMO = respuesta.json().get("cuenta_financiera")
    except Exception as e:
        print(f"[TELEGRAM] No se pudo resolver la cuenta de demostración: {e}")
        return None
    return CUENTA_DEMO

def _es_detalle_facturacion(text: str) -> bool:
    """
    Verifica que el texto realmente sea un desglose o detalle estructurado
    de pagos, recibos o planes antes de anteponer '📊 *Detalle:*'.
    """
    texto_lower = text.lower().strip()
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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    custom_keyboard = [
        [KeyboardButton("👤 Hablar con un asesor"), KeyboardButton("📊 ¿Por qué varió mi recibo?")]
    ]
    reply_markup = ReplyKeyboardMarkup(custom_keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "¡Hola! Soy Lucía, tu Copiloto de Facturación.\n"
        "Escríbeme cualquier consulta sobre tu recibo o usa los accesos directos:",
        reply_markup=reply_markup
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    session_id = str(update.effective_chat.id) # Usamos el ID del chat como sesión
    
    # Notificar que el bot está escribiendo
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    
    cuenta = resolver_cuenta_demo()
    if not cuenta:
        await update.message.reply_text(
            "No pude identificar una cuenta con datos de facturación. "
            "Verifica que el backend esté encendido y que se haya ejecutado "
            "scripts/ingest_real_data.py."
        )
        return

    # 1. Enviar petición a nuestra API
    payload = {
        "session_id": session_id,
        "user_id": cuenta,
        "message": user_text,
        "channel": "telegram"
    }
    
    try:
        response = requests.post(API_URL, json=payload)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        await update.message.reply_text(f"Error al contactar a la API local. ¿Está encendido el servidor? ({e})")
        return

    # 2. Procesar los mensajes en orden
    for msg in data.get("messages", []):
        delay = msg.get("delay_ms", 0) / 1000.0
        if delay > 0:
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
            await asyncio.sleep(delay)
            
        text = msg.get("text", "")
        # Si es evidencia real de facturación/pago/plan, lo formateamos con Detalle
        if msg.get("type") == "evidence" and _es_detalle_facturacion(text):
            text = f"📊 *Detalle:*\n`{text}`"
            
        await update.message.reply_text(text, parse_mode="Markdown")
        
    # 3. Procesar sugerencias de venta cruzada (Upsell)
    suggestion = data.get("plan_optimizer_suggestion", {})
    if suggestion.get("available"):
        await asyncio.sleep(1)
        plan = suggestion.get("plan_recomendado", {})
        
        texto_upsell = (
            f"✨ *Sugerencia Comercial*\n"
            f"{suggestion.get('mensaje_comercial')}\n\n"
            f"*{plan.get('nombre')}* por solo S/ {plan.get('precio')}\n"
            f"_{plan.get('beneficios')}_"
        )
        
        # Crear botones nativos
        keyboard = [
            [InlineKeyboardButton("¡Me interesa!", callback_data="buy_yes")],
            [InlineKeyboardButton("No, gracias", callback_data="buy_no")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(texto_upsell, reply_markup=reply_markup, parse_mode="Markdown")
    elif data.get("next_best_actions"):
        # 4. Siguientes mejores acciones recomendadas (Next Best Actions)
        keyboard = []
        for action in data.get("next_best_actions", []):
            keyboard.append([InlineKeyboardButton(action["titulo"], callback_data=f"nba_{action['id']}")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("💡 *Acciones sugeridas:*", reply_markup=reply_markup, parse_mode="Markdown")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cb_data = query.data or ""
    if cb_data == "buy_yes":
        await query.edit_message_text(text="¡Genial! Hemos iniciado la activación de tu nuevo plan. (Simulación)")
    elif cb_data == "buy_no":
        await query.edit_message_text(text="Entendido, mantendremos tu plan actual.")
    elif cb_data.startswith("nba_"):
        action_id = cb_data.replace("nba_", "")
        if action_id == "CANAL_CHAT":
            await query.message.reply_text("💬 Tu asesor continuará contigo directamente por este chat con todo el expediente preparado.")
        elif action_id == "CANAL_LLAMADA":
            await query.message.reply_text("📞 Un asesor se comunicará contigo vía llamada telefónica con todo el detalle de tu recibo.")
        elif action_id == "CANAL_WHATSAPP":
            await query.message.reply_text("📲 Un asesor te contactará al WhatsApp con todo el contexto de tu consulta.")
        elif action_id == "PAY_BILL":
            await query.message.reply_text("💳 Redirigiendo a la pasarela de pagos seguros de Movistar (Yape/Plin/Tarjeta). ¡Pago registrado en simulación!")
        elif action_id == "VIEW_BREAKDOWN":
            await query.message.reply_text("📊 Puedes consultar el desglose completo de tus conceptos en la App Mi Movistar o pedirme 'ver desglose'.")
        elif action_id == "HANDOFF_AGENT":
            await query.message.reply_text("👤 Te estamos transfiriendo con un asesor humano con el historial completo de tu consulta...")
        elif action_id == "EXPLORE_PLANS":
            await query.message.reply_text("📱 Conoce nuestros planes en www.movistar.com.pe o pregúntame '¿qué planes tienes?'.")
        elif action_id == "REGISTER_RESOLVED":
            await query.message.reply_text("😊 ¡Me alegra haberte ayudado! Que tengas un excelente día.")
        else:
            await query.message.reply_text("Opción seleccionada.")

def main():
    if not TOKEN:
        print("Error: Por favor, define TELEGRAM_TOKEN en tu archivo .env")
        return
        
    print("Iniciando Bot de Telegram...")
    cuenta = resolver_cuenta_demo()
    if cuenta:
        print(f"Cuenta financiera de demostración: {cuenta}")
    else:
        print("AVISO: no se pudo resolver la cuenta de demostración todavía. "
              "Se reintentará en el primer mensaje.")

    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    from telegram.ext import CallbackQueryHandler
    app.add_handler(CallbackQueryHandler(button_callback))
    
    print("Bot en ejecución. Presiona Ctrl+C para detener.")
    app.run_polling()

if __name__ == "__main__":
    main()
