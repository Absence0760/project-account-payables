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

Open http://localhost:7777 — login with `demo@acme.com` / `demo`

## Documentation

All documentation lives in the [`/docs`](docs/) folder:

| Document | Description |
|----------|-------------|
| [Getting Started](docs/getting-started.md) | Prerequisites, setup, and first run |
| [Architecture](docs/architecture.md) | System overview, tech stack, project structure |
| [Frontend](docs/frontend.md) | SvelteKit app structure, stores, conventions |
| [Backend](docs/backend.md) | FastAPI setup, project structure, linting, tests |
| [API Reference](docs/api-reference.md) | All REST endpoints with parameters |
| [Database](docs/database.md) | PostgreSQL schema, models, Alembic migrations |
| [MinIO](docs/minio.md) | S3-compatible object storage setup and usage |
| [Redis](docs/redis.md) | Cache and task queue service |
| [Authentication](docs/authentication.md) | JWT auth flow, RBAC, frontend/backend integration |
| [Docker](docs/docker.md) | Docker Compose services, commands, health checks |
| [Environment Variables](docs/environment.md) | Frontend and backend configuration |
| [Troubleshooting](docs/troubleshooting.md) | Common issues and fixes |
| [Implementation Plan](docs/plan.md) | 5-phase delivery roadmap |
