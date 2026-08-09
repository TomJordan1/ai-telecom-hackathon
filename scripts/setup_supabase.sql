-- =============================================================================
--  Lucía — Copiloto de Transparencia de Facturación
--  Esquema del servicio RAG en Supabase (PostgreSQL + pgvector)
-- =============================================================================
--
--  CÓMO EJECUTARLO
--  1. Entra a tu proyecto en https://supabase.com
--  2. Abre "SQL Editor" > "New query"
--  3. Pega este archivo completo y ejecútalo (Run)
--
--  DIMENSIONES DEL VECTOR (IMPORTANTE)
--  Este script está configurado para 384 dimensiones, que corresponde al modelo
--  local `all-MiniLM-L6-v2` (EMBEDDING_PROVIDER=local en .env). Es la opción sin
--  API externa ni costo, y la recomendada para este proyecto.
--
--  Si prefieres los embeddings de OpenAI (`text-embedding-3-small`,
--  EMBEDDING_PROVIDER=openai), cambia 384 por 1536 en LOS DOS lugares donde
--  aparece: la columna `embedding` de la tabla y el parámetro `query_embedding`
--  de la función `match_documentos`.
--
--  DeepSeek NO sirve para esto: su API solo expone chat completions, no tiene
--  endpoint de embeddings.
--
--  La dimensión de la tabla y la del modelo deben coincidir exactamente, o las
--  inserciones fallarán con un error de tipo.
--
--  SI YA EJECUTASTE ESTE SCRIPT CON OTRA DIMENSIÓN
--  Una columna VECTOR no se puede redimensionar en caliente. Ejecuta primero:
--      DROP TABLE IF EXISTS documentos_politicas CASCADE;
--  y luego este script completo. Se pierden los chunks ya cargados, pero se
--  recuperan en segundos volviendo a correr scripts/ingest_supabase.py.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. Habilitar la extensión de vectores
-- -----------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS vector;


-- -----------------------------------------------------------------------------
-- 2. Tabla de chunks de políticas y sus embeddings
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documentos_politicas (
    id          BIGSERIAL PRIMARY KEY,
    contenido   TEXT NOT NULL,
    -- La categoría se alinea a propósito con los `detected_event` del motor
    -- determinista (FIN_PROMOCION, PRORRATEO_CAMBIO_PLAN, CUOTA_EQUIPO,
    -- RECONEXION_MOROSIDAD, REDUCCION_TARIFA) más 'GENERAL', para poder
    -- filtrar la búsqueda por el evento ya detectado cuando convenga.
    categoria   VARCHAR(50) DEFAULT 'GENERAL',
    fuente      VARCHAR(100) DEFAULT 'manual_politicas',
    embedding   VECTOR(384),    -- 384 = all-MiniLM-L6-v2 (local) | 1536 = OpenAI text-embedding-3-small
    metadata    JSONB DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);


-- -----------------------------------------------------------------------------
-- 3. Índices
-- -----------------------------------------------------------------------------
-- HNSW con distancia de coseno: búsqueda aproximada rápida por similitud.
-- Requiere pgvector >= 0.5 (disponible en Supabase).
CREATE INDEX IF NOT EXISTS idx_documentos_embedding_hnsw
    ON documentos_politicas USING hnsw (embedding vector_cosine_ops);

-- Índice de apoyo para el filtrado por categoría.
CREATE INDEX IF NOT EXISTS idx_documentos_categoria
    ON documentos_politicas (categoria);


-- -----------------------------------------------------------------------------
-- 4. Función RPC de búsqueda semántica
-- -----------------------------------------------------------------------------
-- Se elimina primero por si ya existe con otra firma: CREATE OR REPLACE no
-- permite cambiar el tipo de retorno de una función existente.
DROP FUNCTION IF EXISTS match_documentos(VECTOR(384), FLOAT, INT, TEXT);
DROP FUNCTION IF EXISTS match_documentos(VECTOR(1536), FLOAT, INT, TEXT);

CREATE OR REPLACE FUNCTION match_documentos (
    query_embedding  VECTOR(384),
    match_threshold  FLOAT DEFAULT 0.5,
    match_count      INT   DEFAULT 3,
    filter_categoria TEXT  DEFAULT NULL
)
RETURNS TABLE (
    id         BIGINT,
    contenido  TEXT,
    categoria  TEXT,
    similarity FLOAT
)
LANGUAGE sql STABLE
AS $$
    SELECT
        dp.id,
        dp.contenido,
        dp.categoria::TEXT,
        1 - (dp.embedding <=> query_embedding) AS similarity
    FROM documentos_politicas dp
    WHERE
        dp.embedding IS NOT NULL
        AND (1 - (dp.embedding <=> query_embedding)) >= match_threshold
        AND (filter_categoria IS NULL OR dp.categoria = filter_categoria)
    ORDER BY similarity DESC
    LIMIT match_count;
$$;


-- -----------------------------------------------------------------------------
-- 5. Seguridad (Row Level Security)
-- -----------------------------------------------------------------------------
-- Con RLS habilitada y SIN políticas permisivas, la clave `anon` (la que puede
-- terminar expuesta en un cliente) no puede leer ni escribir esta tabla,
-- mientras que la clave `service_role` (solo backend) sigue teniendo acceso
-- completo porque omite RLS por diseño.
--
-- Recomendación: usa la clave `service_role` en SUPABASE_KEY del backend y
-- NUNCA la publiques en el frontend ni la subas al repositorio.
ALTER TABLE documentos_politicas ENABLE ROW LEVEL SECURITY;

-- Si necesitas lectura pública del catálogo de políticas (por ejemplo, para
-- una demo desde el navegador con la clave anon), descomenta esta política.
-- Ten en cuenta que expone el contenido completo de las políticas a cualquiera.
--
-- CREATE POLICY "lectura_publica_politicas"
--     ON documentos_politicas
--     FOR SELECT
--     TO anon, authenticated
--     USING (true);


-- -----------------------------------------------------------------------------
-- 6. Verificación rápida
-- -----------------------------------------------------------------------------
-- Ejecuta esto después de correr `python scripts/ingest_supabase.py`:
--
-- SELECT categoria, COUNT(*) AS chunks
-- FROM documentos_politicas
-- GROUP BY categoria
-- ORDER BY categoria;
