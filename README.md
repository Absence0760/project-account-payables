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
| [Environment Variables](docs/environment.md) | Frontend and backend configuration |
| [Production Deployment](docs/production-deployment.md) | AWS, CloudFront, ALB, ECS |
| [Troubleshooting](docs/troubleshooting.md) | Common issues and fixes |
| [Roadmap](docs/roadmap.md) | Feature backlog with status |
| [Competitive Analysis](docs/competitive-analysis.md) | Market landscape |
| [Implementation Plan](docs/plan.md) | 5-phase delivery roadmap |

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
| [PO Matching](backend/docs/po-matching.md) | 2-way/3-way matching logic |
| [Vendor Management](backend/docs/vendor-management.md) | Sources, sync, matching |
| [Local AI Testing](backend/docs/local-ai-testing.md) | Ollama setup |

**Subproject guides**

| File | Purpose |
|------|---------|
| [`backend/CLAUDE.md`](backend/CLAUDE.md) | Backend structure, adapters, conventions |
| [`frontend/CLAUDE.md`](frontend/CLAUDE.md) | Routes, stores, components, API mappings |
| [`mobile/CLAUDE.md`](mobile/CLAUDE.md) | Flutter screens, stores, API client |
