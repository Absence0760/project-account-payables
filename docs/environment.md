# Environment Variables

## Frontend (`frontend/.env`)

| Variable         | Default                 | Description                         |
|------------------|-------------------------|-------------------------------------|
| `PUBLIC_API_URL` | `http://localhost:8000` | Backend API URL (embedded at build time) |
| `BASE_PATH`      | (empty)                 | URL prefix for GitHub Pages deploys |

Copy the example file:

```bash
cd frontend
cp .env.example .env
```

Override at build time for different environments:

```bash
PUBLIC_API_URL=https://api-qa.example.com pnpm build    # QA
PUBLIC_API_URL=https://api.example.com pnpm build        # Production
```

Note: The frontend also reads the tenant slug from the browser subdomain at runtime (not from env vars). See [multi-tenancy.md](multi-tenancy.md).

## Backend (`backend/.env`)

| Variable              | Default                                                                  | Description                      |
|-----------------------|--------------------------------------------------------------------------|----------------------------------|
| `AP_DATABASE_URL`     | `postgresql+asyncpg://postgres:postgres@localhost:5432/account_payables` | Control-plane DB connection      |
| `AP_TENANT_DB_PREFIX` | `ap_`                                                                    | Prefix for tenant database names |
| `AP_SECRET_KEY`       | `change-me-in-production`                                                | JWT signing key                  |
| `AP_S3_ENDPOINT_URL`  | `http://localhost:9000`                                                  | MinIO/S3 endpoint                |
| `AP_S3_ACCESS_KEY`    | `minioadmin`                                                             | MinIO/S3 access key              |
| `AP_S3_SECRET_KEY`    | `minioadmin`                                                             | MinIO/S3 secret key              |
| `AP_S3_BUCKET`        | `invoices`                                                               | S3 bucket for invoice files      |
| `AP_DEBUG`            | `true`                                                                   | Enable debug logging             |
| `AP_SSO_REDIRECT_PATH` | `/login/sso-callback`                                                   | Path the IdP redirects back to after OIDC auth (per tenant subdomain) |
| `AP_SSO_STATE_TTL_SECONDS` | `600`                                                               | TTL on the OIDC state/nonce stored in Redis  |
| `AP_SCIM_URL_PATH`    | `/api/scim/v2`                                                           | Mount path for SCIM 2.0 endpoints |
| `AP_MFA_ENABLED`      | `false`                                                                  | Master MFA switch. `false` skips all MFA flows (recommended for local dev). Flip to `true` in deployed environments. |
| `AP_MFA_ISSUER`       | `Account Payables`                                                       | Label shown in TOTP authenticator apps |
| `AP_MFA_EMAIL_OTP_TTL_SECONDS` | `360`                                                           | Lifetime of email-OTP backup codes |
| `AP_MFA_CHALLENGE_TTL_SECONDS` | `300`                                                           | Lifetime of the post-password "still need MFA" challenge token |
| `AP_ACCESS_TOKEN_EXPIRE_MINUTES` | `30`                                                          | JWT lifetime in minutes |
| `AP_REDIS_URL`        | `redis://localhost:6379`                                                 | Redis connection — required at runtime (auth blocklist, MFA, SSO state, rate limit) |
| `AP_CORS_ORIGINS`     | `["http://localhost:7777","http://localhost:5173"]`                      | Comma-separated allowed origins (also matched by regex on subdomain) |
| `AP_EXTRACTION_MODE`  | `local`                                                                  | `local` (in-process thread) or `lambda` (dispatch to SQS) |
| `AP_EXTRACTION_TIMEOUT_SECONDS` | `600`                                                          | How long an invoice may sit in `pending` before the reaper transitions it to `failed`. |
| `AP_EXTRACTION_REAPER_INTERVAL_SECONDS` | `60`                                                   | How often the in-process reaper sweeps for stuck `pending` invoices. |
| `AP_EXTRACTION_REAPER_ENABLED` | `true`                                                          | Disable to skip the background reaper (useful for one-shot CLI runs / tests). |
| `AP_ERP_MODE`         | `local`                                                                  | `local` or `lambda` for ERP push dispatch |
| `AP_AUDIT_MODE`       | `local`                                                                  | `local` or `lambda` for audit log writes |
| `AP_SQS_EXTRACTION_QUEUE_URL` | (empty)                                                          | Required when `AP_EXTRACTION_MODE=lambda` |
| `AP_SQS_ERP_QUEUE_URL` | (empty)                                                                 | Required when `AP_ERP_MODE=lambda` |
| `AP_SQS_AUDIT_QUEUE_URL` | (empty)                                                               | Required when `AP_AUDIT_MODE=lambda` |
| `AP_AUDIT_SHIPPING_ENABLED` | `false`                                                            | Master switch for the centralized audit-log shipper (SOC 2). Disabled in local dev; flip on in deployed envs. |
| `AP_AUDIT_SHIPPING_INTERVAL_SECONDS` | `60`                                                      | How often the shipper sweeps every tenant DB for unshipped `audit_log` rows. |
| `AP_AUDIT_SHIPPING_BATCH_SIZE` | `500`                                                          | Max rows shipped per tenant per sweep. |
| `AP_AUDIT_SHIPPING_PROVIDERS` | `mock`                                                          | Comma-separated adapter names — typical prod value `cloudwatch,s3_objectlock`. |
| `AP_AUDIT_SHIPPING_CLOUDWATCH_GROUP` | `/ap/audit`                                              | CloudWatch Logs group for the shipped events. |
| `AP_AUDIT_SHIPPING_S3_BUCKET` | (empty)                                                         | Object-Lock-enabled S3 bucket. Required when the `s3_objectlock` provider is enabled. |
| `AP_ANTHROPIC_API_KEY` | (empty)                                                                 | Platform Claude Vision key (used when org chooses "Platform" extraction) |
| `AP_EXTRACTION_MODEL` | `claude-sonnet-4-20250514`                                               | Default extraction model for the platform program |
| `AP_LITHIC_API_KEY`   | (empty)                                                                  | Platform Lithic key for virtual cards |
| `AP_LITHIC_SANDBOX`   | `true`                                                                   | Use Lithic sandbox endpoints |
| `AP_NIUM_CLIENT_ID` / `_SECRET` / `_CUSTOMER_HASH_ID` / `_WALLET_HASH_ID` / `_SANDBOX` | (empty / `true`)                | Platform Nium config for virtual cards |
| `AP_EMAIL_PROVIDER`   | `console`                                                                | `console` (logs to stdout) or `ses` |
| `AP_EMAIL_FROM`       | `no-reply@localhost`                                                     | From-address on outbound transactional email |
| `AP_AWS_SES_REGION`   | `us-east-1`                                                              | AWS region when `AP_EMAIL_PROVIDER=ses` |
| `AP_PUBLIC_URL`       | `http://localhost:7777`                                                  | Where the frontend is served — used in outbound email links |
| `AP_TENANT_URL_TEMPLATE` | `http://{slug}.localhost:7777`                                        | Per-tenant URL shape; `{slug}` is substituted |
| `AP_HCAPTCHA_SECRET` / `AP_HCAPTCHA_SITEKEY` | (empty)                                                   | hCaptcha keys for self-service signup |
| `AP_SIGNUP_RATE_LIMIT_PER_HOUR` | `5`                                                            | Max signups per IP per hour (Redis sliding window) |
| `AP_RAG_ENABLED`      | `true`                                                                   | RAG retrieval of similar invoices as few-shot examples |
| `AP_RAG_TOP_K`        | `3`                                                                      | Number of semantic neighbors retrieved per extraction |
| `AP_EMBEDDING_PROVIDER` | `mock`                                                                 | `mock` (dev) or `openai` (text-embedding-3-small) |
| `AP_EMBEDDING_API_KEY` | (empty)                                                                 | OpenAI API key when `AP_EMBEDDING_PROVIDER=openai` |
| `AP_EMBEDDING_MODEL`  | `text-embedding-3-small`                                                 | Embedding model name |
| `AP_EMBEDDING_DIMENSIONS` | `1536`                                                               | Embedding vector size — must match the column type in pgvector |
| `AP_DUPLICATE_SIMILARITY_THRESHOLD` | `0.95`                                                     | Cosine threshold for flagging near-duplicate invoices |

Copy the example file (optional — defaults work with Docker Compose):

```bash
cd backend
cp .env.example .env
```

All backend variables are prefixed with `AP_` and loaded via `pydantic-settings` in `app/config.py`.

### Database URLs

The `AP_DATABASE_URL` points to the **control-plane database** (`account_payables`). Tenant database URLs are derived automatically by replacing the database name with `<AP_TENANT_DB_PREFIX><slug>` (e.g., `ap_acme`).

### Alembic

Set `AP_MIGRATE_TENANT` to target a specific tenant database for migrations:

```bash
AP_MIGRATE_TENANT=ap_acme alembic upgrade head
```

When unset, Alembic targets the control-plane database.
