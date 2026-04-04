from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "AP_"}

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/account_payables"
    tenant_db_prefix: str = "ap_"

    # Auth / JWT
    secret_key: str = "change-me-in-production"
    access_token_expire_minutes: int = 30

    # S3 / MinIO
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "invoices"

    # Extraction
    extraction_mode: str = "local"  # "local" = in-process, "lambda" = dispatch to SQS
    sqs_extraction_queue_url: str = ""  # required when extraction_mode = "lambda"

    # CORS
    cors_origins: list[str] = ["http://localhost:7777", "http://localhost:5173"]

    # App
    debug: bool = True


settings = Settings()
