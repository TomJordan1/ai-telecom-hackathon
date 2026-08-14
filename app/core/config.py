from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    PROJECT_NAME: str = "Copiloto de Transparencia (Lucía)"
    # 0.2.0 = RAG sobre Supabase/pgvector + almacenamiento operacional en Postgres.
    # Sirve además para verificar qué versión está realmente desplegada.
    VERSION: str = "0.2.0"
    API_V1_STR: str = "/api/v1"
    
    # LLM Settings
    DEEPSEEK_API_KEY: str | None = None
    USE_MOCK_LLM: bool = True  # True by default since we don't have a key yet

    # Database
    DATABASE_URL: str = "sqlite:///./lucia_brain.db"

    # Cuenta financiera (FINANCIAL_ACCOUNT del dataset real) que se usa cuando
    # un canal externo no logra identificar al cliente: por ejemplo, un número
    # de WhatsApp que no está registrado en contactos_usuario. Si se deja vacío,
    # el sistema resuelve una cuenta con historial desde la propia base, así que
    # la demo nunca depende de un identificador escrito a mano en el código.
    DEMO_ACCOUNT_ID: str | None = None

    # --- Capa RAG (Supabase + pgvector) ---
    SUPABASE_URL: str | None = None
    SUPABASE_KEY: str | None = None
    # True por defecto para poder desarrollar y demostrar sin credenciales:
    # el RAG devuelve el contexto simulado en vez de fallar.
    USE_MOCK_RAG: bool = True

    # Proveedor de embeddings: "openai" (text-embedding-3-small, 1536 dims)
    # o "local" (SentenceTransformers all-MiniLM-L6-v2, 384 dims).
    # La dimensión debe coincidir con la definida en scripts/setup_supabase.sql.
    EMBEDDING_PROVIDER: str = "openai"
    EMBEDDING_MODEL: str | None = None  # None = modelo por defecto del proveedor
    # Clave de OpenAI SOLO para embeddings. Es distinta de DEEPSEEK_API_KEY:
    # DeepSeek no expone endpoint de embeddings, así que no se puede reutilizar.
    OPENAI_API_KEY: str | None = None

    # Umbral de similitud y top-k del retriever. El diseño original propone un
    # umbral alto (0.85) para demo; se deja configurable para poder ajustarlo
    # con experimentación sin tocar código.
    RAG_MATCH_THRESHOLD: float = 0.5
    RAG_MATCH_COUNT: int = 3

    # WhatsApp Cloud API Settings
    WHATSAPP_TOKEN: str | None = None
    WHATSAPP_PHONE_ID: str | None = None
    WHATSAPP_VERIFY_TOKEN: str = "lucia_hackathon_secret"
    # App Secret de Meta (App Settings > Basic). Habilita la verificación de la
    # firma X-Hub-Signature-256 de los eventos entrantes. Si queda sin definir,
    # el webhook sigue aceptando eventos pero lo advierte en el log.
    WHATSAPP_APP_SECRET: str | None = None
    # Versión de la Graph API. Meta retira las versiones antiguas (v17.0 ya está
    # deprecada), así que se deja configurable: copia la que muestre el panel
    # de Meta for Developers > WhatsApp > API Setup en su ejemplo de curl.
    WHATSAPP_API_VERSION: str = "v26.0"

    # Telegram Bot Settings (usado tanto por scripts/telegram_bot.py como por
    # el envío proactivo saliente desde el backend)
    TELEGRAM_TOKEN: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
