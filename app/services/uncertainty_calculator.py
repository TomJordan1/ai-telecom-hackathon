from typing import Dict, Any, Optional

from app.services.deterministic import EVENTOS_RESUELTOS

# Umbrales
HANDOFF_THRESHOLD = 0.65

def calculate_uncertainty(
    fact_payload: Dict[str, Any],
    caso_conocido: Optional[Dict],
    rag_context: Optional[str],
    compliance_triggered: bool = False
) -> float:
    """
    Calcula el índice de incertidumbre entre 0.0 (certeza absoluta) y 1.0 (máxima incertidumbre)
    usando exclusivamente señales determinísticas del backend.
    El LLM nunca recibe este número.
    """
    score = 0.5  # Punto de partida neutro

    # --- Señales que REDUCEN incertidumbre ---

    # 1. Hay un caso conocido y validado que coincide exactamente
    if caso_conocido:
        score -= 0.35

    # 2. Hay recibos suficientes para calcular ΔM (al menos 2)
    if fact_payload.get("previous_bills") and len(fact_payload["previous_bills"]) >= 1:
        score -= 0.15

    # 3. El evento detectado no es ambiguo. La lista vive en el motor
    #    determinista para que ambos módulos no puedan desincronizarse cuando
    #    se añade un evento nuevo (era el caso: aquí faltaban COMPRA_PAQUETE,
    #    TRAFICO_ADICIONAL, CAMBIO_PLAN y los ajustes por nota de crédito).
    evento = fact_payload.get("detected_event", "")
    if evento in EVENTOS_RESUELTOS:
        score -= 0.10

    # 3.b La descomposición por categoría cuadra con la variación total: la
    #     explicación está respaldada al céntimo por cargos reales del recibo.
    componentes = fact_payload.get("variacion_por_categoria") or []
    if componentes:
        suma = round(sum(c.get("impacto", 0.0) for c in componentes), 2)
        if abs(suma - float(fact_payload.get("variation_amount", 0.0))) < 0.05:
            score -= 0.10

    # 4. Hay evidencia explícita
    if fact_payload.get("evidence"):
        score -= 0.05

    # --- Señales que AUMENTAN incertidumbre ---

    # 5. El evento no fue reconocido
    if evento in ("SIN_CAMBIOS", "INCREMENTO_OTROS", "CONSULTA_GENERAL", ""):
        score += 0.20

    # 6. No hay datos de recibos
    if "error" in fact_payload:
        score += 0.30

    # 7. Compliance activado (zona sensible)
    if compliance_triggered:
        score += 0.30

    # Clamp entre 0.0 y 1.0
    return round(max(0.0, min(1.0, score)), 3)


def requires_handoff(uncertainty_score: float) -> bool:
    return uncertainty_score >= HANDOFF_THRESHOLD
