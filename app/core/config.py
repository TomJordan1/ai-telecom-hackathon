from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    PROJECT_NAME: str = "Copiloto de Transparencia (Lucía)"
    VERSION: str = "0.1.0"
    API_V1_STR: str = "/api/v1"
    
    # LLM Settings
    DEEPSEEK_API_KEY: str | None = None
    USE_MOCK_LLM: bool = True  # True by default since we don't have a key yet

    # Database
    DATABASE_URL: str = "sqlite:///./lucia_brain.db"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
