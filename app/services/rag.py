"""
Capa de recuperación de conocimiento (RAG) sobre Supabase + pgvector.

Contrato con el orquestador: esta función NUNCA lanza una excepción y NUNCA
retorna vacío. Un fallo de red hacia Supabase, una credencial ausente o una
búsqueda sin coincidencias no deben tumbar `POST /chat`: en todos esos casos se
degrada a un bloque de políticas generales y la conversación continúa.

Recordatorio de la separación de capas: el contexto que se devuelve aquí es
material de apoyo cualitativo (políticas, reglas, definiciones). Los montos,
fechas y variaciones siguen viniendo exclusivamente del motor determinista.
Nada de lo que se recupere aquí debe usarse como fuente de cifras.
"""

from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.services import embeddings as embeddings_service

try:
    from supabase import create_client
    SUPABASE_AVAILABLE = True
except ImportError:  # la librería es opcional en entornos de solo demo
    SUPABASE_AVAILABLE = False


# Contexto simulado. Es exactamente el que devolvía la versión stub, para que
# activar USE_MOCK_RAG=True reproduzca el comportamiento previo sin sorpresas.
_CONTEXTO_MOCK = (
    "Políticas de facturación: Todo cobro por cambio de plan es proporcional "
    "a los días de uso (prorrateo). Si finaliza un descuento, el cargo fijo "
    "vuelve a su precio regular. La cuota de equipo se cobra por equipos financiados "
    "y tiene un número definido de cuotas."
)

# Red de seguridad cuando el RAG real está activo pero no devuelve nada útil
# (sin coincidencias sobre el umbral, error de red, tabla vacía). Se limita a
# principios generales de transparencia: no afirma nada específico del cliente.
_CONTEXTO_POR_DEFECTO = (
    "Políticas generales de transparencia en facturación: toda variación en el "
    "monto de un recibo debe poder explicarse con un concepto facturado concreto. "
    "Los cambios de plan se cobran de forma proporcional a los días de uso. "
    "Al finalizar un descuento promocional, el cargo vuelve a su precio regular. "
    "Los equipos financiados se cobran en cuotas de número definido. "
    "Si no hay información suficiente para explicar un cargo, corresponde derivar "
    "la consulta a un asesor humano en lugar de especular."
)

# Cliente reutilizado entre peticiones: crear uno por turno añade latencia
# innecesaria en el camino caliente del chat.
_cliente_cache = None


def _supabase_configurado() -> bool:
    return bool(settings.SUPABASE_URL and settings.SUPABASE_KEY)


def rag_real_disponible() -> bool:
    """
    ¿Están dadas todas las condiciones para consultar Supabase?
    Se comprueba antes de intentar cualquier llamada de red.
    """
    return (
        not settings.USE_MOCK_RAG
        and SUPABASE_AVAILABLE
        and _supabase_configurado()
        and embeddings_service.embeddings_disponibles()
    )


def _get_client():
    """Devuelve el cliente de Supabase, memoizado por proceso."""
    global _cliente_cache
    if _cliente_cache is None:
        _cliente_cache = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
    return _cliente_cache


def _formatear_contexto(resultados: List[Dict[str, Any]]) -> str:
    """
    Arma el bloque de contexto delimitado que se inyecta en el prompt de Lucía.
    Se marca explícitamente como material de referencia y no como fuente de
    cifras, para reforzar la regla anti-alucinación del prompt de generación.
    """
    lineas = [
        "[CONTEXTO RECUPERADO — políticas oficiales de facturación]",
        "Úsalo solo como referencia conceptual. No extraigas montos ni fechas de aquí.",
        "",
    ]
    for i, item in enumerate(resultados, start=1):
        categoria = item.get("categoria") or "GENERAL"
        similitud = item.get("similarity")
        etiqueta = f"({categoria}"
        if isinstance(similitud, (int, float)):
            etiqueta += f", similitud {similitud:.2f}"
        etiqueta += ")"
        lineas.append(f"{i}. {etiqueta} {(item.get('contenido') or '').strip()}")
    lineas.append("[FIN DEL CONTEXTO RECUPERADO]")
    return "\n".join(lineas)


def retrieve_context(query: str, categoria: Optional[str] = None) -> str:
    """
    Recupera el contexto de políticas más relevante para la consulta.

    Args:
        query: mensaje del usuario (o consulta derivada).
        categoria: filtro opcional por categoría de política. Se alinea con los
            `detected_event` del motor determinista (FIN_PROMOCION,
            PRORRATEO_CAMBIO_PLAN, CUOTA_EQUIPO, RECONEXION_MOROSIDAD,
            REDUCCION_TARIFA). None = buscar en todas las categorías, que es lo
            que hace hoy el orquestador para no perder recall.

    Returns:
        Un bloque de texto listo para inyectar en el prompt. Siempre retorna algo
        utilizable, incluso ante fallos.
    """
    # 1. Modo simulado o configuración incompleta: se conserva el comportamiento
    #    anterior sin intentar red. Es el camino por defecto en desarrollo.
    if settings.USE_MOCK_RAG:
        return _CONTEXTO_MOCK

    if not SUPABASE_AVAILABLE:
        print("[RAG] La librería 'supabase' no está instalada. Se usa contexto por defecto.")
        return _CONTEXTO_POR_DEFECTO

    if not _supabase_configurado():
        print("[RAG] SUPABASE_URL/SUPABASE_KEY no configuradas. Se usa contexto por defecto.")
        return _CONTEXTO_POR_DEFECTO

    if not embeddings_service.embeddings_disponibles():
        print(
            "[RAG] Proveedor de embeddings no disponible "
            f"({embeddings_service.describir_configuracion()}). Se usa contexto por defecto."
        )
        return _CONTEXTO_POR_DEFECTO

    if not query or not query.strip():
        return _CONTEXTO_POR_DEFECTO

    # 2. Flujo RAG real. Todo el bloque va protegido: ningún fallo de red,
    #    de credenciales o de esquema debe propagarse al endpoint /chat.
    try:
        vector = embeddings_service.embed_query(query)

        parametros: Dict[str, Any] = {
            "query_embedding": vector,
            "match_threshold": settings.RAG_MATCH_THRESHOLD,
            "match_count": settings.RAG_MATCH_COUNT,
            "filter_categoria": categoria,
        }

        respuesta = _get_client().rpc("match_documentos", parametros).execute()
        resultados = respuesta.data or []

        if not resultados:
            print(
                f"[RAG] Sin coincidencias sobre el umbral "
                f"({settings.RAG_MATCH_THRESHOLD}) para la consulta recibida."
            )
            return _CONTEXTO_POR_DEFECTO

        print(f"[RAG] {len(resultados)} chunk(s) recuperados de Supabase.")
        return _formatear_contexto(resultados)

    except Exception as e:
        # Degradación intencional: se prefiere responder con políticas generales
        # antes que romper la conversación por un problema de infraestructura.
        print(f"[RAG ERROR] Fallo consultando Supabase: {e}. Se usa contexto por defecto.")
        return _CONTEXTO_POR_DEFECTO
