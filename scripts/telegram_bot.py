import os
import asyncio
import requests
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ChatAction

# Cargar variables de entorno
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
API_URL = "http://127.0.0.1:8000/api/v1/chat"

# Por defecto, fingiremos ser el usuario A para probar el flujo de fin de promoción
# En un sistema real, mapearíamos update.effective_user.id a una cuenta en DB.
MOCK_USER_ID = "user_a_fin_promo"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "¡Hola! Soy Lucía, tu Copiloto de Facturación.\n"
        "Escríbeme cualquier consulta sobre tu recibo."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    session_id = str(update.effective_chat.id) # Usamos el ID del chat como sesión
    
    # Notificar que el bot está escribiendo
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    
    # 1. Enviar petición a nuestra API
    payload = {
        "session_id": session_id,
        "user_id": MOCK_USER_ID,
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
        # Si es evidencia, lo formateamos bonito
        if msg.get("type") == "evidence":
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

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "buy_yes":
        await query.edit_message_text(text="¡Genial! Hemos iniciado la activación de tu nuevo plan. (Simulación)")
    else:
        await query.edit_message_text(text="Entendido, mantendremos tu plan actual.")

def main():
    if not TOKEN:
        print("Error: Por favor, define TELEGRAM_TOKEN en tu archivo .env")
        return
        
    print("Iniciando Bot de Telegram...")
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    from telegram.ext import CallbackQueryHandler
    app.add_handler(CallbackQueryHandler(button_callback))
    
    print("Bot en ejecución. Presiona Ctrl+C para detener.")
    app.run_polling()

if __name__ == "__main__":
    main()
