"""
Alertas proactivas: Lucía le escribe primero al usuario cuando detecta que
una promoción está por vencer, en vez de esperar a que el usuario pregunte.

Reutiliza toda la lógica determinista ya existente (calculate_billing_facts,
_calculate_upcoming_alerts vía el payload) — no se reinventa nada, solo se
agrega el disparador (quién revisa a quién) y el envío saliente.

Diseño deliberado para el hackathon: disparo MANUAL vía endpoint
(POST /api/v1/admin/proactive-check), no un scheduler en background. Esto
evita añadir infraestructura de colas/workers y mantiene el comportamiento
observable y controlable durante la demo. El plan original menciona un cron
job externo; este endpoint es el equivalente controlado a mano.
"""

from typing import Dict, Any, List
from sqlalchemy.orm import Session

from app.db import crud
from app.services.deterministic import calculate_billing_facts
from app.services.llm import generate_proactive_alert_message
from app.services.whatsapp_sender import send_whatsapp_text
from app.services.telegram_sender import send_telegram_text


def _enviar_alerta(db: Session, user_id: str, mensaje: str) -> Dict[str, Any]:
    """
    Envía el mensaje proactivo por el/los canales de contacto disponibles
    del usuario. Si no tiene ningún contacto registrado, se reporta como
    'sin_canal' en vez de fallar silenciosamente.
    """
    contacto = crud.get_contacto_usuario(db, user_id)
    canales_usados = []

    if contacto and contacto.whatsapp_number:
        send_whatsapp_text(contacto.whatsapp_number, mensaje)
        canales_usados.append("whatsapp")

    if contacto and contacto.telegram_chat_id:
        send_telegram_text(contacto.telegram_chat_id, mensaje)
        canales_usados.append("telegram")

    if not canales_usados:
        return {"user_id": user_id, "estado": "sin_canal_de_contacto"}

    return {"user_id": user_id, "estado": "enviado", "canales": canales_usados}


MAX_USERS_PROACTIVE_SCAN = 200  # límite para no bloquear el servidor en testing local


def run_proactive_check(db: Session) -> Dict[str, Any]:
    """
    Recorre todos los usuarios con recibos, detecta alertas próximas a vencer
    (upcoming_alerts, ya calculado deterministamente) y envía un mensaje
    proactivo por los canales de contacto disponibles.

    Si el usuario no tiene canal de WhatsApp/Telegram operativo (caso habitual
    en testing local), la alerta se registra igualmente con estado "panel" para
    que el panel de administración la muestre y el demo sea visible.

    Retorna un resumen apto para mostrarse en el panel de administración.
    """
    user_ids = crud.get_all_user_ids(db)[:MAX_USERS_PROACTIVE_SCAN]
    resultados: List[Dict[str, Any]] = []

    for user_id in user_ids:
        fact_payload = calculate_billing_facts(user_id, db)
        alertas = fact_payload.get("upcoming_alerts") or []

        if not alertas:
            continue

        for alerta in alertas:
            mensaje = generate_proactive_alert_message(alerta)
            envio = _enviar_alerta(db, user_id, mensaje)

            # Si no hay canal externo disponible (testing local), registramos
            # igualmente la alerta con canal "panel" para hacerla visible en el admin.
            if envio.get("estado") == "sin_canal_de_contacto":
                envio["estado"] = "panel"
                envio["canales"] = ["panel"]

            resultados.append({
                **envio,
                "alerta": alerta,
                "mensaje_enviado": mensaje,
            })

    return {
        "usuarios_revisados": len(user_ids),
        "alertas_enviadas": len(resultados),
        "detalle": resultados,
    }
