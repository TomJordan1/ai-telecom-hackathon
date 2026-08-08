from sqlalchemy.orm import Session
from app.db import crud
from typing import Dict, Any, Optional, Tuple

def match_caso(db: Session, fact_payload: Dict[str, Any]) -> Optional[Tuple[str, Dict]]:
    """
    Busca en base_casos si existe una solución validada para el patrón detectado.
    Retorna (caso_id, solucion_estructurada) si hay match, o None si es un caso nuevo.
    
    La coincidencia es determinista (no semántica): compara el 'detected_event'
    directamente contra los patrones registrados en la base de casos.
    """
    patron = fact_payload.get("detected_event")
    if not patron or patron in ("SIN_CAMBIOS", "NUEVO_CLIENTE", "CONSULTA_GENERAL"):
        return None

    caso = crud.get_caso_conocido(db, patron)
    if caso:
        # Incrementar el contador de reutilización
        crud.increment_caso_aplicado(db, caso.id)
        return (caso.id, caso.solucion_estructurada)

    return None
