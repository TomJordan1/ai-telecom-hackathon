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


MAX_USERS_PROACTIVE_SCAN = 60  # límite para barrido ágil en demostraciones


def run_proactive_check(db: Session) -> Dict[str, Any]:
    """
    Recorre los usuarios con recibos, priorizando aquellos con WhatsApp/Telegram
    vinculado, detecta alertas próximas a vencer y envía los mensajes proactivos.
    """
    # 1. Obtener primero los usuarios con WhatsApp registrado
    contactos_con_wa = [c.user_id for c in crud.get_all_contactos(db) if c.whatsapp_number]
    
    # 2. Completar con otros usuarios hasta el límite
    todos_los_ids = crud.get_all_user_ids(db)
    otros_ids = [uid for uid in todos_los_ids if uid not in contactos_con_wa][:MAX_USERS_PROACTIVE_SCAN]
    
    user_ids = contactos_con_wa + otros_ids
    resultados: List[Dict[str, Any]] = []

    for user_id in user_ids:
        fact_payload = calculate_billing_facts(user_id, db)
        alertas = fact_payload.get("upcoming_alerts") or []

        if not alertas:
            continue


        contacto = crud.get_contacto_usuario(db, user_id)
        historial = crud.get_historial_reciente_usuario(
            db, user_id=user_id, whatsapp_number=contacto.whatsapp_number if contacto else None
        )

        for alerta in alertas:
            mensaje = generate_proactive_alert_message(alerta, historial_conversacion=historial)
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
