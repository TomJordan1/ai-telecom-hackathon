import math
from typing import Dict, Any, Optional, Tuple, List
from sqlalchemy.orm import Session
from app.db import crud, models
from app.services import embeddings


def _cosine_similarity(v1: List[float], v2: List[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0
    return dot / (norm1 * norm2)


def match_caso(
    db: Session,
    fact_payload: Dict[str, Any],
    user_message: Optional[str] = None
) -> Optional[Tuple[str, Dict]]:
    """
    Busca en base_casos si existe una solución validada para el patrón o consulta.
    Retorna (caso_id, solucion_estructurada) si hay match, o None si es un caso nuevo.
    
    Estrategia de matching:
      1. Similitud semántica de embeddings vectoriales sobre la consulta del usuario.
      2. Coincidencia determinista directa sobre 'detected_event'.
    """
    patron = fact_payload.get("detected_event")

    # 1. Matching Semántico Vectorial
    if user_message and embeddings.embeddings_disponibles():
        try:
            query_vec = embeddings.embed_query(user_message)
            casos_activos = db.query(models.BaseCasos).filter(models.BaseCasos.activo == True).all()

            mejor_caso = None
            mejor_score = 0.0
            UMBRAL_SIMILITUD = 0.78

            for caso in casos_activos:
                condiciones = dict(caso.condiciones or {})
                caso_vec = condiciones.get("embedding")

                if not caso_vec:
                    texto_ejemplo = condiciones.get("user_message") or condiciones.get("query_ejemplo") or caso.patron_problema
                    if texto_ejemplo:
                        caso_vec = embeddings.embed_query(str(texto_ejemplo))
                        condiciones["embedding"] = caso_vec
                        caso.condiciones = condiciones
                        db.commit()

                if caso_vec:
                    sim = _cosine_similarity(query_vec, caso_vec)
                    if sim > mejor_score and sim >= UMBRAL_SIMILITUD:
                        # Si difiere en evento determinista marcado, requerir mayor similitud
                        if patron and caso.patron_problema and caso.patron_problema != patron:
                            if sim < 0.85:
                                continue
                        mejor_score = sim
                        mejor_caso = caso

            if mejor_caso:
                crud.increment_caso_aplicado(db, mejor_caso.id)
                print(f"[CASE_MATCHER] Match semántico: caso={mejor_caso.id} score={mejor_score:.3f}")
                return (mejor_caso.id, mejor_caso.solucion_estructurada)
        except Exception as e:
            print(f"[CASE_MATCHER WARNING] Error en matching semántico: {e}")

    # 2. Matching determinista tradicional por detected_event
    if not patron or patron in ("SIN_CAMBIOS", "NUEVO_CLIENTE", "CONSULTA_GENERAL"):
        return None

    caso = crud.get_caso_conocido(db, patron)
    if caso:
        crud.increment_caso_aplicado(db, caso.id)
        return (caso.id, caso.solucion_estructurada)

    return None

