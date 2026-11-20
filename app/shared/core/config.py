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

    # 업로드 원본 파일 보존용 S3. 비어 있으면 아래 upload_local_dir(로컬 폴더)에 저장한다.
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "ap-northeast-2"
    s3_upload_bucket: str = ""
    # S3가 설정되지 않았을 때 원본을 쌓아 둘 로컬 폴더(backend 기준 상대경로).
    # 업로드 원본은 "틀어진 값을 되돌릴 유일한 근거"라서, 버킷이 준비되기 전에도 반드시 남겨야 한다.
    # 나중에 버킷이 생기면 s3_upload_bucket만 채우면 되고 호출부는 손댈 필요가 없다
    # (저장 위치가 달라져도 uploads.s3_key에 들어가는 "키" 모양은 똑같이 유지한다).
    upload_local_dir: str = "var/uploads"


settings = Settings()
