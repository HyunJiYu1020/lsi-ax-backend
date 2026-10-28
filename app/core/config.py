from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "S.LSI 예산관리 플랫폼 API"
    database_url: str = "postgresql+psycopg://slsi:slsi@localhost:5432/slsi"
    cors_origins: list[str] = ["http://localhost:5173"]
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    chat_provider: str = "anthropic"  # "anthropic" | "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma4:26b"


settings = Settings()
