# Environment Variables

## Frontend (`frontend/.env.development`)

| Variable         | Default                 | Description                         |
|------------------|-------------------------|-------------------------------------|
| `PUBLIC_API_URL` | `http://localhost:8000` | Backend API URL (embedded at build time) |
| `BASE_PATH`      | (empty)                 | URL prefix for GitHub Pages deploys |

`frontend/.env.development` is **committed** with the safe local default above
and Vite loads it automatically in dev mode — no setup step. Because `api.ts`
imports `PUBLIC_API_URL` from `$env/static/public`, the var must exist for the
dev server to start, and the committed file guarantees it does. For a personal
override, create a gitignored `frontend/.env.local` (it wins in Vite's
precedence over `.env.development`).

Override at build time for different environments:

```bash
PUBLIC_API_URL=https://api-qa.feohledger.com pnpm build    # QA
PUBLIC_API_URL=https://api.feohledger.com pnpm build        # Production
```

Note: The frontend also reads the tenant slug from the browser subdomain at runtime (not from env vars). See [multi-tenancy.md](multi-tenancy.md).

## Backend (`backend/.env`)

| Variable              | Default                                                                  | Description                      |
|-----------------------|--------------------------------------------------------------------------|----------------------------------|
| `FEOH_DATABASE_URL`     | `postgresql+asyncpg://postgres:postgres@localhost:5432/feohledger` | Control-plane DB connection      |
| `FEOH_TENANT_DB_PREFIX` | `feoh_`                                                                    | Prefix for tenant database names |
| `FEOH_SECRET_KEY`       | `change-me-in-production`                                                | JWT signing key                  |
| `FEOH_S3_ENDPOINT_URL`  | `http://localhost:9000`                                                  | MinIO/S3 endpoint. Set **empty** in deployed envs → real AWS S3 |
| `FEOH_S3_ACCESS_KEY`    | `minioadmin`                                                             | MinIO/S3 access key. Set **empty** (with the secret key) → boto3 default credential chain (instance/task role) |
| `FEOH_S3_SECRET_KEY`    | `minioadmin`                                                             | MinIO/S3 secret key. Set **empty** with the access key |
| `FEOH_S3_BUCKET`        | `invoices`                                                               | S3 bucket for invoice files      |
| `FEOH_DEBUG`            | `false`                                                                  | Enable debug logging + SQLAlchemy echo. Default `false` so a forgotten deploy doesn't ship Python tracebacks to clients; `backend/.env.development` sets it to `true` for local dev. The boot guard also relaxes the FEOH_SECRET_KEY / FEOH_EMAIL_INTAKE_SIGNING_SECRET defaults when this is `true`. |
| `FEOH_SSO_REDIRECT_PATH` | `/login/sso-callback`                                                   | Path the IdP redirects back to after OIDC auth (per tenant subdomain) |
| `FEOH_SSO_STATE_TTL_SECONDS` | `600`                                                               | TTL on the OIDC state/nonce stored in Redis  |
| `FEOH_SCIM_URL_PATH`    | `/api/scim/v2`                                                           | Mount path for SCIM 2.0 endpoints |
| `FEOH_MFA_ENABLED`      | `false`                                                                  | Master MFA switch. `false` skips all MFA flows (recommended for local dev). Flip to `true` in deployed environments. |
| `FEOH_MFA_ISSUER`       | `FeohLedger`                                                       | Label shown in TOTP authenticator apps |
| `FEOH_MFA_EMAIL_OTP_TTL_SECONDS` | `360`                                                           | Lifetime of email-OTP backup codes |
| `FEOH_MFA_CHALLENGE_TTL_SECONDS` | `300`                                                           | Lifetime of the post-password "still need MFA" challenge token |
| `FEOH_MFA_ENROLL_PENDING_TTL_SECONDS` | `900`                                                      | Lifetime of a pending (started-but-unverified) TOTP enrollment secret. The candidate lives in Redis, never on the account row, so starting an enrollment can't disturb the factor already in force |
| `FEOH_WEBAUTHN_RP_ID`   | `localhost`                                                              | Passkey Relying Party ID — the registrable domain passkeys are bound to (bare host, no scheme/port). Must be a suffix of every login origin; set to the app apex (e.g. `app.example.com`) in deployed envs |
| `FEOH_WEBAUTHN_ORIGINS` | `http://localhost:7777`                                                  | Comma-separated origins passkey ceremonies are verified against. Exact match, plus wildcard-subdomain entries (`https://*.app.example.com`) that admit every tenant subdomain — same scheme only, never the bare base, ports stay exact-match. Deployed envs MUST set this or passkeys silently fail on every prod origin |
| `FEOH_TRUSTED_PROXY_CIDRS` | (empty)                                                               | CIDRs whose `X-Forwarded-For` is trusted for the real client IP (rate limits + login/signup/SSO audit rows). Empty = never trust XFF (local dev). Behind a proxy (Caddy on the single-VM stack, the ALB on ECS) set it to the proxy's network or every user collapses into one per-IP bucket |
| `FEOH_ACCESS_TOKEN_EXPIRE_MINUTES` | `30`                                                          | JWT lifetime in minutes |
| `FEOH_MAX_CONCURRENT_SESSIONS` | `5`                                                                | Concurrent sessions per user. When a user logs in with this many already active, the oldest JTI is evicted to the blocklist. `0` disables the cap. |
| `FEOH_REDIS_URL`        | `redis://localhost:6379`                                                 | Redis connection — required at runtime (auth blocklist + active-session tracking, MFA, SSO state, rate limit) |
| `FEOH_CORS_ORIGINS`     | `["http://localhost:7777","http://localhost:5173"]`                      | Comma-separated allowed origins (also matched by regex on subdomain) |
| `FEOH_EXTRACTION_MODE`  | `local`                                                                  | `local` (in-process thread) or `lambda` (dispatch to SQS) |
| `FEOH_EXTRACTION_TIMEOUT_SECONDS` | `600`                                                          | How long an invoice may sit in `pending` before the reaper transitions it to `failed`. |
| `FEOH_EXTRACTION_REAPER_INTERVAL_SECONDS` | `60`                                                   | How often the in-process reaper sweeps for stuck `pending` invoices. |
| `FEOH_EXTRACTION_REAPER_ENABLED` | `true`                                                          | Disable to skip the background reaper (useful for one-shot CLI runs / tests). |
| `FEOH_EXTRACTION_AUTO_ROTATE`   | `true`                                                          | Run Tesseract OSD on rendered PDF pages before sending to vision adapters, auto-rotating 90/180/270-off-upright scans. No-op when `pytesseract` / `tesseract` are not installed. |
| `FEOH_SWEEP_FAILURE_ALERT_STREAK` | `3`                                                           | Consecutive failed runs (a tick that raised, or one that completed reporting `failures > 0`) before a background sweep is called **degraded**: the `GET /api/health/sweeps` aggregate flips, and the loop emits the alertable PII-free `NOT MAKING PROGRESS` ERROR on each streak multiple. `0` disables the escalation; the per-tick failure log stays either way. See `backend/docs/background-sweeps.md`. |
| `FEOH_APPROVAL_ESCALATION_ENABLED` | `false`                                                      | Master switch for the approval-escalation sweeper. Disabled in local dev; flip on in deployed envs. |
| `FEOH_APPROVAL_ESCALATION_INTERVAL_SECONDS` | `600`                                                 | How often the escalation sweeper scans every tenant's active workflow instances. |
| `FEOH_PAYMENT_RECONCILE_ENABLED` | `false`                                                        | Master switch for the payment-status reconciler (backstop polling for processors whose webhooks went missing). Pair with Modern Treasury / Stripe Treasury in prod. |
| `FEOH_PAYMENT_RECONCILE_INTERVAL_SECONDS` | `300`                                                 | How often the reconciler sweeps for `submitted`/`processing` payments. |
| `FEOH_PAYMENT_RECONCILE_AFTER_MINUTES` | `10`                                                     | Minimum age before a payment is re-checked against the processor. |
| `FEOH_PAYMENT_RECONCILE_MAX_AGE_HOURS` | `72`                                                     | Maximum age the reconciler tracks; anything older is left to ops. |
| `FEOH_EMAIL_INTAKE_DOMAIN` | (empty)                                                              | Hostname for inbound intake addresses (`invoices+<token>@<domain>`). Empty disables email intake. |
| `FEOH_EMAIL_INTAKE_SIGNING_SECRET` | (empty)                                                      | HMAC-SHA256 signing secret for the email-intake webhook body. Boot refuses if `FEOH_EMAIL_INTAKE_DOMAIN` is set and this is empty (unless `FEOH_DEBUG=true`). |
| `FEOH_ERP_MODE`         | `local`                                                                  | `local` or `lambda` for ERP push dispatch |
| `FEOH_ERP_MERGE_API_BASE` | `https://api.merge.dev/api/accounting/v1`                              | Merge.dev API base. Operator-controlled (process-level, TRUSTED — bypasses the admin-config SSRF guard). `backend/.env.development` points it at the local fake-erp (`http://localhost:12112/merge/api/accounting/v1`). |
| `FEOH_ERP_NETSUITE_API_BASE` | (empty)                                                              | NetSuite SuiteTalk REST base override. Empty → the per-account URL derived from `account_id`; set → used verbatim (operator-trusted). Dev value: `http://localhost:12112/netsuite/services/rest/record/v1`. |
| `FEOH_ERP_D365_API_BASE` | (empty)                                                                  | Dynamics 365 BC OData base override. Empty → the admin-config `base_url` + SSRF guard; set → used verbatim (operator-trusted). Dev value: `http://localhost:12112/d365`. |
| `FEOH_ERP_D365_TOKEN_URL` | (empty)                                                                 | D365 OAuth token endpoint override. Empty → `https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token`. Dev value: `http://localhost:12112/d365/oauth2/token`. |
| `FEOH_AUDIT_MODE`       | `local`                                                                  | `local` or `lambda` for audit log writes |
| `FEOH_SQS_EXTRACTION_QUEUE_URL` | (empty)                                                          | Required when `FEOH_EXTRACTION_MODE=lambda` |
| `FEOH_SQS_ERP_QUEUE_URL` | (empty)                                                                 | Required when `FEOH_ERP_MODE=lambda` |
| `FEOH_SQS_AUDIT_QUEUE_URL` | (empty)                                                               | Required when `FEOH_AUDIT_MODE=lambda` |
| `FEOH_AUDIT_SHIPPING_ENABLED` | `false`                                                            | Master switch for the centralized audit-log shipper (SOC 2). Disabled in local dev; flip on in deployed envs. |
| `FEOH_AUDIT_SHIPPING_INTERVAL_SECONDS` | `60`                                                      | How often the shipper sweeps every tenant DB for unshipped `audit_log` rows. |
| `FEOH_AUDIT_SHIPPING_BATCH_SIZE` | `500`                                                          | Max rows shipped per tenant per sweep. |
| `FEOH_AUDIT_SHIPPING_PROVIDERS` | `mock`                                                          | Comma-separated adapter names — typical prod value `cloudwatch,s3_objectlock`. |
| `FEOH_AUDIT_SHIPPING_CLOUDWATCH_GROUP` | `/ap/audit`                                              | CloudWatch Logs group for the shipped events. |
| `FEOH_AUDIT_SHIPPING_S3_BUCKET` | (empty)                                                         | Object-Lock-enabled S3 bucket. Required when the `s3_objectlock` provider is enabled. |
| `FEOH_ANTHROPIC_API_KEY` | (empty)                                                                 | Platform Claude Vision key (used when org chooses "Platform" extraction) |
| `FEOH_EXTRACTION_MODEL` | `claude-sonnet-4-20250514`                                               | Default extraction model for the platform program |
| `FEOH_EXTRACTION_PROVIDER` | (empty) / `mock` in `.env.development`                                | Operator override for the adapter **platform**-mode extraction runs on. Empty = derive: a set `FEOH_ANTHROPIC_API_KEY` → `claude_vision`; keyless + non-deployed → the offline `mock` reader (so `pnpm dev` never calls out with an empty key); keyless + **deployed** → still `claude_vision`, which fails loudly, because `mock.extract` returns a fixture and fabricating invoice fields is worse than a provider error. A BYOK org's own `settings.extraction` is unaffected. An unregistered name is refused at boot. Precedence table + rationale: `backend/docs/ai-extraction.md` § Platform provider precedence. |
| `FEOH_LITHIC_API_KEY`   | (empty)                                                                  | Platform Lithic key for virtual cards |
| `FEOH_LITHIC_SANDBOX`   | `true`                                                                   | Use Lithic sandbox endpoints |
| `FEOH_NIUM_CLIENT_ID` / `_SECRET` / `_CUSTOMER_HASH_ID` / `_WALLET_HASH_ID` / `_SANDBOX` | (empty / `true`)                | Platform Nium config for virtual cards |
| `FEOH_EMAIL_PROVIDER`   | `console`                                                                | `console` (logs to stdout) or `ses` |
| `FEOH_EMAIL_FROM`       | `no-reply@localhost`                                                     | From-address on outbound transactional email |
| `FEOH_AWS_SES_REGION`   | `us-east-1`                                                              | AWS region when `FEOH_EMAIL_PROVIDER=ses` |
| `FEOH_PUBLIC_URL`       | `http://localhost:7777`                                                  | Where the frontend is served — used in outbound email links |
| `FEOH_TENANT_URL_TEMPLATE` | `http://{slug}.localhost:7777`                                        | Per-tenant URL shape; `{slug}` is substituted |
| `FEOH_HCAPTCHA_SECRET` / `FEOH_HCAPTCHA_SITEKEY` | (empty)                                                   | hCaptcha keys for self-service signup |
| `FEOH_SIGNUP_RATE_LIMIT_PER_HOUR` | `5`                                                            | Max `/signup/start` + `/complete` per IP per hour (Redis sliding window) |
| `FEOH_SIGNUP_EMAIL_RATE_LIMIT_PER_HOUR` | `3`                                                      | Max verification emails per target address per hour (anti email-bombing) |
| `FEOH_SLUG_CHECK_RATE_LIMIT_PER_HOUR` | `120`                                                      | Max `/signup/slug-check` per IP per hour (anti-enumeration) |
| `FEOH_ENVIRONMENT`      | `development`                                                            | Deployment discriminator; non-dev values turn on prod guards (captcha required to boot) |
| `FEOH_RAG_ENABLED`      | `true`                                                                   | RAG retrieval of similar invoices as few-shot examples |
| `FEOH_RAG_TOP_K`        | `3`                                                                      | Number of semantic neighbors retrieved per extraction |
| `FEOH_EMBEDDING_PROVIDER` | `mock`                                                                 | `mock` (dev) or `openai` (text-embedding-3-small) |
| `FEOH_EMBEDDING_API_KEY` | (empty)                                                                 | OpenAI API key when `FEOH_EMBEDDING_PROVIDER=openai` |
| `FEOH_EMBEDDING_MODEL`  | `text-embedding-3-small`                                                 | Embedding model name |
| `FEOH_EMBEDDING_DIMENSIONS` | `1536`                                                               | Embedding vector size — must match the column type in pgvector |
| `FEOH_DUPLICATE_SIMILARITY_THRESHOLD` | `0.95`                                                     | Cosine threshold for flagging near-duplicate invoices |
| `FEOH_HSTS_ENABLED`     | `false`                                                                  | Master switch for the `Strict-Transport-Security` response header. Keep `false` in local HTTP dev; set `true` in deployed environments behind HTTPS. |
| `FEOH_HSTS_MAX_AGE`     | `63072000`                                                               | HSTS `max-age` in seconds. Default is two years — the minimum for `hstspreload.org` submission. |
| `FEOH_HSTS_INCLUDE_SUBDOMAINS` | `true`                                                            | Emit the `includeSubDomains` directive (recommended; subdomains inherit the pin). |
| `FEOH_HSTS_PRELOAD`     | `true`                                                                   | Emit the `preload` directive. Only meaningful if you actually submit to the preload list. |
| `FEOH_PEPPOL_PROVIDER`  | `mock`                                                                   | PEPPOL Access Point adapter — `mock` (in-process, local-first default) or `as4_gateway` (real hosted AP). Per-org override via `Organization.settings.peppol.provider`. |
| `FEOH_PEPPOL_GATEWAY_URL` | (empty)                                                                | Base URL of the hosted Access Point. Required when `FEOH_PEPPOL_PROVIDER=as4_gateway`. |
| `FEOH_PEPPOL_GATEWAY_API_KEY` | (empty)                                                            | Gateway API key — no hardcoded fallback; empty disables the gateway (returns `peppol_not_configured`). Store via sops in deployed envs. |
| `FEOH_PEPPOL_INBOUND_ENABLED` | `false`                                                            | Master switch for the inbound PEPPOL AS4 receive webhook (`POST /api/peppol/inbound/{tenant_slug}`). When `false` the route is a silent no-op 204. `backend/.env.development` sets it `true` so the webhook is locally testable. |
| `FEOH_PEPPOL_INBOUND_SIGNING_SECRET` | (empty)                                                     | HMAC-SHA256 key the Access Point signs the inbound POST body with. Boot refuses if `FEOH_PEPPOL_INBOUND_ENABLED` is true and this is empty (unless `FEOH_DEBUG=true`). No hardcoded fallback; real secret via sops. `backend/.env.development` carries a NON-secret dev value (`dev-peppol-inbound-secret`). |
| `FEOH_PEPPOL_INBOUND_MAX_BYTES` | `4194304`                                                          | Hard cap (bytes) on the inbound PEPPOL webhook body — oversized POSTs are rejected with 204 before buffering/parsing (memory-exhaustion guard). PEPPOL UBL documents are tens of KB; the 4 MiB default leaves headroom. |

`backend/.env.development` is **committed** with safe local defaults and is
loaded by `main.py` (the local-dev entrypoint) via `python-dotenv` — no setup
step. It's belt-and-suspenders anyway: the defaults in `app/config.py` already
work with Docker Compose even with no env file present. For personal overrides,
add a gitignored `backend/.env`; it wins over `.env.development`.

All backend variables are prefixed with `FEOH_` and loaded via `pydantic-settings` in `app/config.py`.

### Database URLs

The `FEOH_DATABASE_URL` points to the **control-plane database** (`feohledger`). Tenant database URLs are derived automatically by replacing the database name with `<FEOH_TENANT_DB_PREFIX><slug>` (e.g., `feoh_acme`).

### Alembic

Set `FEOH_MIGRATE_TENANT` to target a specific tenant database for migrations:

```bash
FEOH_MIGRATE_TENANT=feoh_acme alembic upgrade head
```

When unset, Alembic targets the control-plane database.
