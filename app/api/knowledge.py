from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.db.database import get_db
from app.db import crud
from app.services.feedback_handler import register_feedback, get_followup_message
from app.services.proactive_alerts import run_proactive_check

from app.services.whatsapp_sender import send_whatsapp_text
from app.services.deterministic import calculate_billing_facts
from app.services.llm import generate_proactive_alert_message

router = APIRouter(prefix="/api/v1", tags=["Feedback & Admin"])

# --- Schemas ---

class ContactoRequest(BaseModel):
    user_id: str
    whatsapp_number: Optional[str] = None
    telegram_chat_id: Optional[str] = None

class EnviarAlertaManualRequest(BaseModel):
    user_id: Optional[str] = None
    whatsapp_number: Optional[str] = None
    mensaje_personalizado: Optional[str] = None

class HandoffReplyRequest(BaseModel):
    message: str

class FeedbackRequest(BaseModel):
    session_id: str
    caso_id: str
    tipo: str  # POSITIVO | NEGATIVO
    es_posterior: bool = False

class ValidarCasoRequest(BaseModel):
    validado_por: Optional[str] = "AGENTE_MOVISTAR"
    solucion_editada: Optional[str] = None  # Texto editado por el agente (reemplaza la solucion propuesta)

# --- Endpoints de Feedback ---


@router.post("/feedback")
def submit_feedback(request: FeedbackRequest, db: Session = Depends(get_db)):
    """
    Recibe el feedback del usuario (👍 / 👎), inmediato o posterior.
    Si es posterior y positivo, el sistema puede promover el caso a base_casos.
    """
    if request.tipo not in ("POSITIVO", "NEGATIVO"):
        raise HTTPException(status_code=400, detail="tipo debe ser POSITIVO o NEGATIVO")

    register_feedback(db, request.caso_id, request.tipo, request.es_posterior)
    return {"status": "ok", "mensaje": "Feedback registrado exitosamente."}

@router.post("/followup/{caso_id}")
def trigger_followup(caso_id: str, db: Session = Depends(get_db)):
    """
    Endpoint para disparar el mensaje de seguimiento.
    En producción, sería invocado por un cron job o scheduler externo.
    """
    caso = crud.get_caso_cuarentena(db, caso_id)
    if not caso:
        raise HTTPException(status_code=404, detail="Caso no encontrado")

    mensaje = get_followup_message(caso.patron_detectado)
    # En producción aquí se enviaría por el canal original (WhatsApp/Telegram/Web)
    return {
        "status": "ok",
        "mensaje_followup": mensaje,
        "caso_id": caso_id,
        "patron": caso.patron_detectado,
        "nota": "En producción este mensaje se enviará automáticamente al canal de origen."
    }

# --- Endpoints de Administración (Panel Movistar) ---

@router.get("/admin/cuarentena")
def list_cuarentena(db: Session = Depends(get_db)):
    """Lista todos los casos en cuarentena pendientes de validación."""
    casos = crud.get_cuarentena_pendiente(db)
    return {
        "total": len(casos),
        "casos": [
            {
                "id": c.id,
                "patron": c.patron_detectado,
                "incertidumbre": c.incertidumbre_score,
                "feedback_inmediato": c.feedback_inmediato,
                "feedback_posterior": c.feedback_posterior,
                "fecha": c.fecha_consulta.isoformat() if c.fecha_consulta else None,
                "fecha_followup": c.fecha_followup.isoformat() if c.fecha_followup else None,
                "solucion_propuesta": c.solucion_propuesta,
                "evidencias": c.evidencias,
            }
            for c in casos
        ]
    }


@router.get("/admin/cuarentena/{caso_id}")
def get_cuarentena_detalle(caso_id: str, db: Session = Depends(get_db)):
    """Devuelve el detalle completo de un caso en cuarentena para revisión/edición."""
    caso = crud.get_caso_cuarentena(db, caso_id)
    if not caso:
        raise HTTPException(status_code=404, detail="Caso no encontrado")
    return {
        "id": caso.id,
        "patron": caso.patron_detectado,
        "session_id": caso.session_id,
        "incertidumbre": caso.incertidumbre_score,
        "feedback_inmediato": caso.feedback_inmediato,
        "feedback_posterior": caso.feedback_posterior,
        "fecha": caso.fecha_consulta.isoformat() if caso.fecha_consulta else None,
        "solucion_propuesta": caso.solucion_propuesta,
        "evidencias": caso.evidencias,
    }


@router.post("/admin/validar/{caso_id}")
def validate_case(caso_id: str, request: ValidarCasoRequest, db: Session = Depends(get_db)):
    """
    Promueve un caso de cuarentena a base_casos (conocimiento validado).
    Si se provee solucion_editada, reemplaza la solución propuesta antes de promover.
    """
    if request.solucion_editada is not None:
        # El agente editó la respuesta: actualizar el caso antes de promoverlo.
        crud.update_caso_cuarentena(db, caso_id, {
            "solucion_propuesta": {"texto": request.solucion_editada}
        })

    nuevo_caso = crud.promover_caso_a_base(db, caso_id, request.validado_por)
    if not nuevo_caso:
        raise HTTPException(status_code=404, detail="Caso no encontrado en cuarentena")
    return {
        "status": "ok",
        "mensaje": f"Caso promovido exitosamente a base_casos.",
        "nuevo_caso_id": nuevo_caso.id,
        "patron": nuevo_caso.patron_problema
    }

@router.post("/admin/handoff/return/{session_id}")
def return_handoff_to_bot(session_id: str, db: Session = Depends(get_db)):
    """
    Devuelve explícitamente el control de la sesión a Lucía, 
    revocando la bandera de handoff humano antes del timeout automático.
    """
    historial = crud.revocar_handoff(db, session_id)
    if not historial:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    return {
        "status": "ok",
        "mensaje": "Control devuelto a Lucía exitosamente."
    }

@router.get("/admin/handoff-queue")
def list_handoff_queue(solo_pendientes: bool = True, db: Session = Depends(get_db)):
    """
    Lista los turnos derivados a un humano (solicitud explícita o incertidumbre
    alta), con el contexto ya empaquetado (handoff_context) para que un agente
    pueda continuar la conversación sin pedirle al cliente que repita todo.
    """
    entradas = crud.get_handoff_queue(db, solo_pendientes=solo_pendientes)
    return {
        "total": len(entradas),
        "casos": [
            {
                "id": e.id,
                "session_id": e.session_id,
                "intent_category": e.intent_category,
                "detected_event": e.detected_event,
                "handoff_context": e.handoff_context,
                "atendido": e.atendido,
                "fecha": e.timestamp.isoformat() if e.timestamp else None,
            }
            for e in entradas
        ]
    }


@router.post("/admin/handoff-queue/{audit_log_id}/atender")
def marcar_handoff_atendido(audit_log_id: int, db: Session = Depends(get_db)):
    """Marca un caso de la cola de derivación como ya atendido por un agente."""
    entrada = crud.marcar_handoff_atendido(db, audit_log_id)
    if not entrada:
        raise HTTPException(status_code=404, detail="Registro no encontrado")
    return {"status": "ok", "id": entrada.id, "atendido": entrada.atendido}


class HandoffChannelRequest(BaseModel):
    session_id: str
    canal_preferido: str


@router.post("/handoff-channel")
def update_handoff_channel(request: HandoffChannelRequest, db: Session = Depends(get_db)):
    """
    Actualiza el canal de atención preferido por el cliente (CHAT, LLAMADA, WHATSAPP)
    en el expediente de derivación de la cola de soporte del panel de administración.
    """
    entrada = crud.update_ultimo_handoff_channel(db, request.session_id, request.canal_preferido)
    return {"status": "ok", "canal_preferido": request.canal_preferido, "actualizado": entrada is not None}


@router.post("/admin/proactive-check")
def trigger_proactive_check(db: Session = Depends(get_db)):
    """
    Dispara manualmente el barrido de alertas proactivas: revisa a todos los
    usuarios, detecta promociones próximas a vencer (upcoming_alerts, ya
    calculado deterministamente) y envía un mensaje proactivo por WhatsApp
    o Telegram si el usuario tiene un canal de contacto registrado.

    En producción esto lo dispararía un cron job o scheduler externo; aquí se
    deja como un botón manual para poder controlarlo durante la demo.
    """
    resumen = run_proactive_check(db)
    return {"status": "ok", **resumen}


@router.get("/cuenta-demo")
def get_cuenta_demo(db: Session = Depends(get_db)):
    """
    Devuelve una cuenta financiera real con historial suficiente para demostrar
    una explicación de variación de recibo.

    Existe para que los clientes que no pueden resolver la identidad del usuario
    (el bot de Telegram, una prueba manual con curl) no tengan que llevar un
    identificador escrito a mano que se rompe al reingerir los datos.
    """
    cuenta = crud.get_cuenta_demo(db)
    if not cuenta:
        raise HTTPException(
            status_code=404,
            detail="No hay cuentas facturadas en la base. Ejecuta scripts/ingest_real_data.py.",
        )
    return {"cuenta_financiera": cuenta}


@router.get("/admin/base-casos")
def list_base_casos(db: Session = Depends(get_db)):
    """Lista todos los casos validados en la base de conocimiento."""
    from app.db.models import BaseCasos
    casos = db.query(BaseCasos).filter(BaseCasos.activo == True).all()
    return {
        "total": len(casos),
        "casos": [
            {
                "id": c.id,
                "patron": c.patron_problema,
                "veces_aplicado": c.veces_aplicado,
                "tasa_exito": c.tasa_exito,
                "validado_por": c.validado_por,
                "fecha_validacion": c.fecha_validacion.isoformat() if c.fecha_validacion else None,
                "solucion": c.solucion_estructurada,
                "condiciones": c.condiciones,
            }
            for c in casos
        ]
    }


# --- Endpoints de Mapeo de WhatsApp y Contactos ---

@router.get("/admin/contactos")
def list_contactos(db: Session = Depends(get_db), limit: int = 50):
    """Lista los números de WhatsApp vinculados a cuentas de facturación (solo registros con número real)."""
    todos = crud.get_all_contactos(db)
    # Filtrar solo aquellos que tienen un número de WhatsApp real registrado
    contactos_con_wa = [c for c in todos if c.whatsapp_number and str(c.whatsapp_number).strip() != ""][:limit]
    resultado = []
    for c in contactos_con_wa:
        facts = calculate_billing_facts(c.user_id, db)
        plan = facts.get("plan_actual", "No identificado")
        current_bill = facts.get("current_bill") or {}
        monto = current_bill.get("amount", 0.0)
        alertas = facts.get("upcoming_alerts") or []

        resultado.append({
            "user_id": c.user_id,
            "whatsapp_number": c.whatsapp_number,
            "telegram_chat_id": c.telegram_chat_id,
            "plan_actual": plan,
            "monto_ultimo_recibo": monto,
            "total_alertas_activas": len(alertas),
            "alertas": alertas,
        })
    return {
        "total": len(resultado),
        "contactos": resultado
    }




@router.post("/admin/contactos")
def upsert_contacto(request: ContactoRequest, db: Session = Depends(get_db)):
    """
    Vincula o actualiza el número de WhatsApp o Telegram de un cliente.
    Verifica que la cuenta financiera exista en la base de facturación.
    """
    user_id_clean = request.user_id.strip()
    if not crud.verificar_existe_cuenta(db, user_id_clean):
        raise HTTPException(
            status_code=404,
            detail=f"La cuenta financiera '{user_id_clean}' no existe en la base de datos."
        )

    contacto = crud.upsert_contacto_usuario(
        db,
        user_id=user_id_clean,
        whatsapp_number=request.whatsapp_number.strip() if request.whatsapp_number else None,
        telegram_chat_id=request.telegram_chat_id.strip() if request.telegram_chat_id else None,
    )
    return {
        "status": "ok",
        "mensaje": f"Contacto vinculado exitosamente a la cuenta {user_id_clean}",
        "user_id": contacto.user_id,
        "whatsapp_number": contacto.whatsapp_number,
        "telegram_chat_id": contacto.telegram_chat_id,
    }


@router.delete("/admin/contactos/{user_id}")
def delete_contacto(user_id: str, db: Session = Depends(get_db)):
    """Desvincula un contacto por su número de cuenta."""
    eliminado = crud.delete_contacto_usuario(db, user_id)
    if not eliminado:
        raise HTTPException(status_code=404, detail="Contacto no encontrado")
    return {"status": "ok", "mensaje": f"Contacto {user_id} desvinculado exitosamente."}


@router.get("/admin/cuentas-con-alertas")
def list_cuentas_con_alertas(db: Session = Depends(get_db), limit_scan: int = 150):
    """
    Busca y devuelve cuentas financieras que tienen promociones en su último ciclo (upcoming_alerts).
    Permite al usuario/admin seleccionar una cuenta con 1 clic para probar el envío de alertas.
    """
    user_ids = crud.get_all_user_ids(db)[:limit_scan]
    cuentas_con_alerta = []

    for uid in user_ids:
        facts = calculate_billing_facts(uid, db)
        alertas = facts.get("upcoming_alerts") or []
        if alertas:
            contacto = crud.get_contacto_usuario(db, uid)
            current_bill = facts.get("current_bill") or {}
            cuentas_con_alerta.append({
                "user_id": uid,
                "plan_actual": facts.get("plan_actual", "Desconocido"),
                "monto_actual": current_bill.get("amount", 0.0),
                "alertas": alertas,
                "whatsapp_vinculado": contacto.whatsapp_number if contacto else None,
            })
            if len(cuentas_con_alerta) >= 20:
                break

    return {
        "total": len(cuentas_con_alerta),
        "cuentas": cuentas_con_alerta
    }


@router.post("/admin/enviar-alerta-manual")
def enviar_alerta_manual(request: EnviarAlertaManualRequest, db: Session = Depends(get_db)):
    """
    Envía una alerta proactiva o notificación de prueba directamente al número de WhatsApp indicado.
    Si se provee user_id, calcula los upcoming_alerts de esa cuenta y genera el mensaje de Lucía.
    """
    destino_whatsapp = request.whatsapp_number
    if not destino_whatsapp and request.user_id:
        contacto = crud.get_contacto_usuario(db, request.user_id)
        if contacto and contacto.whatsapp_number:
            destino_whatsapp = contacto.whatsapp_number

    if not destino_whatsapp:
        raise HTTPException(
            status_code=400,
            detail="Se requiere un número de WhatsApp destino o una cuenta vinculada a un número."
        )

    mensaje_a_enviar = request.mensaje_personalizado

    if not mensaje_a_enviar and request.user_id:
        facts = calculate_billing_facts(request.user_id, db)
        alertas = facts.get("upcoming_alerts") or []
        historial = crud.get_historial_reciente_usuario(
            db, user_id=request.user_id, whatsapp_number=destino_whatsapp
        )
        if alertas:
            mensaje_a_enviar = generate_proactive_alert_message(alertas[0], historial_conversacion=historial)
        else:
            plan = facts.get("plan_actual", "tu plan")
            monto = facts.get("current_bill", {}).get("amount", 0.0)
            if historial and len(historial) > 0:
                mensaje_a_enviar = (
                    f"Aprovechando que estamos en contacto, te confirmo que tu cuenta {request.user_id} ({plan}) "
                    f"está al día con un último recibo de S/ {monto:.2f}. "
                    "¿Deseas consultar algún detalle adicional?"
                )
            else:
                mensaje_a_enviar = (
                    f"¡Hola! Soy Lucía de Movistar. Te confirmo que tu cuenta {request.user_id} ({plan}) "
                    f"está al día con un último recibo de S/ {monto:.2f}. "
                    "¿Tienes alguna duda sobre tu facturación?"
                )


    if not mensaje_a_enviar:
        mensaje_a_enviar = (
            "🔔 ¡Hola! Este es un mensaje de prueba de Lucía (Copiloto de Facturación Movistar). "
            "Tu canal de WhatsApp está correctamente conectado y listo para recibir alertas proactivas."
        )

    # Enviar a WhatsApp Cloud API
    send_whatsapp_text(destino_whatsapp, mensaje_a_enviar)

    return {
        "status": "ok",
        "mensaje": f"Mensaje enviado exitosamente a {destino_whatsapp}",
        "destinatario": destino_whatsapp,
        "contenido": mensaje_a_enviar
    }


# --- Handoff a Agente Real ---

@router.get("/admin/handoff/{session_id}/historial")
def get_handoff_historial(session_id: str, response: Response, db: Session = Depends(get_db)):
    from app.db.models import HistorialInteracciones
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    historial = db.query(HistorialInteracciones).filter(HistorialInteracciones.session_id == session_id).first()
    if not historial:
        return {"historial_conversacion": [], "en_atencion_humana": False}
    return {
        "historial_conversacion": historial.historial_conversacion or [],
        "en_atencion_humana": historial.en_atencion_humana
    }


@router.post("/admin/handoff/{session_id}/reply")
def reply_handoff(session_id: str, payload: HandoffReplyRequest, db: Session = Depends(get_db)):
    from app.services.whatsapp_sender import send_whatsapp_text
    from app.db import crud
    
    if session_id.startswith("wa_"):
        phone_number = session_id[3:]
        send_whatsapp_text(phone_number, payload.message)
    
    crud.append_turno_conversacion(db, session_id, "lucia", payload.message, "AGENTE_HUMANO")
    return {"status": "ok"}


@router.post("/admin/handoff/{session_id}/resolve")
def resolve_handoff(session_id: str, db: Session = Depends(get_db)):
    from app.db import crud
    from app.db.models import AuditLog
    from app.services.whatsapp_sender import send_whatsapp_text
    
    crud.update_historial(db, session_id, {"en_atencion_humana": False})
    
    logs = db.query(AuditLog).filter(
        AuditLog.session_id == session_id,
        AuditLog.requires_human_intervention == True,
        AuditLog.atendido == False
    ).all()
    
    for log in logs:
        crud.marcar_handoff_atendido(db, log.id)

    if session_id.startswith("wa_"):
        phone_number = session_id[3:]
        send_whatsapp_text(phone_number, "🤖 Lucía ha retomado la conversación. ¿En qué más te puedo ayudar?")
        
    return {"status": "resolved"}

