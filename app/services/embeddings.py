"""
Proveedor único de embeddings.

Lo usan tanto el script de ingesta (`scripts/ingest_supabase.py`) como el
retriever (`app/services/rag.py`). Tener un solo punto de verdad evita el error
más común de un RAG con pgvector: indexar con un modelo y consultar con otro,
lo que produce dimensiones incompatibles o similitudes sin sentido.

Dos proveedores soportados:

- "local"   -> all-MiniLM-L6-v2 (384 dimensiones). Sin API externa ni costo.
              Usa `fastembed` (ONNX, ~80 MB) si está instalado, y si no cae a
              `sentence-transformers` (~2 GB con torch). Ambos ejecutan el mismo
              modelo, así que los vectores son intercambiables entre backends.
- "openai"  -> text-embedding-3-small (1536 dimensiones). Requiere OPENAI_API_KEY.

IMPORTANTE: DeepSeek no expone endpoint de embeddings (su API es solo chat
completions), por lo que DEEPSEEK_API_KEY no sirve para vectorizar. Si no quieres
una segunda API de pago, usa EMBEDDING_PROVIDER=local.

La dimensión resultante debe coincidir con la declarada en
`scripts/setup_supabase.sql`.
"""

from typing import List, Optional

from app.core.config import settings

PROVEEDOR_OPENAI = "openai"
PROVEEDOR_LOCAL = "local"

BACKEND_FASTEMBED = "fastembed"
BACKEND_SENTENCE_TRANSFORMERS = "sentence-transformers"

MODELO_OPENAI_POR_DEFECTO = "text-embedding-3-small"
MODELO_LOCAL_POR_DEFECTO = "all-MiniLM-L6-v2"

# fastembed identifica los modelos con el prefijo del repo de HuggingFace.
# Se acepta el nombre corto en la configuración y se traduce aquí.
_ALIAS_FASTEMBED = {
    "all-MiniLM-L6-v2": "sentence-transformers/all-MiniLM-L6-v2",
    "all-mpnet-base-v2": "sentence-transformers/all-mpnet-base-v2",
}

# Dimensiones conocidas por modelo. Sirven para avisar temprano de un desajuste
# contra el esquema de Supabase en vez de fallar con un error opaco de Postgres.
DIMENSIONES_CONOCIDAS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    "all-MiniLM-L6-v2": 384,
    "all-mpnet-base-v2": 768,
}

# Modelo cargado una sola vez por proceso (inicializar el runtime de ONNX o de
# torch en cada turno de chat sería inaceptablemente lento).
_modelo_cache = None
_backend_local_cache: Optional[str] = None


class EmbeddingsNoDisponibles(RuntimeError):
    """El proveedor de embeddings no está configurado o no se pudo inicializar."""


def proveedor_activo() -> str:
    return (settings.EMBEDDING_PROVIDER or PROVEEDOR_OPENAI).strip().lower()


def modelo_activo() -> str:
    if settings.EMBEDDING_MODEL:
        return settings.EMBEDDING_MODEL
    return (
        MODELO_LOCAL_POR_DEFECTO
        if proveedor_activo() == PROVEEDOR_LOCAL
        else MODELO_OPENAI_POR_DEFECTO
    )


def dimension_esperada() -> Optional[int]:
    """Dimensión del vector que produce el modelo activo, si se conoce."""
    nombre = modelo_activo()
    if nombre in DIMENSIONES_CONOCIDAS:
        return DIMENSIONES_CONOCIDAS[nombre]
    # Tolera que se configure el nombre completo con prefijo de HuggingFace.
    return DIMENSIONES_CONOCIDAS.get(nombre.split("/")[-1])


def _backend_local_disponible() -> Optional[str]:
    """
    Elige el backend local instalado, prefiriendo fastembed por ser mucho más
    liviano. Retorna None si no hay ninguno.
    """
    try:
        import fastembed  # noqa: F401
        return BACKEND_FASTEMBED
    except ImportError:
        pass
    try:
        import sentence_transformers  # noqa: F401
        return BACKEND_SENTENCE_TRANSFORMERS
    except ImportError:
        return None


def embeddings_disponibles() -> bool:
    """
    ¿Se puede generar un embedding ahora mismo? Comprobación barata (sin cargar
    el modelo) para decidir si el RAG real es viable o hay que ir al fallback.
    """
    proveedor = proveedor_activo()

    if proveedor == PROVEEDOR_LOCAL:
        return _backend_local_disponible() is not None

    if proveedor == PROVEEDOR_OPENAI:
        if not settings.OPENAI_API_KEY:
            return False
        try:
            import langchain_openai  # noqa: F401
        except ImportError:
            return False
        return True

    return False


def _cargar_modelo():
    """Inicializa (y memoiza) el cliente/modelo de embeddings."""
    global _modelo_cache, _backend_local_cache
    if _modelo_cache is not None:
        return _modelo_cache

    proveedor = proveedor_activo()
    nombre_modelo = modelo_activo()

    if proveedor == PROVEEDOR_LOCAL:
        backend = _backend_local_disponible()

        if backend == BACKEND_FASTEMBED:
            from fastembed import TextEmbedding

            nombre_fastembed = _ALIAS_FASTEMBED.get(nombre_modelo, nombre_modelo)
            # La primera inicialización descarga el modelo (~80 MB) y lo cachea
            # en disco; las siguientes son instantáneas.
            _modelo_cache = TextEmbedding(model_name=nombre_fastembed)
            _backend_local_cache = BACKEND_FASTEMBED
            return _modelo_cache

        if backend == BACKEND_SENTENCE_TRANSFORMERS:
            from sentence_transformers import SentenceTransformer

            _modelo_cache = SentenceTransformer(nombre_modelo)
            _backend_local_cache = BACKEND_SENTENCE_TRANSFORMERS
            return _modelo_cache

        raise EmbeddingsNoDisponibles(
            "No hay backend local instalado. Instala el liviano con: "
            "pip install fastembed"
        )

    if proveedor == PROVEEDOR_OPENAI:
        if not settings.OPENAI_API_KEY:
            raise EmbeddingsNoDisponibles(
                "OPENAI_API_KEY no está configurada: es necesaria para el proveedor "
                "'openai'. Nota: DEEPSEEK_API_KEY no sirve aquí, DeepSeek no expone "
                "endpoint de embeddings. Usa EMBEDDING_PROVIDER=local si no quieres "
                "una key de OpenAI."
            )
        try:
            from langchain_openai import OpenAIEmbeddings
        except ImportError as e:
            raise EmbeddingsNoDisponibles(
                "langchain-openai no está instalado. Ejecuta: pip install -r requirements.txt"
            ) from e

        _modelo_cache = OpenAIEmbeddings(
            model=nombre_modelo,
            api_key=settings.OPENAI_API_KEY,
        )
        return _modelo_cache

    raise EmbeddingsNoDisponibles(
        f"EMBEDDING_PROVIDER='{proveedor}' no reconocido. Usa 'local' u 'openai'."
    )


def embed_query(texto: str) -> List[float]:
    """Vectoriza una consulta del usuario."""
    modelo = _cargar_modelo()

    if proveedor_activo() == PROVEEDOR_LOCAL:
        if _backend_local_cache == BACKEND_FASTEMBED:
            # fastembed devuelve un generador de arrays de numpy.
            vector = next(iter(modelo.embed([texto])))
            return [float(x) for x in vector]
        return [float(x) for x in modelo.encode(texto, normalize_embeddings=True)]

    return modelo.embed_query(texto)


def embed_documents(textos: List[str]) -> List[List[float]]:
    """Vectoriza un lote de documentos para la ingesta."""
    modelo = _cargar_modelo()

    if proveedor_activo() == PROVEEDOR_LOCAL:
        if _backend_local_cache == BACKEND_FASTEMBED:
            return [[float(x) for x in v] for v in modelo.embed(textos)]
        vectores = modelo.encode(textos, normalize_embeddings=True)
        return [[float(x) for x in v] for v in vectores]

    return modelo.embed_documents(textos)


def describir_configuracion() -> str:
    """Resumen legible para logs y para la salida del script de ingesta."""
    dim = dimension_esperada()
    descripcion = f"proveedor={proveedor_activo()} modelo={modelo_activo()}"
    if proveedor_activo() == PROVEEDOR_LOCAL:
        backend = _backend_local_cache or _backend_local_disponible() or "ninguno"
        descripcion += f" backend={backend}"
    descripcion += f" dimensiones={dim if dim else 'desconocidas'}"
    return descripcion
