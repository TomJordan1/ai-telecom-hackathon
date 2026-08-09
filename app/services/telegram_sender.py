"""
Envío saliente a Telegram desde el backend (fuera del ciclo de polling del
bot en scripts/telegram_bot.py). Se usa para las alertas proactivas: Lucía
necesita poder escribirle primero a un chat_id sin que el usuario haya
enviado un mensaje. Se implementa vía HTTP directo a la Bot API para no
acoplar el backend al loop de 'python-telegram-bot'.
"""

import requests
from app.core.config import settings


def send_telegram_text(chat_id: str, text: str):
    """Envía un mensaje de texto simple vía la API de Telegram."""
    if not settings.TELEGRAM_TOKEN:
        print(f"[MOCK TELEGRAM] A {chat_id}: {text}")
        return

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}

    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
    except Exception as e:
        print(f"Error enviando Telegram: {e}")
        try:
            print(f"  Detalle: {response.text}")
        except Exception:
            pass
