# Backend

Python 3.12+ / FastAPI / SQLAlchemy 2 (async) / Pydantic 2

## Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Copy the env file (optional — defaults work with Docker Compose):

```bash
cp .env.example .env
```

## Running

```bash
source .venv/bin/activate
python scripts/seed.py   # first time only — creates tables + demo data
python main.py            # API on :8000 with auto-reload
```

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- Health check: http://localhost:8000/api/health

## Project Structure

```
backend/
├── app/
│   ├── api/              # FastAPI route handlers
│   │   ├── auth.py       # Login, current user
│   │   ├── dashboard.py  # KPI aggregation
│   │   ├── deps.py       # Shared dependencies (auth, tenant)
│   │   ├── invoices.py   # Invoice CRUD
│   │   └── vendors.py    # Vendor CRUD
│   ├── models/           # SQLAlchemy ORM models
│   │   ├── base.py       # Base class, mixins
│   │   ├── exception.py  # AP exceptions
│   │   ├── invoice.py    # Invoices, line items, extraction results
│   │   ├── organization.py
│   │   ├── payment.py    # Payment runs, schedules, payments
│   │   ├── procurement.py # POs, goods receipts
│   │   ├── user.py       # Users, roles
│   │   ├── vendor.py
│   │   └── workflow.py   # Workflow definitions, instances, steps, audit log
│   ├── schemas/          # Pydantic request/response models
│   │   ├── auth.py
│   │   ├── dashboard.py
│   │   ├── invoice.py
│   │   └── vendor.py
│   ├── config.py         # Settings via pydantic-settings
│   ├── database.py       # Async SQLAlchemy engine & session
│   └── main.py           # FastAPI app entrypoint
├── alembic/              # Database migrations
├── scripts/
│   └── seed.py           # Sample data seeder
├── alembic.ini
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── .env.example
```

## Seed Data

`python scripts/seed.py` creates:
- 1 Organization (Acme Corp)
- 1 Demo user (`demo@acme.com` / password: `demo`)
- 4 Roles (admin, ap_manager, ap_clerk, cfo)
- 8 Vendors
- 12 Sample invoices

## Linting

```bash
source .venv/bin/activate
ruff check app/
ruff format app/
```

## Tests

```bash
source .venv/bin/activate
pytest
```

## CORS

Allowed origins are configured in `app/config.py` via `AP_CORS_ORIGINS`. Defaults include `http://localhost:7777` (frontend dev server) and `http://localhost:5173`.
