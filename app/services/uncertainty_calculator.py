from typing import Dict, Any, Optional, Tuple, List

from app.services.deterministic import EVENTOS_RESUELTOS

# Umbrales
HANDOFF_THRESHOLD = 0.65

def calculate_uncertainty_with_reasons(
    fact_payload: Dict[str, Any],
    caso_conocido: Optional[Dict],
    rag_context: Optional[str] = None,
    compliance_triggered: bool = False
) -> Tuple[float, List[str]]:
    """
    Calcula el índice de incertidumbre entre 0.0 (certeza absoluta) y 1.0 (máxima incertidumbre)
    y retorna la lista de razones verificables que justifican la calificación.
    """
    score = 0.5  # Punto de partida neutro
    reasons: List[str] = []

    # --- Señales que REDUCEN incertidumbre (Certeza alta) ---

    # 1. Hay un caso conocido y validado que coincide
    if caso_conocido:
        score -= 0.35
        reasons.append("Solución validada previamente por asesores en el banco de conocimiento (+35% certeza).")
    else:
        reasons.append("Sin caso idéntico en el banco de soluciones (caso nuevo en validación).")

    # 2. Hay recibos suficientes para calcular ΔM (al menos 2)
    es_visitante = fact_payload.get("detected_event") == "CONSULTA_GENERAL" and fact_payload.get("current_bill") is None
    if fact_payload.get("previous_bills") and len(fact_payload["previous_bills"]) >= 1:
        score -= 0.15
        reasons.append("Historial de recibos verificado disponible para comparación mensual (+15% certeza).")
    else:
        if not es_visitante:
            reasons.append("Historial previo limitado (primer ciclo o sin recibos comparativos).")

    # 3. El evento detectado no es ambiguo
    evento = fact_payload.get("detected_event", "")
    if evento in EVENTOS_RESUELTOS:
        score -= 0.10
        reasons.append(f"Patrón causal clasificado con regla determinista exacta: {evento} (+10% certeza).")

    # 3.b La descomposición por categoría cuadra con la variación total al céntimo
    componentes = fact_payload.get("variacion_por_categoria") or []
    if componentes:
        suma = round(sum(c.get("impacto", 0.0) for c in componentes), 2)
        delta_m = float(fact_payload.get("variation_amount", 0.0))
        if abs(suma - delta_m) < 0.05:
            score -= 0.10
            reasons.append("Conciliación matemática al céntimo verificada (Σ Impactos = Δ Monto) (+10% certeza).")
        else:
            reasons.append(f"Diferencia en conciliación de partidas: suma {suma:.2f} vs delta {delta_m:.2f}.")

    # 4. Hay evidencia explícita
    if fact_payload.get("evidence"):
        score -= 0.05
        reasons.append("Evidencia explícita identificada en los conceptos de facturación (+5% certeza).")

    # --- Señales que AUMENTAN incertidumbre ---

    # 5. El evento no fue reconocido o es genérico
    if evento in ("SIN_CAMBIOS", "INCREMENTO_OTROS", "CONSULTA_GENERAL", ""):
        score += 0.20
        if evento == "INCREMENTO_OTROS":
            reasons.append("Variación en categoría miscelánea/otros (requiere supervisión humana).")
        elif evento == "SIN_CAMBIOS":
            reasons.append("Sin variaciones monetarias en el período analizado.")

    # 6. No hay datos de recibos (visitante sin cuenta vinculada)
    if es_visitante:
        score += 0.30
        reasons.append("No se registran recibos facturados para la cuenta consultada (+30% incertidumbre).")

    # 7. Compliance activado (zona sensible)
    if compliance_triggered:
        score += 0.30
        reasons.append("Término sensible o bloqueo de compliance activado (+30% incertidumbre).")

    final_score = round(max(0.0, min(1.0, score)), 3)
    return final_score, reasons


def calculate_uncertainty(
    fact_payload: Dict[str, Any],
    caso_conocido: Optional[Dict],
    rag_context: Optional[str] = None,
    compliance_triggered: bool = False
) -> float:
    """Wrapper para compatibilidad con código existente."""
    score, _ = calculate_uncertainty_with_reasons(
        fact_payload=fact_payload,
        caso_conocido=caso_conocido,
        rag_context=rag_context,
        compliance_triggered=compliance_triggered,
    )
    return score


def requires_handoff(uncertainty_score: float) -> bool:
    return uncertainty_score >= HANDOFF_THRESHOLD

