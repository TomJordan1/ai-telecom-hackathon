import re
import uuid
from typing import Optional, Dict, Any, List, Union
from datetime import datetime
from sqlalchemy import desc, distinct, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db import models


class VirtualRecibo:
    """Wrapper virtual para agrupar cargos de facturacion_clientes por ciclo."""
    def __init__(self, ciclo: str, cargos: list):
        self.ciclo = str(ciclo or "")
        self.mes_emision = f"{self.ciclo[:4]}-{self.ciclo[4:6]}" if len(self.ciclo) >= 6 else "2026-01"
        self.monto_total = round(sum(float(c.charge_total_amount or 0.0) for c in cargos), 2)
        try:
            self.fecha_emision = datetime(int(self.ciclo[:4]), int(self.ciclo[4:6]), 1) if len(self.ciclo) >= 6 else datetime(2026, 1, 1)
        except ValueError:
            self.fecha_emision = datetime(2026, 1, 1)
        self.plan_actual = None

        cargos_dict = []
        for c in cargos:
            cargos_dict.append({
                "CHARGE_CODE_ID": c.charge_code_id,
                "CHARGE_CODE_DESC": c.charge_code_desc,
                "CHARGE_CODE_CLASSIFICATION": c.charge_code_classification,
                "CHARGE_TOTAL_AMOUNT": c.charge_total_amount,
                "CHARGE_NET_AMOUNT": c.charge_net_amount,
                "GRUPO": c.grupo,
                "SUB_GRUPO": c.sub_grupo,
            })

        first = cargos[0] if cargos else None
        info_factura = {
            "CUSTOMER_KEY": getattr(first, "customer_key", ""),
            "BILLING_ARRANGEMENT_KEY": getattr(first, "billing_arrangement_key", ""),
            "LEGAL_INVOICE_NUMBER": getattr(first, "legal_invoice_number", ""),
            "BILLING_CYCLE_KEY": getattr(first, "billing_cycle_key", ""),
            "SUBSCRIBER_KEY": getattr(first, "subscriber_key", ""),
            "PERIOD_START_DATE": getattr(first, "period_start_date", ""),
            "PERIOD_END_DATE": getattr(first, "period_end_date", ""),
            "FECHA_VENCIMIENTO": getattr(first, "fecha_vencimiento", ""),
            "DEUDA": getattr(first, "deuda", ""),
        }
        self.conceptos_facturados = {
            "cargos": cargos_dict,
            "info_factura": info_factura,
        }


def get_cargos_por_usuario(db: Session, account_id: str, limit_ciclos: int = 6):
    """
    Recupera los cargos facturados directamente de facturacion_clientes para una cuenta,
    agrupados por los ciclos más recientes.
    """
    if not account_id:
        return []
    ciclos = db.query(models.FacturacionCliente.ciclo)\
              .filter(models.FacturacionCliente.financial_account_key == str(account_id))\
              .distinct()\
              .order_by(models.FacturacionCliente.ciclo.desc())\
              .limit(limit_ciclos)\
              .all()
    lista_ciclos = [c[0] for c in ciclos if c[0]]
    if not lista_ciclos:
        return []

    cargos = db.query(models.FacturacionCliente)\
               .filter(
                   models.FacturacionCliente.financial_account_key == str(account_id),
                   models.FacturacionCliente.ciclo.in_(lista_ciclos)
               )\
               .order_by(models.FacturacionCliente.ciclo.desc())\
               .all()

    by_ciclo = {}
    for c in cargos:
        by_ciclo.setdefault(c.ciclo, []).append(c)

    # Mantener el orden descendente de ciclo
    res = []
    for c_id in lista_ciclos:
        if c_id in by_ciclo:
            res.append(VirtualRecibo(c_id, by_ciclo[c_id]))
    return res


def get_recibos_by_user(db: Session, user_id: str, limit: int = 6):
    return get_cargos_por_usuario(db, user_id, limit_ciclos=limit)


def get_all_user_ids(db: Session):
    """Lista todos los financial_account_key distintos que tienen cargos facturados."""
    rows = db.query(models.FacturacionCliente.financial_account_key).distinct().all()
    return [r[0] for r in rows if r[0]]


# Cache de proceso: la consulta de cuenta demo recorre la tabla de facturación
# y no tiene sentido repetirla en cada mensaje entrante de un canal externo.
_cuenta_demo_cache: str | None = None


def get_cuenta_demo(db: Session):
    """
    Resuelve una cuenta financiera real con historial suficiente para demostrar
    una explicación de variación (la que más ciclos facturados tiene).

    Se usa como respaldo cuando un canal externo recibe un mensaje de alguien
    que no está registrado en contactos_usuario. Antes había un identificador
    del set de datos ficticio escrito en el código, que dejó de existir al
    migrar al dataset real y hacía fallar la conversación.
    """
    global _cuenta_demo_cache
    if _cuenta_demo_cache:
        return _cuenta_demo_cache

    from app.core.config import settings

    if settings.DEMO_ACCOUNT_ID:
        _cuenta_demo_cache = str(settings.DEMO_ACCOUNT_ID)
        return _cuenta_demo_cache

    fila = (
        db.query(
            models.FacturacionCliente.financial_account_key,
            func.count(distinct(models.FacturacionCliente.ciclo)).label("ciclos"),
        )
        .group_by(models.FacturacionCliente.financial_account_key)
        .order_by(desc("ciclos"))
        .first()
    )
    _cuenta_demo_cache = fila[0] if fila else None
    return _cuenta_demo_cache


def get_contacto_usuario(db: Session, user_id: str):
    return db.query(models.ContactoUsuario).filter(models.ContactoUsuario.user_id == user_id).first()


def _solo_digitos(valor: str) -> str:
    return re.sub(r"\D", "", valor or "")


def get_user_id_por_whatsapp(db: Session, numero: str):
    """
    Resuelve el user_id a partir del número de WhatsApp entrante.

    Es la búsqueda inversa de get_contacto_usuario: el webhook recibe un número
    y necesita saber de qué cliente se trata, o todos los mensajes terminarían
    atendidos como el mismo usuario.

    La comparación se hace sobre dígitos, porque Meta envía el número sin '+' y
    los registros pueden tener formatos distintos. Si no hay coincidencia exacta
    se intenta por los últimos 9 dígitos, que tolera diferencias de prefijo país.
    """
    digitos = _solo_digitos(numero)
    if not digitos:
        return None

    contactos = db.query(models.ContactoUsuario).filter(
        models.ContactoUsuario.whatsapp_number.isnot(None)
    ).all()

    for contacto in contactos:
        if _solo_digitos(contacto.whatsapp_number) == digitos:
            return contacto.user_id

    for contacto in contactos:
        registrado = _solo_digitos(contacto.whatsapp_number)
        if len(registrado) >= 9 and len(digitos) >= 9 and registrado[-9:] == digitos[-9:]:
            return contacto.user_id

    return None


def get_user_id_por_telegram(db: Session, chat_id: str):
    """Equivalente para Telegram: resuelve el user_id desde el chat_id."""
    if not chat_id:
        return None
    contacto = db.query(models.ContactoUsuario).filter(
        models.ContactoUsuario.telegram_chat_id == str(chat_id)
    ).first()
    return contacto.user_id if contacto else None


def upsert_contacto_usuario(db: Session, user_id: str, whatsapp_number: str = None, telegram_chat_id: str = None):
    contacto = get_contacto_usuario(db, user_id)
    if not contacto:
        contacto = models.ContactoUsuario(user_id=user_id)
        db.add(contacto)
    if whatsapp_number is not None:
        contacto.whatsapp_number = whatsapp_number
    if telegram_chat_id is not None:
        contacto.telegram_chat_id = telegram_chat_id
    db.commit()
    db.refresh(contacto)
    return contacto


def get_all_contactos(db: Session):
    """Lista todos los registros de contactos (WhatsApp y Telegram)."""
    return db.query(models.ContactoUsuario).all()


def delete_contacto_usuario(db: Session, user_id: str) -> bool:
    """Elimina la vinculación de un contacto por su user_id."""
    contacto = get_contacto_usuario(db, user_id)
    if contacto:
        db.delete(contacto)
        db.commit()
        return True
    return False


def verificar_existe_cuenta(db: Session, account_id: str) -> bool:
    """Comprueba si una cuenta financiera existe en la base de facturación o planta."""
    if not account_id:
        return False
    str_acc = str(account_id).strip()
    fact = db.query(models.FacturacionCliente.id).filter(
        models.FacturacionCliente.financial_account_key == str_acc
    ).first()
    if fact:
        return True
    planta = db.query(models.PlantaCliente.id).filter(
        models.PlantaCliente.financial_account == str_acc
    ).first()
    return planta is not None


def get_historial_reciente_usuario(db: Session, user_id: str | None = None, whatsapp_number: str | None = None) -> list:
    """
    Recupera los mensajes del historial conversacional más reciente asociado al usuario o número de WhatsApp.
    Permite saber si ya existe una conversación activa para contextualizar los mensajes de alerta proactiva.
    """
    # 1. Buscar por sesión de WhatsApp primero si se proporcionó número
    if whatsapp_number:
        clean_wa = "".join(filter(str.isdigit, str(whatsapp_number)))
        historial = db.query(models.HistorialInteracciones).filter(
            models.HistorialInteracciones.session_id.in_([f"wa_{clean_wa}", f"wa_+{clean_wa}"])
        ).order_by(models.HistorialInteracciones.updated_at.desc()).first()
        if historial and historial.historial_conversacion:
            return historial.historial_conversacion

    # 2. Buscar por user_id en historial de interacciones
    if user_id:
        historial = db.query(models.HistorialInteracciones).filter(
            models.HistorialInteracciones.user_id == str(user_id)
        ).order_by(models.HistorialInteracciones.updated_at.desc()).first()
        if historial and historial.historial_conversacion:
            return historial.historial_conversacion

    return []


TTL_MENSAJES_PROCESADOS_HORAS = 24  # ventana suficiente para cubrir reintentos de Meta




def _purgar_mensajes_procesados(db: Session):
    """Elimina ids antiguos para que la tabla de idempotencia no crezca sin límite."""
    from datetime import datetime, timedelta

    limite = datetime.utcnow() - timedelta(hours=TTL_MENSAJES_PROCESADOS_HORAS)
    try:
        db.query(models.MensajeProcesado).filter(
            models.MensajeProcesado.recibido_at < limite
        ).delete(synchronize_session=False)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[IDEMPOTENCIA] No se pudo purgar mensajes antiguos: {e}")


def reclamar_mensaje_entrante(db: Session, message_id: str, canal: str = "whatsapp") -> bool:
    """
    Reclama el procesamiento de un mensaje entrante de forma atómica.

    Devuelve True solo la primera vez que se ve `message_id`; en los reintentos
    que envía Meta con el mismo id devuelve False y el webhook los descarta.
    El INSERT sobre la clave primaria es lo que hace la operación atómica: si dos
    reintentos llegan en paralelo, únicamente uno consigue insertar.
    """
    if not message_id:
        # Sin identificador no hay forma de deduplicar: se procesa para no perder
        # el mensaje, aceptando el riesgo de un duplicado.
        return True

    nuevo = models.MensajeProcesado(message_id=message_id, canal=canal)
    try:
        db.add(nuevo)
        db.commit()
    except IntegrityError:
        db.rollback()
        return False
    except Exception as e:
        # Si el control de idempotencia falla por otra razón, no se bloquea la
        # atención del cliente.
        db.rollback()
        print(f"[IDEMPOTENCIA] Error registrando {message_id}: {e}")
        return True

    _purgar_mensajes_procesados(db)
    return True


def peek_ultima_actividad(db: Session, session_id: str, user_id: str):
    """
    Lee (sin escribir) la fecha de la última actividad registrada para este
    usuario/sesión, ANTES de que el turno actual la actualice.

    Se usa para distinguir una sesión genuinamente retomada tras un tiempo
    (donde sí tiene sentido preguntar "¿quedó resuelto lo anterior?") de un
    turno más dentro de la misma conversación activa (donde no).

    Debe llamarse antes de get_or_create_historial: esa función puede hacer
    un commit por transferencia de session_id, y ese commit por sí solo ya
    actualiza `updated_at` por el onupdate del modelo, borrando la señal que
    necesitamos leer aquí.
    """
    if user_id and user_id != "anonimo":
        historial = db.query(models.HistorialInteracciones).filter(
            models.HistorialInteracciones.user_id == user_id
        ).order_by(models.HistorialInteracciones.updated_at.desc()).first()
        if historial:
            return historial.updated_at

    historial = db.query(models.HistorialInteracciones).filter(
        models.HistorialInteracciones.session_id == session_id
    ).first()
    return historial.updated_at if historial else None


def _sanitizar_user_id_para_fk(db: Session, user_id: str | None) -> str | None:
    """Retorna user_id solo si existe en contactos_usuario o planta_clientes, sino None para permitir sesiones de invitados."""
    if not user_id:
        return None
    uid = str(user_id).strip()
    if uid.startswith("invitado") or uid in ("anonimo", "guest", "visitante"):
        return None
    # Si existe la cuenta o contacto, se retorna el ID
    if verificar_existe_cuenta(db, uid):
        # Asegurar que esté en contactos_usuario para no violar FK
        contacto = db.query(models.ContactoUsuario).filter(models.ContactoUsuario.user_id == uid).first()
        if not contacto:
            try:
                db.add(models.ContactoUsuario(user_id=uid))
                db.flush()
            except Exception:
                db.rollback()
        return uid
    return None


def get_or_create_historial(db: Session, session_id: str, user_id: str):
    """
    Recupera o crea el historial conversacional de un usuario.
    Resiliente ante sesiones de visitantes (user_id=None en DB) y usuarios registrados.
    """
    db_uid = _sanitizar_user_id_para_fk(db, user_id)
    try:
        # 1. Buscar por session_id primero
        historial = db.query(models.HistorialInteracciones).filter(
            models.HistorialInteracciones.session_id == session_id
        ).first()
        if historial:
            if db_uid and historial.user_id != db_uid:
                historial.user_id = db_uid
                db.commit()
            return historial

        # 2. Si es cliente identificado, buscar si tenía sesión previa
        if db_uid:
            previo = db.query(models.HistorialInteracciones).filter(
                models.HistorialInteracciones.user_id == db_uid
            ).order_by(models.HistorialInteracciones.updated_at.desc()).first()
            if previo:
                nuevo = models.HistorialInteracciones(
                    session_id=session_id,
                    user_id=db_uid,
                    comentarios_emocionales=previo.comentarios_emocionales or [],
                    score_sentimiento=previo.score_sentimiento or 3,
                    perfil_lexico_usuario=previo.perfil_lexico_usuario or "CASUAL",
                    historial_conversacion=previo.historial_conversacion or []
                )
                db.add(nuevo)
                db.commit()
                db.refresh(nuevo)
                return nuevo

        # 3. Crear registro nuevo (para visitante o nuevo cliente)
        nuevo = models.HistorialInteracciones(
            session_id=session_id,
            user_id=db_uid,
            comentarios_emocionales=[],
            score_sentimiento=3,
            perfil_lexico_usuario="CASUAL",
            historial_conversacion=[]
        )
        db.add(nuevo)
        db.commit()
        db.refresh(nuevo)
        return nuevo
    except Exception as e:
        db.rollback()
        print(f"[HISTORIAL] Fallback en memoria para session_id={session_id}: {e}")
        return models.HistorialInteracciones(
            session_id=session_id,
            user_id=db_uid,
            comentarios_emocionales=[],
            score_sentimiento=3,
            perfil_lexico_usuario="CASUAL",
            historial_conversacion=[]
        )



EMOTIONAL_COMMENT_TTL_DAYS = 14  # expiración: no crecer el contexto indefinidamente
MAX_COMENTARIOS_EMOCIONALES = 5  # consolidación: solo se conservan los más recientes


def add_comentario_emocional(db: Session, session_id: str, text: str, importance: int = 3):
    """
    Registra una nueva frase emocional detectada en el mensaje del usuario.
    Es la contraparte de creación que faltaba: hasta ahora solo se leían y
    marcaban como referenciados comentarios existentes, nunca se generaban.
    Aplica expiración (TTL) y un tope de cantidad para evitar crecimiento
    indefinido del contexto, tal como especifica el diseño original.
    """
    from datetime import datetime, timedelta

    historial = db.query(models.HistorialInteracciones).filter(
        models.HistorialInteracciones.session_id == session_id
    ).first()
    if not historial:
        print(f"[MEMORIA] add_comentario_emocional: no se encontró historial para session_id={session_id}")
        return None

    ahora = datetime.utcnow()
    comentarios = list(historial.comentarios_emocionales or [])

    # Consolidación: descartar los ya vencidos antes de agregar uno nuevo.
    vigentes = []
    for c in comentarios:
        expira = c.get("expires_at")
        try:
            if expira and datetime.fromisoformat(expira) < ahora:
                continue
        except ValueError:
            pass
        vigentes.append(c)

    nuevo_id = (max((c.get("id", 0) for c in vigentes), default=0)) + 1
    vigentes.append({
        "id": nuevo_id,
        "text": text,
        "timestamp": ahora.isoformat(),
        "importance": importance,
        "reference_count": 0,
        "expires_at": (ahora + timedelta(days=EMOTIONAL_COMMENT_TTL_DAYS)).isoformat(),
        "referenciado": False,
    })

    # Resumen/consolidación: si se excede el tope, se conservan los más recientes.
    if len(vigentes) > MAX_COMENTARIOS_EMOCIONALES:
        vigentes = vigentes[-MAX_COMENTARIOS_EMOCIONALES:]

    historial.comentarios_emocionales = vigentes
    db.commit()
    db.refresh(historial)
    return historial


def update_historial(db: Session, session_id: str, updates: dict):
    historial = db.query(models.HistorialInteracciones).filter(models.HistorialInteracciones.session_id == session_id).first()
    if historial:
        for key, value in updates.items():
            setattr(historial, key, value)
        db.commit()
        db.refresh(historial)
    return historial

def is_en_atencion_humana(db: Session, session_id: str) -> bool:
    historial = db.query(models.HistorialInteracciones).filter(models.HistorialInteracciones.session_id == session_id).first()
    return getattr(historial, "en_atencion_humana", False) if historial else False

MAX_TURNOS_HISTORIAL = 12  # ~6 intercambios usuario/Lucía; suficiente para dar
                           # continuidad sin acumular contexto indefinidamente.


def append_turno_conversacion(db: Session, session_id: str, role: str, text: str, intent: str = ""):
    """
    Añade un turno a la bitácora acotada de la sesión y recorta al límite.
    role: 'user' | 'lucia'.
    """
    historial = db.query(models.HistorialInteracciones).filter(
        models.HistorialInteracciones.session_id == session_id
    ).first()
    if not historial:
        print(f"[MEMORIA] append_turno_conversacion: no se encontró historial para session_id={session_id}")
        return None

    turnos = list(historial.historial_conversacion or [])
    turnos.append({"role": role, "text": text, "intent": intent})
    if len(turnos) > MAX_TURNOS_HISTORIAL:
        turnos = turnos[-MAX_TURNOS_HISTORIAL:]

    historial.historial_conversacion = turnos
    db.commit()
    db.refresh(historial)
    return historial


def get_terminos_restringidos(db: Session):
    return db.query(models.TerminosRestringidos).all()

# --- Observabilidad ---

def create_audit_log(db: Session, **kwargs):
    """
    Registra una decisión del orquestador. Nunca debe bloquear el flujo
    principal si falla: la observabilidad es secundaria a responder al usuario.
    """
    log = models.AuditLog(**kwargs)
    db.add(log)
    db.commit()
    return log


def get_handoff_queue(db: Session, solo_pendientes: bool = True):
    """
    Lista los turnos que requirieron intervención humana, con el contexto
    empaquetado para que un agente pueda continuar sin pedirle al cliente
    que repita todo. Es la cola de atención del panel de administración.
    """
    query = db.query(models.AuditLog).filter(models.AuditLog.requires_human_intervention == True)
    if solo_pendientes:
        query = query.filter(models.AuditLog.atendido == False)
    return query.order_by(models.AuditLog.timestamp.desc()).all()


def marcar_handoff_atendido(db: Session, audit_log_id: int):
    entrada = db.query(models.AuditLog).filter(models.AuditLog.id == audit_log_id).first()
    if entrada:
        entrada.atendido = True
        db.commit()
        db.refresh(entrada)
    return entrada


def update_ultimo_handoff_channel(db: Session, session_id: str, canal: str):
    """Actualiza el canal preferido del cliente en el último registro de handoff de la sesión."""
    log = (
        db.query(models.AuditLog)
        .filter(models.AuditLog.session_id == session_id, models.AuditLog.requires_human_intervention == True)
        .order_by(models.AuditLog.timestamp.desc())
        .first()
    )
    if log and log.handoff_context:
        ctx = dict(log.handoff_context)
        ctx["canal_preferido"] = canal
        log.handoff_context = ctx
        db.commit()
        db.refresh(log)
    return log

# --- Base de Casos ---

def get_caso_conocido(db: Session, patron_problema: str):
    """Busca una solución validada para un patrón dado."""
    return db.query(models.BaseCasos).filter(
        models.BaseCasos.patron_problema == patron_problema,
        models.BaseCasos.activo == True
    ).first()

def increment_caso_aplicado(db: Session, caso_id: str):
    caso = db.query(models.BaseCasos).filter(models.BaseCasos.id == caso_id).first()
    if caso:
        caso.veces_aplicado += 1
        db.commit()

def create_caso_base(db: Session, data: dict):
    caso = models.BaseCasos(**data)
    db.add(caso)
    db.commit()
    db.refresh(caso)
    return caso

# --- Cuarentena de Casos y Folios ---

def generar_folio() -> str:
    """Genera un folio corto y legible (ej: CASO-8F3A2) para trazabilidad del cliente."""
    return f"CASO-{uuid.uuid4().hex[:5].upper()}"


def create_caso_cuarentena(db: Session, data: dict):
    if not data.get("folio"):
        data["folio"] = generar_folio()
    caso = models.CuarentenaCasos(**data)
    db.add(caso)
    db.commit()
    db.refresh(caso)
    return caso


def get_caso_cuarentena(db: Session, caso_id: str):
    return db.query(models.CuarentenaCasos).filter(models.CuarentenaCasos.id == caso_id).first()


def consultar_estado_caso(
    db: Session,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    folio: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Busca el estado en tiempo real de un caso por su folio corto o por la sesión/usuario.
    Retorna el estado de resolución (PENDIENTE, EN_GESTION, VALIDADO, ATENDIDO).
    """
    caso_cuarentena = None
    if folio:
        folio_clean = folio.strip().upper()
        caso_cuarentena = db.query(models.CuarentenaCasos).filter(models.CuarentenaCasos.folio == folio_clean).first()

    if not caso_cuarentena and session_id:
        caso_cuarentena = (
            db.query(models.CuarentenaCasos)
            .filter(models.CuarentenaCasos.session_id == session_id)
            .order_by(models.CuarentenaCasos.fecha_consulta.desc())
            .first()
        )

    audit_handoff = None
    if session_id:
        audit_handoff = (
            db.query(models.AuditLog)
            .filter(models.AuditLog.session_id == session_id, models.AuditLog.requires_human_intervention == True)
            .order_by(models.AuditLog.timestamp.desc())
            .first()
        )

    if not caso_cuarentena and not audit_handoff:
        return None

    folio_retornado = caso_cuarentena.folio if (caso_cuarentena and caso_cuarentena.folio) else None
    if not folio_retornado and audit_handoff and audit_handoff.handoff_context:
        folio_retornado = audit_handoff.handoff_context.get("folio")

    if not folio_retornado:
        folio_retornado = f"CASO-{(session_id or 'REC')[:5].upper()}"

    patron = (caso_cuarentena.patron_detectado if caso_cuarentena else (audit_handoff.detected_event or "CONSULTA_FACTURACION"))

    if caso_cuarentena and caso_cuarentena.estado_validacion == "APROBADO":
        estado = "VALIDADO"
        mensaje_estado = "Tu caso ya fue validado y homologado con éxito por un asesor de Movistar."
    elif audit_handoff and audit_handoff.atendido:
        estado = "ATENDIDO"
        mensaje_estado = "Tu caso ya fue gestionado y marcado como resuelto por el asesor especializado."
    elif audit_handoff and not audit_handoff.atendido:
        estado = "EN_GESTION_ASESOR"
        canal = audit_handoff.handoff_context.get("canal_preferido", "CHAT") if audit_handoff.handoff_context else "CHAT"
        canal_nombre = {"CHAT": "Chat Web", "LLAMADA": "Llamada Telefónica", "WHATSAPP": "WhatsApp"}.get(canal, canal)
        mensaje_estado = f"Tu expediente está en bandeja prioritaria de atención humana. Un asesor continuará contigo vía {canal_nombre} con todo el detalle de tu recibo."
    else:
        estado = "PENDIENTE_REVISION"
        mensaje_estado = "Tu caso se encuentra registrado en nuestra bandeja de validación y seguimiento."

    fecha_dt = caso_cuarentena.fecha_consulta if caso_cuarentena else audit_handoff.timestamp
    return {
        "folio": folio_retornado,
        "patron": patron,
        "estado": estado,
        "mensaje_estado": mensaje_estado,
        "fecha": fecha_dt.strftime("%d/%m/%Y %H:%M") if fecha_dt else "",
    }


def get_cuarentena_pendiente(db: Session):
    return db.query(models.CuarentenaCasos).filter(
        models.CuarentenaCasos.estado_validacion == "PENDIENTE"
    ).all()

def update_caso_cuarentena(db: Session, caso_id: str, updates: dict):
    caso = db.query(models.CuarentenaCasos).filter(models.CuarentenaCasos.id == caso_id).first()
    if caso:
        for key, value in updates.items():
            setattr(caso, key, value)
        db.commit()
        db.refresh(caso)
    return caso

def promover_caso_a_base(db: Session, caso_id: str, validado_por: str = "AGENTE_MOVISTAR"):
    """Mueve un caso aprobado de cuarentena a base_casos y genera sus embeddings."""
    caso = get_caso_cuarentena(db, caso_id)
    if not caso:
        return None

    condiciones = dict(caso.evidencias or {})
    query_text = condiciones.get("user_message") or ""
    if query_text:
        try:
            from app.services.embeddings import embed_query, embeddings_disponibles
            if embeddings_disponibles():
                condiciones["embedding"] = embed_query(query_text)
                condiciones["query_ejemplo"] = query_text
        except Exception as e:
            print(f"[EMBED WARNING] No se pudo generar vector para caso promovido: {e}")

    nuevo_caso_base = models.BaseCasos(
        patron_problema=caso.patron_detectado,
        condiciones=condiciones,
        solucion_estructurada=caso.solucion_propuesta,
        validado_por=validado_por
    )
    db.add(nuevo_caso_base)
    caso.estado_validacion = "APROBADO"
    db.commit()
    db.refresh(nuevo_caso_base)
    return nuevo_caso_base



# --- Órdenes del cliente ---

def get_ordenes_por_customer_key(db: Session, customer_key: str, limit: int = 10):
    """
    Recupera las últimas órdenes ejecutadas para un CUSTOMER_KEY, ordenadas
    por fecha de inicio descendente. Se usa para enriquecer la evidencia del
    motor determinista con contexto de cuándo/por qué ocurrió una acción CRM.
    """
    if not customer_key:
        return []
    return db.query(models.OrdenCliente).filter(
        models.OrdenCliente.customer_key == customer_key
    ).order_by(models.OrdenCliente.start_date.desc()).limit(limit).all()


# --- Notas de crédito ---

def get_notas_credito_por_ba_no(db: Session, ba_no: str, limit: int = 10):
    if not ba_no:
        return []
    return db.query(models.NotaCredito).filter(
        models.NotaCredito.ba_no == str(ba_no)
    ).order_by(models.NotaCredito.effective_date.desc()).limit(limit).all()


def get_notas_credito_por_customer_key(db: Session, customer_key: str, limit: int = 10):
    if not customer_key:
        return []
    return db.query(models.NotaCredito).filter(
        models.NotaCredito.receiver_customer == str(customer_key)
    ).order_by(models.NotaCredito.effective_date.desc()).limit(limit).all()


# --- Planta de clientes ---

def get_planta_por_financial_account(db: Session, account_id: str):
    if not account_id:
        return []
    return db.query(models.PlantaCliente).filter(
        models.PlantaCliente.financial_account == str(account_id)
    ).all()


# --- Catálogo de ofertas ---

def get_oferta_por_charge_code(db: Session, charge_code: str):
    if not charge_code:
        return None
    return db.query(models.CatalogoOfertas).filter(
        models.CatalogoOfertas.charge_code == str(charge_code)
    ).first()


def get_ofertas_por_charge_codes(db: Session, charge_codes):
    """
    Resuelve varias tarifas del catálogo en una sola consulta.
    Devuelve {charge_code: CatalogoOfertas}. Evita el N+1 que provocaba
    llamar a get_oferta_por_charge_code dentro de un bucle de cargos.
    """
    codigos = [str(c) for c in (charge_codes or []) if c]
    if not codigos:
        return {}
    filas = db.query(models.CatalogoOfertas).filter(
        models.CatalogoOfertas.charge_code.in_(codigos)
    ).all()
    return {f.charge_code: f for f in filas}


def get_notas_credito_por_ciclo(db: Session, ba_no: str, ciclo: str):
    """
    Notas de crédito (CRD) y débito (DSC) emitidas en un ciclo concreto.

    Es la pieza que permite explicar una variación por ajuste financiero:
    sin filtrar por ciclo no se puede atribuir la nota al recibo que el
    cliente está consultando.
    """
    if not ba_no or not ciclo:
        return []
    return db.query(models.NotaCredito).filter(
        models.NotaCredito.ba_no == str(ba_no),
        models.NotaCredito.ciclo == str(ciclo),
    ).all()


# Clasificaciones reales del dataset que identifican el cargo recurrente del
# plan principal. Se usan para construir el catálogo de planes ofertables a
# partir de cargos realmente facturados, en vez de un listado inventado.
CLASIFICACIONES_PLAN = (
    "Cargo Recurrente De Plan",
    "PLAN_Fija",
    "Cargo Recurrente Corp De Plan",
)


def get_planes_ofertables(db: Session, limit: int = 400):
    """
    Construye el catálogo de planes candidatos para recomendación cruzando
    dos fuentes reales:

      - facturacion_clientes: descripción legible del plan (CHARGE_CODE_DESC)
        de los cargos realmente clasificados como cargo recurrente de plan.
      - catalogo_ofertas: tarifa oficial (rate_final) y tipo de renta.

    Solo se devuelven planes que existen en AMBAS tablas: si un charge code no
    tiene tarifa en el catálogo oficial, no hay precio verificable que ofrecer
    y queda fuera. Devuelve una lista de dicts.
    """
    filas = (
        db.query(
            models.FacturacionCliente.charge_code_id,
            models.FacturacionCliente.charge_code_desc,
        )
        .filter(
            models.FacturacionCliente.charge_code_classification.in_(CLASIFICACIONES_PLAN),
            models.FacturacionCliente.charge_total_amount > 0,
        )
        .distinct()
        .limit(limit)
        .all()
    )
    if not filas:
        return []

    tarifas = get_ofertas_por_charge_codes(db, [f[0] for f in filas])

    planes = []
    vistos = set()
    for charge_code, desc in filas:
        oferta = tarifas.get(str(charge_code))
        if not oferta or not oferta.rate_final or charge_code in vistos:
            continue
        vistos.add(charge_code)
        planes.append({
            "charge_code": charge_code,
            "nombre": desc or charge_code,
            "precio": round(float(oferta.rate_final), 2),
            "tipo_renta": oferta.tipo_renta or "",
        })
    return planes

