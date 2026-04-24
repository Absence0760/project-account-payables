from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "AP_"}

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/account_payables"
    tenant_db_prefix: str = "ap_"

    # Auth / JWT
    secret_key: str = "change-me-in-production"
    access_token_expire_minutes: int = 30
    # Maximum concurrent sessions per user. When a user logs in and already
    # has this many active sessions, the oldest JTI is evicted onto the Redis
    # blocklist. Set to 0 to disable the cap. Default 5 — a reasonable mix of
    # desktop + mobile + tablet without encouraging shared credentials.
    max_concurrent_sessions: int = 5

    # S3 / MinIO
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "invoices"

    # Extraction
    extraction_mode: str = "local"  # "local" = in-process, "lambda" = dispatch to SQS
    sqs_extraction_queue_url: str = ""  # required when extraction_mode = "lambda"
    # Background reaper that transitions invoices stuck in `pending` (extraction
    # timed out, worker crashed, Ollama hung, etc.) to `failed` so the reviewer
    # can retry. Threshold = how long an invoice may sit in `pending` before
    # being declared dead. Interval = how often the reaper sweeps.
    extraction_timeout_seconds: int = 600
    extraction_reaper_interval_seconds: int = 60
    extraction_reaper_enabled: bool = True
    # Run Tesseract OSD on rendered PDF pages before sending to vision adapters,
    # rotating 90/180/270-off-upright scans back to upright. Safe to leave on —
    # a missing ``pytesseract`` / ``tesseract`` binary degrades to a silent no-op.
    extraction_auto_rotate: bool = True

    # Email-to-invoice intake (see backend/docs/email-intake.md).
    # ``email_intake_domain`` is the hostname used in the recipient address
    # (``invoices+<token>@<domain>``). Leave empty to disable intake.
    email_intake_domain: str = ""
    # HMAC-SHA256 signing secret for the webhook body. When empty, signature
    # verification is skipped (dev only — set this in every deployed env).
    email_intake_signing_secret: str = ""

    # ERP
    erp_mode: str = "local"  # "local" = in-process, "lambda" = dispatch to SQS
    sqs_erp_queue_url: str = ""  # required when erp_mode = "lambda"

    # Audit
    audit_mode: str = "local"  # "local" = in-process, "lambda" = dispatch to SQS
    sqs_audit_queue_url: str = ""  # required when audit_mode = "lambda"

    # Centralized audit-log shipping (SOC 2). A background task sweeps every
    # tenant DB, pulls unshipped `audit_log` rows in batches, and ships them
    # to the configured WORM-compliant sinks (CloudWatch Logs, S3 with Object
    # Lock). Default disabled — local dev doesn't need it and we don't want
    # to fire AWS calls from a developer laptop. Flip on in deployed envs.
    audit_shipping_enabled: bool = False
    audit_shipping_interval_seconds: int = 60
    audit_shipping_batch_size: int = 500
    # Comma-separated list of adapter names to fan out to. Every adapter in
    # the list must ACK before the rows get marked shipped — if any one
    # fails, none are marked shipped and the next tick retries.
    audit_shipping_providers: str = "mock"
    # CloudWatch Logs group name. Tenant log streams are created under this
    # group as `<tenant_db>/<YYYY-MM-DD>`.
    audit_shipping_cloudwatch_group: str = "/ap/audit"
    # S3 bucket must have Object Lock enabled (Governance or Compliance
    # mode) with a default retention period set on the bucket. The shipper
    # verifies the bucket exists on startup but does not configure Object
    # Lock itself — that's a bucket-provisioning concern (Terraform).
    audit_shipping_s3_bucket: str | None = None

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

    # MFA (TOTP + email backup)
    # Master switch — when false, all MFA enrollment, challenge, and enforcement
    # is bypassed. Default is `false` so local dev "just works" without TOTP
    # apps; flip to `true` in deployed environments.
    mfa_enabled: bool = False
    # Issuer label baked into TOTP provisioning URIs (what the user sees in
    # Google Authenticator / 1Password). Customer name keeps it brand-aligned.
    mfa_issuer: str = "Account Payables"
    # Email-OTP code lifetime. Six minutes balances "user has time to switch
    # from inbox back to the form" with "stolen email is short-lived."
    mfa_email_otp_ttl_seconds: int = 360
    # Short-lived JWT minted when password+email check out but MFA is still
    # required. The user trades it for a real access token by completing the
    # TOTP / email challenge. Five minutes is enough to find your phone.
    mfa_challenge_ttl_seconds: int = 300

    # SSO / SCIM
    # Base URL the OIDC provider redirects back to. Must exactly match what's
    # registered with Okta / Entra. {base} is substituted from AP_PUBLIC_URL.
    sso_redirect_path: str = "/login/sso-callback"
    # Called after successful SSO to hand the browser our own JWT in a short-
    # lived URL fragment; the frontend reads and stores it. Keep the path
    # static so IdP configs don't need per-tenant changes.
    sso_state_ttl_seconds: int = 600  # OIDC state / nonce expiry
    # Hash (not reversible) of the per-tenant SCIM bearer token is what gets
    # stored. The plaintext token is shown to the admin ONCE on generation.
    scim_url_path: str = "/api/scim/v2"

    # Security headers (SOC 2 — TLS + tablestakes hardening)
    # HSTS is gated off by default so local HTTP dev isn't broken. Deployed
    # environments should set AP_HSTS_ENABLED=true.
    hsts_enabled: bool = False
    # Two years is the value required by browsers that consider preload
    # submissions (hstspreload.org). Keep it as a setting so ops can dial it
    # down during a cert migration without a code push.
    hsts_max_age: int = 63072000
    hsts_include_subdomains: bool = True
    hsts_preload: bool = True

    # App
    debug: bool = True


settings = Settings()
