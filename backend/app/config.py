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

    # ERP
    erp_mode: str = "local"  # "local" = in-process, "lambda" = dispatch to SQS
    sqs_erp_queue_url: str = ""  # required when erp_mode = "lambda"

    # Audit
    audit_mode: str = "local"  # "local" = in-process, "lambda" = dispatch to SQS
    sqs_audit_queue_url: str = ""  # required when audit_mode = "lambda"

    # AI Extraction (platform-level key — used when customers choose "Platform" extraction)
    anthropic_api_key: str = ""  # your Anthropic API key for Claude Vision
    extraction_model: str = "claude-sonnet-4-20250514"

    # Virtual Cards (platform-level keys — used when customers choose "Platform" card program)
    lithic_api_key: str = ""  # your Lithic API key
    lithic_sandbox: bool = True
    nium_client_id: str = ""  # your Nium client ID
    nium_client_secret: str = ""
    nium_customer_hash_id: str = ""
    nium_wallet_hash_id: str = ""
    nium_sandbox: bool = True

    # Redis
    redis_url: str = "redis://localhost:6379"

    # CORS
    cors_origins: list[str] = ["http://localhost:7777", "http://localhost:5173"]

    # Self-service signup
    email_provider: str = "console"  # "console" (dev default) | "ses"
    email_from: str = "no-reply@localhost"
    aws_ses_region: str = "us-east-1"
    public_url: str = "http://localhost:7777"  # where the frontend is served
    tenant_url_template: str = "http://{slug}.localhost:7777"
    hcaptcha_secret: str = ""  # empty = skip captcha verification
    hcaptcha_sitekey: str = ""  # exposed to frontend via a public endpoint
    signup_rate_limit_per_hour: int = 5

    # RAG / embeddings
    rag_enabled: bool = True
    rag_top_k: int = 3
    embedding_provider: str = "mock"  # "mock" (dev default) | "openai"
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    # Cosine similarity above which a new invoice is flagged as a likely
    # duplicate of an already-stored one. Tighter than rag_top_k retrieval:
    # RAG wants semantically related invoices; dup detection wants near-
    # identical ones.
    duplicate_similarity_threshold: float = 0.95

    # App
    debug: bool = True


settings = Settings()
