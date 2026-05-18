# Account Payables

Full-stack accounts payable management application built with SvelteKit, FastAPI, and PostgreSQL.

## Quick Start

```bash
# 1. Start infrastructure (Postgres, Redis, MinIO)
cd backend && docker compose up -d

# 2. Start backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python scripts/seed.py
python main.py

# 3. Start frontend
cd frontend
pnpm i && cp .env.example .env
pnpm dev
```

Open http://acme.localhost:7777 — login with `demo@acme.com` / `demo`

## Documentation

Cross-cutting docs live in [`/docs`](docs/). Backend-specific docs live in [`/backend/docs`](backend/docs/). Frontend and mobile details live in their subproject `CLAUDE.md` files.

**Cross-cutting (`/docs`)**

| Document | Description |
|----------|-------------|
| [Getting Started](docs/getting-started.md) | Prerequisites, setup, and first run |
| [Architecture](docs/architecture.md) | System overview, tech stack, project structure |
| [Authentication](docs/authentication.md) | JWT auth flow, RBAC, frontend/backend integration |
| [User Management](docs/user-management.md) | Role matrix, user admin |
| [Multi-Tenancy](docs/multi-tenancy.md) | Subdomain routing, DB-per-tenant, provisioning |
| [Self-Service Signup](docs/self-service-signup.md) | Public signup flow, abuse mitigations |
| [Environment Variables](docs/environment.md) | Frontend and backend configuration |
| [Production Deployment](docs/production-deployment.md) | AWS, CloudFront, ALB, ECS |
| [Backup & Disaster Recovery](docs/backup-disaster-recovery.md) | RTO/RPO, restore procedures |
| [Secrets Rotation](docs/secrets-rotation.md) | What to rotate, when, and how |
| [SOC 2 Readiness](docs/soc2-readiness.md) | Control mapping, vendor selection, kickoff plan |
| [Troubleshooting](docs/troubleshooting.md) | Common issues and fixes |
| [Founder Runbooks](docs/founder-runbooks/) | Non-code playbooks — legal, prod deploy, Stripe, payment rails, SOC 2, support |
| [Roadmap](docs/roadmap.md) | Feature backlog with status |
| [Competitive Analysis](docs/competitive-analysis.md) | Market landscape |

**Backend (`/backend/docs`)**

| Document | Description |
|----------|-------------|
| [API Reference](backend/docs/api-reference.md) | All REST endpoints with parameters |
| [Database](backend/docs/database.md) | PostgreSQL schema, models, Alembic migrations |
| [Docker](backend/docs/docker.md) | Docker Compose services, commands, health checks |
| [Redis](backend/docs/redis.md) | Token blocklist and cache |
| [MinIO](backend/docs/minio.md) | S3-compatible object storage setup |
| [AI Extraction](backend/docs/ai-extraction.md) | Platform vs BYOK, provider configs |
| [ERP Integration](backend/docs/erp-integration.md) | Adapter pattern, Merge.dev, direct APIs |
| [Workflow Design](backend/docs/workflow-design.md) | State machine, step types |
| [Workflow Snapshots](backend/docs/workflow-snapshots.md) | Frozen definition semantics |
| [Payments](backend/docs/payments.md) | Payment runs, schedules, ERP sync |
| [Virtual Cards](backend/docs/virtual-cards.md) | Lithic/Nium, rebates, webhooks |
| [International Payments](backend/docs/international-payments.md) | Cross-border rails, FX, sanctions screening |
| [Bank Reconciliation](backend/docs/bank-reconciliation.md) | Statement import, match-to-payment |
| [PO Matching](backend/docs/po-matching.md) | 2-way/3-way matching logic |
| [Vendor Management](backend/docs/vendor-management.md) | Sources, sync, matching |
| [Supplier Portal](backend/docs/supplier-portal.md) | VendorUser auth, invoice submission |
| [Email Intake](backend/docs/email-intake.md) | Inbound email-to-invoice, SES + Mailgun |
| [CSV Import](backend/docs/csv-import.md) | Day-0 vendor + invoice migration |
| [Analytics](backend/docs/analytics.md) | CFO dashboard metrics, exports, scheduled reports |
| [Audit Log Shipping](backend/docs/audit-log-shipping.md) | Centralized WORM sink, S3 Object Lock |
| [1099 Tracking](backend/docs/tax-1099.md) | W-9 collection, YTD reporting |
| [Local AI Testing](backend/docs/local-ai-testing.md) | Ollama setup |

**Subproject guides**

| File | Purpose |
|------|---------|
| [`backend/CLAUDE.md`](backend/CLAUDE.md) | Backend structure, adapters, conventions |
| [`frontend/CLAUDE.md`](frontend/CLAUDE.md) | Routes, stores, components, API mappings |
| [`mobile/CLAUDE.md`](mobile/CLAUDE.md) | Flutter screens, stores, API client |
