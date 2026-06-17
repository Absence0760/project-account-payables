from pydantic import model_validator
from pydantic_settings import BaseSettings

# Environment names that are NOT a deployed/production environment. In these,
# safety guards (e.g. the captcha requirement) are relaxed for local dev + CI.
_NON_DEPLOYED_ENVS = frozenset({"development", "dev", "local", "test", "ci"})


class Settings(BaseSettings):
    model_config = {"env_prefix": "AP_"}

    # Deployment environment discriminator (AP_ENVIRONMENT). Defaults to
    # "development" so local dev + CI are unaffected; deployed envs set it to
    # "production"/"staging" to opt into the production safety guards below.
    environment: str = "development"

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

    # AWS service emulator (LocalStack). Empty = talk to real AWS (prod default).
    # When set (e.g. http://localhost:4566), the non-S3-storage AWS clients —
    # SQS dispatch (lambda mode), SES email, CloudWatch Logs + S3 Object Lock
    # audit shipping, Textract — target it instead. See docs/local-aws-localstack.md.
    aws_endpoint_url: str = ""

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
    # Approval escalation sweeper: scans every tenant's active workflow
    # instances and appends `escalation_to_user_ids` onto any chain level
    # that has been waiting longer than its `escalation_hours`. Disabled
    # by default in local dev (no clock to escalate against in a one-shot
    # demo); flip on in deployed envs.
    approval_escalation_enabled: bool = False

    # Contract renewal-alert sweep. A long-lived loop that finds contracts
    # nearing their end_date and notifies the owner + AP managers once
    # (deduped via Contract.renewal_alert_sent_at). Disabled by default so
    # local dev / tests don't run a background sweep; flip on in deployed
    # envs. The per-contract `renewal_notice_days` overrides the default
    # lead window below.
    contract_renewal_enabled: bool = False
    contract_renewal_interval_seconds: int = 3600
    contract_renewal_default_notice_days: int = 30

    # QMS (Quality Management System) inspection sync. A background loop that
    # pulls quality-inspection records from an external QMS / LIMS into the
    # local `quality_inspections` table (the 4-way-match leg). Disabled by
    # default so local dev / tests don't run a background sweep; flip on in
    # deployed envs once a real QMS is configured per-org in
    # `Organization.settings.qms`. `qms_provider` is the platform-default
    # adapter (`mock` = deterministic, no network/credential — the local-first
    # default); a per-org `settings.qms.provider` overrides it. No secret here:
    # the `generic` adapter reads its base_url + api_key from per-org settings,
    # never from env.
    qms_sync_enabled: bool = False
    qms_sync_interval_seconds: int = 3600
    qms_provider: str = "mock"

    # Sanctions & vendor-risk screening. `vendor_screening_enabled` is the
    # master switch for synchronous screening on vendor create / update
    # (default on — the `mock` adapter is safe and local-first, returning
    # `clear` for everything that isn't an obvious fixture). The re-screen
    # sweep is a background loop (like contract renewal) and is OFF by
    # default so local dev / tests don't run it; deployed envs flip it on.
    # `vendor_rescreen_after_days` is the staleness window — a vendor whose
    # last screen is older than this (or never screened) is re-screened on
    # the next sweep. See `docs/vendor-risk-screening.md`.
    vendor_screening_enabled: bool = True
    vendor_rescreen_enabled: bool = False
    vendor_rescreen_interval_seconds: int = 86400
    vendor_rescreen_after_days: int = 7

    # Dynamic discounting & early-payment optimization (see
    # backend/docs/dynamic-discounting.md). The ROI calculator and the
    # accept/decline offer surface run unconditionally; only the *auto-capture
    # sweep* is gated. `discount_optimization_enabled` is the master switch for
    # that background loop — OFF by default so local dev / tests don't
    # auto-pay. `discount_auto_capture_roi_threshold` is the annualized return
    # (APR %) an offer must clear for the sweep to capture it automatically;
    # `discount_cost_of_capital_pct` is the platform-default annual cost of
    # capital the ROI calculator compares against (per-org override:
    # `Organization.settings.discounting.cost_of_capital_pct`). Percentages,
    # not currency, so float is fine.
    discount_optimization_enabled: bool = False
    discount_optimization_interval_seconds: int = 3600
    discount_auto_capture_roi_threshold: float = 12.0
    discount_cost_of_capital_pct: float = 8.0
    approval_escalation_interval_seconds: int = 600
    # Payment-status reconciler: backstop polling for processors whose
    # webhooks have gone missing. Disabled by default in local dev (the
    # mock adapter settles synchronously, so there's nothing to
    # reconcile); flip on in deployed envs alongside Modern Treasury.
    payment_reconcile_enabled: bool = False
    payment_reconcile_interval_seconds: int = 300
    payment_reconcile_after_minutes: int = 10
    payment_reconcile_max_age_hours: int = 72
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

    # Audit-log summarization (invoice detail modal). Reuses the extraction
    # API key + model, so no new secret. Master switch — when False the
    # `/api/invoices/{id}/summary` endpoint returns the deterministic template
    # summary without any LLM call. `audit_summary_model` defaults to the
    # extraction model when empty.
    audit_summary_enabled: bool = True
    audit_summary_model: str = ""  # falls back to extraction_model when empty

    # Conversational AP Assistant (see backend/docs/conversational-assistant.md).
    # Local-first: the default `mock` adapter routes a natural-language query to
    # one of the five fixed tools via deterministic keyword/intent heuristics —
    # no network, no key. The `claude` adapter (Anthropic Messages API tool-use)
    # is selected only when an API key is configured; the dispatcher
    # auto-downgrades `claude` → `mock` when `AP_ANTHROPIC_API_KEY` is empty, so
    # `pnpm dev` never requires a real credential. Reuses the extraction key — no
    # new secret.
    assistant_provider: str = "mock"  # "mock" (local-first default) | "claude"
    # Empty → falls back to `extraction_model` (claude-opus-4-8 family) at
    # request time; never hardcoded in the adapter.
    assistant_model: str = ""
    # Per-org / per-month token budget. 0 disables the cap (matching the
    # AP_MAX_CONCURRENT_SESSIONS=0 convention). Per-org override lives in
    # Organization.settings.assistant.monthly_token_budget.
    assistant_monthly_token_budget: int = 200_000
    # Caps the claude adapter's tool-use loop so a single turn can't run away
    # on cost. Each hop is one API round-trip.
    assistant_max_tool_hops: int = 4

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
    # `cors_origins` is a list of exact origins (used for the static-frontend
    # dev server). `cors_production_domain` is a single registrable-domain
    # suffix that — when set — adds `https://*.<domain>` to the allowlist
    # via the subdomain regex. Empty in local dev so we never silently
    # match a third-party host; deploys must set it to the real domain
    # (e.g. ``app.example.com``). Comma-separated list of multiple domains
    # is supported.
    cors_origins: list[str] = ["http://localhost:7777", "http://localhost:5173"]
    cors_production_domain: str = ""

    # `X-Forwarded-For` is only honoured when the request's connecting IP
    # belongs to one of these CIDRs (the ALB / CloudFront edges in deployed
    # envs). Anything outside the allowlist is treated as a direct client
    # and uses ``request.client.host`` for rate-limit / audit purposes, so
    # a hostile direct caller can't rotate IPs by spoofing the header.
    # Default empty → never trust XFF (matches local-dev with no proxy).
    # Comma-separated list of CIDRs.
    trusted_proxy_cidrs: str = ""

    # Self-service signup
    email_provider: str = "console"  # "console" (dev default) | "ses" | "smtp"
    email_from: str = "no-reply@localhost"
    aws_ses_region: str = "us-east-1"

    # SMTP email (used when email_provider="smtp"). Defaults target a local
    # Mailpit sink (`pnpm mail:up`) — no auth, no TLS — so outbound mail lands in
    # a web inbox at http://localhost:8025. See docs/local-email-mailpit.md.
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: str = ""  # secret — empty for Mailpit; set via sops in deployed envs
    smtp_use_tls: bool = False
    public_url: str = "http://localhost:7777"  # where the frontend is served
    # Externally-reachable base URL of THIS backend. Unlike OIDC (front-channel
    # code flow that redirects to the SPA), SAML POST-binding makes the IdP post
    # the assertion straight to the backend ACS, so the SP entityID + ACS URL we
    # register with each IdP are built off this. Dev: the backend dev server.
    api_public_url: str = "http://localhost:8000"
    tenant_url_template: str = "http://{slug}.localhost:7777"

    # Master switch for the email + in-app notification system. When false,
    # the `transition_invoice` hook and `assign_reviewer` skip notification
    # dispatch entirely — no in-app rows, no emails. Defaults on; flip off to
    # silence notifications without a code change. Notification *send* is always
    # best-effort regardless of this flag (failures never break a transition).
    notifications_enabled: bool = True
    hcaptcha_secret: str = ""  # empty = skip captcha verification
    hcaptcha_sitekey: str = ""  # exposed to frontend via a public endpoint
    signup_rate_limit_per_hour: int = 5
    # Per-email cap on verification-email sends, so an attacker rotating IPs
    # can't email-bomb one victim address (the per-IP limit can't catch that).
    signup_email_rate_limit_per_hour: int = 3
    # Inline slug-availability check fires on (debounced) keystrokes, so it
    # needs a generous per-IP cap — high enough for real typing, low enough to
    # stop wire-speed namespace enumeration / DB amplification.
    slug_check_rate_limit_per_hour: int = 120

    # Master switch for the Redis-backed rate limiter at
    # ``app/services/rate_limit.py``. Defaults to True so deployed envs
    # are protected; CI's e2e suite flips this off because all 4 shards
    # × 4 workers hit `/api/auth/login` from the same loopback IP and
    # would otherwise saturate the 10/min cap during the
    # signInAndWait calls in opt-out specs.
    rate_limit_enabled: bool = True

    # RAG / embeddings
    rag_enabled: bool = True
    rag_top_k: int = 3
    embedding_provider: str = "mock"  # "mock" (dev default) | "openai"
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    # Local AI (Ollama). Fallback base URL for the `ollama` extraction adapter
    # when an org's extraction config doesn't set its own `base_url`. Defaults to
    # a native Ollama on 11434; point at 11435 to use the Compose container
    # (`pnpm ollama:up`). See backend/docs/local-ai-testing.md.
    ollama_base_url: str = "http://localhost:11434"

    # Stripe API base for the stripe_treasury payment adapter. Empty = live Stripe
    # (api.stripe.com). Set to the local stripe-mock container
    # (http://localhost:12111/v1) for offline payment testing (`pnpm stripe:up`).
    stripe_api_base: str = ""
    # Cosine similarity above which a new invoice is flagged as a likely
    # duplicate of an already-stored one. Tighter than rag_top_k retrieval:
    # RAG wants semantically related invoices; dup detection wants near-
    # identical ones.
    duplicate_similarity_threshold: float = 0.95

    # International tax (VAT / GST / withholding) — see docs/international-tax.md.
    # The consumption-tax *rate* is resolved by a pluggable adapter. Default
    # `mock` reads deterministic rates from the country-rules engine, so a
    # fresh clone needs no cloud account (local-first). Cloud providers
    # (`avalara`, `taxjar`) are skeletons until a real key is wired up. A
    # tenant can override per-org via `Organization.settings.tax.rate_provider`;
    # this is the platform-wide fallback.
    tax_rate_provider: str = "mock"  # "mock" (dev default) | "avalara" | "taxjar"
    tax_rate_api_key: str = ""  # secret — empty for mock; set via sops for cloud providers

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

    # SAML 2.0 SSO (Service-Provider side). Additive, separate code path from
    # OIDC; like OIDC it is gated PER-TENANT via Organization.settings.sso
    # (protocol="saml") — there is no global on/off, so a fresh clone + pnpm dev
    # simply has no SAML tenant configured and the routes 400/404.
    # Frontend bridge route the SAML ACS 303-redirects to after minting a
    # one-time handoff code. The bridge POSTs the code to /api/auth/saml/exchange
    # and receives the JWT in the response body — the JWT never transits a URL.
    saml_acs_path: str = "/login/saml-callback"
    # TTL for the one-time post-ACS handoff code (Redis). Short — it's consumed
    # by the bridge page within a single redirect hop.
    saml_handoff_ttl_seconds: int = 120
    # Optional SP signing keypair, used ONLY when a tenant requires SP-signed
    # AuthnRequests. Real secret -> backend/.env.sops (KMS) in deployed envs;
    # empty by default (local Keycloak runs with client-signature off, so no SP
    # key is needed to run locally). NEVER given a hardcoded non-empty fallback.
    saml_sp_private_key: str = ""
    saml_sp_cert: str = ""

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

    # Multi-currency reporting. The reporting (base) currency an org rolls
    # multi-currency invoices/payments up into for analytics + dashboards. A
    # per-org override lives on `Organization.settings.reporting_currency` (and
    # falls back to the legacy `payments.home_currency`, then
    # `invoice_defaults.currency`); this is the platform-wide last-resort default
    # when none of those are set. ISO 4217. See backend/docs/multi-currency.md.
    reporting_currency_default: str = "USD"

    # US 1099 tax compliance (TIN validation + e-filing). Both default to the
    # offline `mock` adapter so a fresh clone runs with no cloud account
    # (local-first). Per-org overrides live on
    # ``Organization.settings.tax.{tin_validation,filing}`` and win over these
    # process-level defaults; deployed envs that validate/file for real set the
    # provider to `tax1099` there (with the live key in the encrypted settings).
    tin_validation_provider: str = "mock"  # "mock" (offline) | "tax1099"
    tax_filing_provider: str = "mock"  # "mock" (offline) | "tax1099"

    # PEPPOL AS4 outbound (e-invoice transmission via a hosted Access Point).
    # Local-first: the in-process `mock` adapter is the default so `pnpm dev`
    # never needs a real PEPPOL credential. Per-org overrides live on
    # ``Organization.settings.peppol.{provider,gateway_url,api_key,...}`` and
    # win over these process-level defaults; deployed envs that transmit for
    # real set the provider to `as4_gateway` there with the live gateway URL +
    # key in the encrypted settings (sops). The gateway API key has NO
    # hardcoded fallback — empty here, real value via sops in deployed.
    peppol_provider: str = "mock"  # "mock" (in-process default) | "as4_gateway"
    peppol_gateway_url: str = ""  # hosted Access Point base URL (deployed only)
    peppol_gateway_api_key: str = ""  # secret — empty for mock; sops in deployed

    # PEPPOL AS4 INBOUND receive (the receiver-corner C4 webhook the Access
    # Point posts inbound documents to). Master switch — mirrors
    # ``email_intake_domain`` / ``audit_shipping_enabled`` gating: the public
    # ``POST /api/peppol/inbound/{tenant_slug}`` route is a no-op 204 until this
    # is on, so the surface is closed by default. The signing secret is the
    # HMAC-SHA256 key the Access Point signs the inbound POST body with; it has
    # NO hardcoded secret fallback (the boot guard refuses to start a deployed
    # env that enables inbound without it). The committed .env.development sets a
    # NON-secret dev value so the webhook is locally testable under `pnpm dev`.
    peppol_inbound_enabled: bool = False
    peppol_inbound_signing_secret: str = ""
    # Hard cap on the inbound webhook body the route will buffer before parsing.
    # A signed-but-oversized POST is fully read into memory then handed to lxml;
    # PEPPOL UBL documents are tens of KB, so a few-MB ceiling rejects a
    # memory-exhaustion attempt (204, no parse) without truncating real invoices.
    peppol_inbound_max_bytes: int = 4 * 1024 * 1024

    # Punch-out catalogs (live cXML/OCI round-trips). Local-first: the in-process
    # `mock` adapter is the default so `pnpm dev` runs the whole punch-out flow
    # (setup → start URL → returned cart → convert-to-requisition) without an
    # external supplier or credential. Per-org overrides live on
    # ``Organization.settings.punchout.{provider,shared_secret,buyer_identity,...}``
    # and win over these process-level defaults. The cXML supplier shared secret
    # has NO hardcoded fallback — empty here, real value via sops in deployed.
    punchout_provider: str = "mock"  # "mock" (in-process default) | "cxml"
    punchout_shared_secret: str = ""  # cXML supplier credential — sops in deployed
    # HMAC-SHA256 key the supplier signs the PunchOutOrderMessage cart-return POST
    # with. The public return endpoint verifies it (the cart return is
    # public-by-design — no JWT). NO hardcoded secret fallback; the committed
    # .env.development sets a NON-secret dev value so the return is locally
    # testable. In local debug with an empty secret the BuyerCookie match is the
    # sole gate (mirrors peppol_inbound's debug carve-out).
    punchout_return_signing_secret: str = ""
    # Hard cap on the cart-return webhook body the route buffers before parsing
    # (memory-exhaustion guard on the public route; cXML carts are tens of KB).
    punchout_return_max_bytes: int = 4 * 1024 * 1024

    # Digital signatures on approvals (SOX non-repudiation). The HMAC-SHA256
    # signing key over the canonical approval payload (invoice id + exact amount
    # + actor + decision + timestamp), persisted on the approval audit row's
    # `details.signature` block and re-verifiable via
    # GET /api/audit/invoice/{id}/verify-signatures. Empty by default → signing
    # is skipped (no hardcoded production fallback, mirroring the other HMAC
    # secrets); the committed .env.development sets a NON-secret dev value so the
    # feature is exercisable under `pnpm dev`; deployed envs set the real key via
    # sops. See backend/docs/approval-signatures.md.
    approval_signing_key: str = ""

    # Retention policies (SOX records management). The enforcement sweep is a
    # long-lived loop (like contract renewal / qms sync) that finds records past
    # their configured retention window and archives them via a privileged,
    # audited path. Per-record-class retention periods live on
    # `Organization.settings.retention` (configurable, not hardcoded), read +
    # updated via GET/PUT /api/retention-policy. Disabled by default so local
    # dev / tests don't run a background sweep; flip AP_RETENTION_ENABLED on in
    # deployed envs. CRITICAL: the sweep composes with the audit-immutability
    # trigger — `audit_log` rows are NEVER deleted; "retention" for the audit
    # class means verifying WORM-shipment and recording a manifest. See
    # backend/docs/retention.md.
    retention_enabled: bool = False
    retention_interval_seconds: int = 86400
    # Platform-default retention windows (months) when an org sets no per-class
    # override in `Organization.settings.retention`. 84 months = 7 years, the
    # common SOX / IRS records-retention baseline.
    retention_default_months: int = 84

    # App
    # Default is `False` so a deploy that forgets to set `AP_DEBUG` does not
    # ship FastAPI tracebacks (internal paths, env names) to clients. Local
    # dev sets `AP_DEBUG=true` in `.env`.
    debug: bool = False

    @property
    def is_deployed(self) -> bool:
        """True for any deployed (non local-dev / non-CI) environment."""
        return self.environment.strip().lower() not in _NON_DEPLOYED_ENVS

    @model_validator(mode="after")
    def _require_captcha_in_deployed_envs(self) -> "Settings":
        # Fail fast at boot rather than silently shipping signup with captcha
        # disabled — a 'fail open' captcha is an abuse hole on a public,
        # tenant-creating endpoint.
        if self.is_deployed and not self.hcaptcha_secret:
            raise ValueError(
                "AP_HCAPTCHA_SECRET must be set when AP_ENVIRONMENT is a deployed "
                f"environment ({self.environment!r}); refusing to boot with captcha "
                "verification disabled on the public signup endpoint."
            )
        return self


settings = Settings()
