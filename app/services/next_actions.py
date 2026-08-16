"""
Motor determinista de recomendación de Siguientes Acciones (Next Best Actions).

Permite orientar la autogestión del cliente tras cada turno conversacional
ofreciendo botones de acción inmediata:
  - 💳 Pagar recibo con saldo pendiente
  - 📊 Ver desglose visual detallado
  - ✨ Explorar plan comercial recomendado
  - 👤 Solicitar derivación a un asesor humano con contexto
  - 🔑 Vincular cuenta o consultar servicios
  - ✅ Registrar conformidad de consulta resuelta
"""

from typing import Dict, Any, List, Optional
from app.core.schemas import NextBestAction, ChatResponse, ChatRequest


def resolve_next_actions(
    fact_payload: Optional[Dict[str, Any]],
    response: ChatResponse,
    request: Optional[ChatRequest] = None,
) -> List[NextBestAction]:
    """
    Determina de forma no invasiva y determinista la lista de siguientes mejores
    acciones contextuales para la sesión actual.
    """
    actions: List[NextBestAction] = []

    # 1. Caso: Derivación a atención humana (Control y elección del canal por el cliente)
    if response.requires_human_intervention:
        actions.append(
            NextBestAction(
                id="CANAL_CHAT",
                titulo="💬 Continuar por este chat",
                tipo="derivacion",
                payload={"canal": "CHAT", "motivo": response.intent_category},
            )
        )
        actions.append(
            NextBestAction(
                id="CANAL_LLAMADA",
                titulo="📞 Solicitar llamada telefónica",
                tipo="derivacion",
                payload={"canal": "LLAMADA", "motivo": response.intent_category},
            )
        )
        actions.append(
            NextBestAction(
                id="CANAL_WHATSAPP",
                titulo="📲 Continuar por WhatsApp",
                tipo="derivacion",
                payload={"canal": "WHATSAPP", "motivo": response.intent_category},
            )
        )
        return actions

    # 2. Caso: Usuario visitante sin cuenta vinculada (solo cuando el intent
    #    indica explícitamente que no hay cuenta, no en turnos conversacionales
    #    genéricos donde fact_payload es None porque no se tocó facturación).
    es_visitante_sin_cuenta = response.intent_category in ("CONSULTA_SIN_CUENTA", "CONSULTA_GENERAL_PLANES")
    tiene_cuenta = bool(request and request.user_id)
    if es_visitante_sin_cuenta or (not fact_payload and not tiene_cuenta):
        if not tiene_cuenta:
            actions.append(
                NextBestAction(
                    id="VINCULAR_CUENTA",
                    titulo="🔑 Vincular mi cuenta financiera",
                    tipo="consulta",
                    payload={"action": "prompt_account"},
                )
            )
        actions.append(
            NextBestAction(
                id="EXPLORE_PLANS",
                titulo="📱 Conocer planes móviles y fibra",
                tipo="comercial",
                payload={"query": "Planes móviles y fibra óptica disponibles"},
            )
        )
        actions.append(
            NextBestAction(
                id="HOW_IT_WORKS",
                titulo="❓ ¿Cómo funciona la facturación?",
                tipo="consulta",
                payload={"query": "¿Cómo funciona el ciclo de facturación?"},
            )
        )
        return actions

    # 3. Caso: Cliente con datos verificados de facturación.
    #    Si no hay fact_payload (turno conversacional de usuario con cuenta),
    #    devolvemos acciones genéricas sin pedir vinculación.
    if not fact_payload:
        actions.append(
            NextBestAction(
                id="ASK_BILLING",
                titulo="🧾 Consultar mi factura",
                tipo="consulta",
                payload={"query": "¿Cuál es el estado de mi factura actual?"},
            )
        )
        actions.append(
            NextBestAction(
                id="REGISTER_RESOLVED",
                titulo="✅ Todo claro, gracias",
                tipo="consulta",
                payload={"status": "resolved"},
            )
        )
        return actions

    current_bill = fact_payload.get("current_bill") or {}
    monto_actual = float(current_bill.get("amount", 0.0))
    periodo_actual = current_bill.get("issue_date", "")

    # A) Acción de Pago: si el monto es mayor a 0 y no está en derivación
    if monto_actual > 0:
        actions.append(
            NextBestAction(
                id="PAY_BILL",
                titulo=f"💳 Pagar recibo (S/ {monto_actual:.2f})",
                tipo="pago",
                payload={
                    "amount": monto_actual,
                    "periodo": periodo_actual,
                    "ciclo": current_bill.get("ciclo", ""),
                },
            )
        )

    # B) Acción de Desglose Detallado: si hay conceptos o variación
    tiene_desglose = bool(current_bill.get("desglose")) or bool(fact_payload.get("variacion_por_categoria"))
    if tiene_desglose:
        actions.append(
            NextBestAction(
                id="VIEW_BREAKDOWN",
                titulo="📊 Ver desglose detallado",
                tipo="consulta",
                payload={"section": "breakdown"},
            )
        )

    # C) Acción Comercial (si hay plan recomendado disponible)
    suggestion = response.plan_optimizer_suggestion
    if suggestion and suggestion.available and suggestion.plan_recomendado:
        plan = suggestion.plan_recomendado
        actions.append(
            NextBestAction(
                id="EXPLORE_PLANS",
                titulo=f"✨ Me interesa {plan.nombre}",
                tipo="comercial",
                payload={"plan_nombre": plan.nombre, "precio": plan.precio},
            )
        )

    # D) Acción de Derivación o Conformidad según sentimiento
    if response.sentiment_score <= 2 or response.confidence_score < 70:
        actions.append(
            NextBestAction(
                id="HANDOFF_AGENT",
                titulo="👤 Hablar con un asesor",
                tipo="derivacion",
                payload={"motivo": "DISCONFORMIDAD_O_DUDA"},
            )
        )
    elif len(actions) < 4:
        actions.append(
            NextBestAction(
                id="REGISTER_RESOLVED",
                titulo="✅ Todo claro, gracias",
                tipo="consulta",
                payload={"status": "resolved"},
            )
        )

    # Limitar a máximo 4 acciones para no saturar la interfaz
    return actions[:4]
