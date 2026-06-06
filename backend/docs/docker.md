# Docker

Docker Compose manages the local infrastructure: PostgreSQL, Redis, and MinIO
are always-on core services; Keycloak is an opt-in local identity provider for
exercising SSO.

Prefer the root `pnpm` scripts over raw `docker compose` (one script per
service — no need to memorize profile flags): `pnpm db:{up,down,logs,reset}`
for the core trio, `pnpm idp:{up,down,logs,seed}` for Keycloak, and
`pnpm services:{up,down,reset}` to bring up / tear down *everything* at once.

## Starting Services

```bash
cd backend
docker compose up -d        # core trio only — pnpm db:up
```

This starts PostgreSQL, Redis, and MinIO in the background (Keycloak is held
behind the `idp` profile and does NOT start here). On first run, PostgreSQL
automatically creates three databases: `account_payables` (control plane),
`ap_acme`, and `ap_techflow` (dev tenants) via the mounted `init-tenants.sql`.

To start individual services:

```bash
docker compose up -d postgres
docker compose up -d redis
docker compose up -d minio
```

## Local identity provider (Keycloak)

Keycloak is the dev-laptop equivalent of Okta / Entra, so the OIDC SSO flow can
be driven end-to-end with no cloud account. It lives under the `idp` compose
profile, so it only starts when you ask for it:

```bash
docker compose --profile idp up -d keycloak   # pnpm idp:up
docker compose stop keycloak                  # pnpm idp:down
```

It runs in dev mode with an ephemeral in-memory DB and re-imports
`keycloak/realm-export.json` on every boot — always a clean, reproducible IdP.
After it's up, `pnpm idp:seed` points the acme tenant's `settings.sso` at it.
Full walkthrough: [`../../docs/local-sso-keycloak.md`](../../docs/local-sso-keycloak.md).

## Local SCIM provider (Authentik)

Where Keycloak covers inbound OIDC SSO, **Authentik** covers outbound SCIM
provisioning: it's the SCIM *client* that pushes users into the app's SCIM
Service Provider (`app/api/scim.py`, `/api/scim/v2/Users`). It's the local-first
equivalent of Okta / Entra SCIM. `pnpm idp:up` starts it alongside Keycloak; the
stack is self-contained (its own Postgres + Redis under the `idp` profile, not
the app's) and applies `authentik/blueprints/account-payables-scim.yaml` on boot
to configure the SCIM provider automatically.

```bash
pnpm idp:up        # Keycloak + Authentik
pnpm scim:seed     # set the matching SCIM bearer token on the acme tenant
# Authentik admin http://localhost:9002 (akadmin / admin) → Providers → Run sync
```

Authentik reaches the app backend (run on the host via `pnpm dev`, `:8000`)
through Docker's `host.docker.internal` gateway. Full walkthrough:
[`../../docs/local-sso-keycloak.md` § Authentik](../../docs/local-sso-keycloak.md#authentik--local-scim-provisioning).

## Local AWS emulator (LocalStack)

LocalStack gives the AWS-backed paths a local target with no cloud account: SQS
(the `lambda` dispatch modes), SES (the `ses` email adapter), and CloudWatch Logs
+ S3 Object Lock (the audit-log shipper sinks). Opt-in under the `aws` profile:

```bash
docker compose --profile aws up -d localstack   # pnpm aws:up
```

An init script (`localstack/init/ready.d/`) creates the queues, SES identity,
log group, and object-lock bucket on boot. Point the app at it with
`AP_AWS_ENDPOINT_URL=http://localhost:4566` (empty = real AWS). MinIO stays the
S3 *file* store; LocalStack only fronts the other AWS services. Full walkthrough:
[`../../docs/local-aws-localstack.md`](../../docs/local-aws-localstack.md).

## Services

| Service    | Image                       | Port(s)         | Profile | Description                                       |
|------------|-----------------------------|-----------------|---------|---------------------------------------------------|
| PostgreSQL | `pgvector/pgvector:pg16`    | `5432`          | (core)  | Primary database (multi-DB) + pgvector extension  |
| Redis      | `redis:7-alpine`            | `6379`          | (core)  | JWT blocklist + rate-limit counters               |
| MinIO      | `minio/minio:latest`        | `9000`, `9001`  | (core)  | S3-compatible storage                             |
| Keycloak   | `quay.io/keycloak/keycloak` | `8088`          | `idp`   | Local OIDC IdP for SSO testing (opt-in)           |
| Authentik server | `ghcr.io/goauthentik/server` | `9002` | `idp` | Local SCIM IdP — pushes users into `/api/scim/v2` (opt-in) |
| Authentik worker | `ghcr.io/goauthentik/server` | —      | `idp` | Runs the SCIM sync jobs (opt-in)                  |
| Authentik Postgres | `postgres:16-alpine`     | —      | `idp` | Authentik's own DB (not the app's)               |
| Authentik Redis | `redis:7-alpine`           | —      | `idp` | Authentik's own cache/broker (not the app's)     |
| LocalStack | `localstack/localstack:3`     | `4566` | `aws` | Local AWS emulator — SQS, SES, CloudWatch Logs, S3 Object Lock (opt-in) |

The PostgreSQL image is `pgvector/pgvector:pg16` (official Postgres 16 + the [pgvector](https://github.com/pgvector/pgvector) extension) because the RAG-based extraction priors use a `vector(1536)` column. The image is binary-compatible with the vanilla `postgres:16` data directory, so switching from plain Postgres doesn't require a volume wipe — just `docker compose down && up -d`. If you do swap images on an existing volume, run `REINDEX DATABASE <name>` on each DB once to rebuild any text-column indexes affected by a collation-version change.

## Default Credentials

| Service    | Username      | Password      |
|------------|---------------|---------------|
| PostgreSQL | `postgres`    | `postgres`    |
| MinIO      | `minioadmin`  | `minioadmin`  |
| Redis      | (none)        | (none)        |
| Keycloak   | `admin`       | `admin`       |
| Authentik  | `akadmin`     | `admin`       |

Keycloak realm test users (realm `account-payables`): `demo@acme.com` / `demo`
and `newhire@acme.com` / `demo`. Authentik API token for scripting:
`local-dev-authentik-api-token`. SCIM bearer (set by `pnpm scim:seed`):
`local-dev-scim-token-acme`.

## Databases

PostgreSQL hosts multiple databases for the multi-tenant architecture:

| Database           | Purpose              |
|--------------------|----------------------|
| `account_payables` | Control plane (orgs, users, roles) |
| `ap_acme`          | Acme Corp tenant data |
| `ap_techflow`      | TechFlow tenant data  |

The `init-tenants.sql` file is mounted at `docker-entrypoint-initdb.d/` and runs on first startup only. Additional tenant databases are created by `scripts/create_tenant.py`.

## Common Commands

```bash
# Start all services
docker compose up -d

# Stop all services
docker compose down

# View logs
docker compose logs -f
docker compose logs -f postgres

# Restart a service
docker compose restart postgres

# Check status
docker compose ps

# Full reset (removes all data including tenant DBs)
docker compose down -v
docker compose up -d
```

## Health Checks

All services have health checks configured:

- **PostgreSQL**: `pg_isready -U postgres` (every 5s)
- **Redis**: `redis-cli ping` (every 5s)
- **MinIO**: no explicit health check (starts immediately)

## Volumes

Data is persisted in Docker volumes:

| Volume      | Service    | Description            |
|-------------|------------|------------------------|
| `pgdata`    | PostgreSQL | Database files (all DBs)|
| `miniodata` | MinIO      | Uploaded files/objects |

Redis is in-memory only (no persistence in dev).

## Port Conflicts

If port 5432 is already in use (e.g. local Homebrew Postgres):

```bash
brew services stop postgresql@17   # adjust version as needed
```

Then restart Docker Compose.
