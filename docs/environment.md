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
