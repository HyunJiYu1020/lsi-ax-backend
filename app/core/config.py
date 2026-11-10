from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "S.LSI 예산관리 플랫폼 API"
    database_url: str = "postgresql+psycopg://slsi:slsi@localhost:5432/slsi"
    cors_origins: list[str] = ["http://localhost:5173"]
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    chat_provider: str = "gemini"  # "anthropic" | "ollama" | "gemini"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma4:26b"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"

    # 업로드 원본 파일 보존용 S3. 비어 있으면(기본값) 업로드 기능은 S3 저장을 건너뛰고
    # 지금처럼 파일명 + 파싱 결과만 DB에 남긴다 — 버킷 없이도 앱이 그대로 동작한다.
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "ap-northeast-2"
    s3_upload_bucket: str = ""


settings = Settings()
