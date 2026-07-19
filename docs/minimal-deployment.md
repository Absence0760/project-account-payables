# Minimal-cost deployment

How to get the whole app on the public internet for **~$20/month** (or ~$7/month
off-AWS), without building any of the reference AWS architecture in
[`production-deployment.md`](production-deployment.md). That doc stays the
scale-up target; this one is the pilot / first-customers footprint.

**Total: one small VM running Docker Compose + Caddy, real S3 for files,
everything else in-process.**

```
        *.app.<domain>  ──────► one VM (EC2 t4g.small)
                                 ├── Caddy         — TLS, static frontend, /api reverse-proxy
                                 ├── FastAPI       — backend container (uvicorn)
                                 ├── Postgres 16   — pgvector/pgvector:pg16 (control + tenant DBs)
                                 └── Redis 7       — token blocklist, rate limits, MFA state
                                        │
                                     AWS S3 — invoice files, backups (no MinIO in prod)
```

## Why this works without the big build-out

The app was designed local-first, and that carries straight into a cheap deploy:

- `AP_EXTRACTION_MODE` / `AP_ERP_MODE` / `AP_AUDIT_MODE` default to `local` —
  in-process worker threads, **no SQS, no Lambda**.
- Every provider integration defaults to its `mock` adapter; real providers
  (Claude Vision, a payment rail, Lithic) are per-org config flips later, not
  infrastructure.
- All background sweeps are asyncio tasks inside the API process, each behind
  an `AP_*_ENABLED` flag.
- The frontend is a static SPA; the only build-time input is `PUBLIC_API_URL`.
- Tenant routing is subdomain → `X-Tenant-Slug`; CORS for that is already
  solved by `AP_CORS_PRODUCTION_DOMAIN` (wildcard subdomain regex).

## What this deliberately does NOT give you

Single AZ, single instance, no autoscaling, no managed database, manual
deploys, restore-from-backup as the failover story. Acceptable for a pilot;
see [Upgrade triggers](#upgrade-triggers) for when each piece graduates.

## Cost breakdown

| Item | Monthly (us-east-1, on-demand) |
|---|---|
| EC2 `t4g.small` (2 vCPU ARM, 2 GB) | ~$12.30 |
| EBS 30 GB gp3 | ~$2.40 |
| Public IPv4 address | ~$3.65 |
| Route 53 hosted zone | $0.50 |
| KMS key (sops) | $1.00 |
| S3 (files + backups, pilot volume) + SES | ~$1 |
| **Total** | **~$21** |

Domain registration (~$12/yr) extra if you buy a product apex instead of using
a delegated `<project>.jaredhoward.com` zone.

**Cheaper still:** a Hetzner CAX11 (2 vCPU ARM, 4 GB, ~€3.79) replaces the
EC2+EBS+IPv4 rows → **~$7/month total** (keep KMS + Route 53 + S3 on AWS).
Trade-off: no instance profile, so the box needs a scoped IAM access key for
S3/KMS instead of role-based credentials, and it sits outside the AWS-org
guardrails. The AWS path is recommended because the estate tooling (org
sub-account, sops KMS key, OIDC role) already automates it.

If 2 GB gets tight (OCR/extraction spikes), `t4g.medium` (4 GB) is ~$24.50 —
resize is a stop → change-type → start. Add 2 GB of swap either way.

## Key decisions

1. **Real S3 instead of MinIO in prod.** The `infra/` Terraform module already
   defines the invoice-files and audit-logs buckets (versioning, Object Lock,
   SSE-KMS). Set `AP_S3_BUCKET`, omit `AP_S3_ENDPOINT_URL`, and drop the MinIO
   container — less RAM, real durability, pennies at pilot volume.
2. **Caddy on the VM serves the frontend.** GitHub Pages can't serve wildcard
   tenant subdomains and CloudFront+ACM is more moving parts. Caddy serves the
   static `frontend/build`, reverse-proxies `api.<domain>` to the backend, and
   auto-provisions TLS. Start with an **explicit hostname list** (one line per
   tenant subdomain — plain HTTP-01, no DNS plugin, no extra IAM); move to a
   wildcard cert via DNS-01 + the Route 53 plugin only when tenant churn makes
   the list annoying.
3. **Everything stays in `local` dispatch mode.** The Lambda/SQS split exists
   for burst isolation, which a pilot doesn't have.
4. **Secrets follow the estate pattern.** This repo is public — `*.sops` files
   go in the private `Absence0760/infra-secrets` repo (per-project subdir +
   per-project KMS key), never committed here. The EC2 instance profile gets
   `kms:Decrypt` + scoped S3 access, so no static AWS keys live on the box;
   the entrypoint decrypts to env at boot (`set -a; . <(sops -d …); set +a`).
5. **Manual deploys.** SSH in: `git pull`, rebuild, migrate, restart (script
   below). `aws-deploy.yml` stays disarmed (`AWS_DEPLOY_ENABLED` unset) until
   the ECS build-out exists.

## Step-by-step

### 0. Account, DNS, secrets substrate

- Create/choose the AWS account (estate: `new-project-account.sh <slug>` gives
  the sub-account, tfstate bucket, sops KMS key, and a delegated
  `<slug>.jaredhoward.com` zone; set `create_subdomain = false` and buy an apex
  instead if this is customer-facing).
- Bootstrap the project's subdir in the private `infra-secrets` repo
  (`bin/sops-init.sh --project <slug> --region <r>` there — see
  `~/github/project-mgmt/docs/secrets-management.md`). Do **not** run this
  repo's in-repo `./bin/sops-init.sh`.
- `terraform apply` the existing `infra/` module for the S3 buckets + app KMS
  key.

### 1. VM

- EC2 `t4g.small`, Amazon Linux 2023 or Debian arm64, 30 GB gp3, security
  group: 80/443 from anywhere, 22 from your IP (or SSM Session Manager and no
  22 at all).
- Instance profile: `kms:Decrypt` on the sops key; `s3:GetObject/PutObject/
  ListBucket` on the invoice-files, audit-logs, and backups buckets.
- Install docker + compose plugin; add 2 GB swap.
- DNS: `A` records for `app.<domain>`, `api.<domain>`, and each tenant
  subdomain (`acme.app.<domain>`) → the instance IP.

### 2. Production compose file (to be added under `deploy/`)

A `deploy/compose.prod.yml` with four services — this file does not exist yet
and is the main implementation task of this plan:

- `postgres` — `pgvector/pgvector:pg16`, volume-backed, **no host port**
  (compose-network only), real password.
- `redis` — `redis:7-alpine` with `--appendonly yes`, no host port.
- `api` — built from `backend/Dockerfile` (works on arm64; the lock resolves
  universally — if an arm64 wheel gap ever bites, fall back to an x86
  `t3a.small`, ~$14). Entrypoint: decrypt sops env → `alembic upgrade head` is
  **not** run here (see deploy script) → `uvicorn app.main:app` (the production
  entrypoint — not `main.py`).
- `caddy` — ports 80/443, mounts the built `frontend/build` as the site root
  and a `Caddyfile`:
  - `app.<domain>`, `acme.app.<domain>`, … → `root` + `file_server` +
    SPA fallback (`try_files {path} /index.html`)
  - `api.<domain>` → `reverse_proxy api:8000`

Frontend build (on the VM or locally, artifact rsynced up):
`PUBLIC_API_URL=https://api.<domain> pnpm -C frontend build`

### 3. Backend env (the sops-managed `.env`)

Beyond the committed defaults, the deployed env sets at minimum:

| Var | Value |
|---|---|
| `AP_ENVIRONMENT` | `production` (arms hCaptcha enforcement on signup) |
| `AP_SECRET_KEY` | `openssl rand -hex 32` |
| `AP_DATABASE_URL` | `postgresql+asyncpg://…@postgres:5432/account_payables` |
| `AP_REDIS_URL` | `redis://redis:6379` |
| `AP_S3_BUCKET` | invoice-files bucket (no `AP_S3_ENDPOINT_URL`) |
| `AP_MFA_ENABLED` / `AP_HSTS_ENABLED` | `true` / `true` |
| `AP_PUBLIC_URL` / `AP_API_PUBLIC_URL` | `https://app.<domain>` / `https://api.<domain>` |
| `AP_TENANT_URL_TEMPLATE` | `https://{slug}.app.<domain>` |
| `AP_CORS_PRODUCTION_DOMAIN` | `app.<domain>` |
| `AP_EMAIL_PROVIDER` / `AP_EMAIL_FROM` | `ses` / verified sender |
| `AP_APPROVAL_SIGNING_KEY` + the other HMAC signing keys | real values (each key's presence is its feature's on-switch; leave unset = feature off) |

Everything else keeps its safe default: mock adapters, `local` modes, sweeps
off. Flip individual `AP_*_ENABLED` sweeps on once there's a reason
(`AP_PAYMENT_RECONCILE_ENABLED` and `AP_AUDIT_SHIPPING_ENABLED` are the two
worth enabling first when real payments/compliance start).

SES note: a fresh SES account is sandboxed (verified recipients only). Either
request production access, or skip self-service signup at first and provision
tenants by CLI (`python scripts/create_tenant.py …`), leaving email on
`console` until SES clears.

### 4. First boot

```bash
docker compose -f deploy/compose.prod.yml up -d postgres redis && docker compose -f deploy/compose.prod.yml run --rm api alembic upgrade head && docker compose -f deploy/compose.prod.yml up -d
```

Then create the first tenant with `create_tenant.py`, add its subdomain to DNS
+ the Caddyfile, `docker compose exec caddy caddy reload`. Do **not** run
`scripts/seed.py` (demo data) in prod.

### 5. Deploy script (`deploy/deploy.sh`, to be added)

One SSH-able script, run on the VM:

```bash
git pull && docker compose -f deploy/compose.prod.yml build api && docker compose -f deploy/compose.prod.yml run --rm api sh -c "alembic upgrade head && python scripts/migrate_all_tenants.py" && docker compose -f deploy/compose.prod.yml up -d api && PUBLIC_API_URL=https://api.<domain> pnpm -C frontend build
```

Migrations run **before** the new API serves traffic, and fan out to every
tenant DB — same ordering contract as the (future) ECS pipeline.

### 6. Backups (this is the whole DR story — do not skip)

- Nightly cron: `pg_dumpall` (or per-DB `pg_dump` of `account_payables` + every
  `ap_*`) → gzip → `aws s3 cp` to a versioned backups bucket with a lifecycle
  rule (e.g. expire after 90 days). The instance profile already has the
  access.
- Weekly EBS snapshot (Data Lifecycle Manager, free to configure) as the
  coarse fallback.
- **Test a restore once** before calling this done: new volume, restore dump,
  point a scratch compose stack at it.
- RPO ≈ 24h, RTO ≈ hours (new VM + restore). If a customer needs better, that
  is the RDS trigger below.

## Upgrade triggers

| Signal | Move |
|---|---|
| A customer asks about uptime SLA / RPO < 24h | Postgres → RDS (single-AZ first, ~$15–30/mo) |
| Extraction/OCR saturates the VM | `AP_EXTRACTION_MODE=lambda` + the SQS/Lambda pair (already implemented in code) |
| Tenant churn makes the Caddy host list annoying | Wildcard DNS-01 cert (Caddy Route 53 plugin) or CloudFront + ACM |
| Real payment volume | Enable `AP_PAYMENT_RECONCILE_ENABLED`, sanctions/audit-shipping sweeps, and revisit the full architecture doc |
| >1 instance needed | The full `production-deployment.md` build-out — ECS/ALB/RDS/ElastiCache; the compose file retires |

## Open implementation tasks

The plan above needs these repo additions (none exist yet):

1. `deploy/compose.prod.yml` + `deploy/Caddyfile` + `deploy/deploy.sh`
2. Backup cron script (`deploy/backup.sh`)
3. Terraform for the VM + instance profile (optional — clicking it out in the
   console is defensible at this scale; the S3/KMS module in `infra/` already
   exists)
