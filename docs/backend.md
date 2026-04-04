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
python scripts/seed.py   # first time only — seeds control plane + 2 tenant DBs
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
│   │   ├── auth.py       # Login, current user (control-plane DB)
│   │   ├── dashboard.py  # KPI aggregation (tenant DB)
│   │   ├── deps.py       # Shared dependencies (auth)
│   │   ├── invoices.py   # Invoice CRUD (tenant DB)
│   │   └── vendors.py    # Vendor CRUD (tenant DB)
│   ├── models/           # SQLAlchemy ORM models
│   │   ├── base.py       # Base class, mixins
│   │   ├── exception.py  # AP exceptions
│   │   ├── invoice.py    # Invoices, line items, extraction results
│   │   ├── organization.py  # Org with db_name for tenant routing
│   │   ├── payment.py    # Payment runs, schedules, payments
│   │   ├── procurement.py # POs, goods receipts
│   │   ├── user.py       # Users, roles
│   │   ├── vendor.py
│   │   └── workflow.py   # Workflow definitions, instances, steps, audit log
│   ├── schemas/          # Pydantic request/response models
│   ├── config.py         # Settings via pydantic-settings
│   ├── database.py       # Control engine + tenant engine cache
│   ├── tenant.py         # Tenant resolution (X-Tenant-Slug → DB session)
│   └── main.py           # FastAPI app entrypoint
├── alembic/              # Database migrations
├── scripts/
│   ├── seed.py           # Multi-tenant seeder (2 demo tenants)
│   ├── create_tenant.py  # Provision a new tenant
│   └── migrate_all_tenants.py  # Run migrations across all tenant DBs
├── init-tenants.sql      # Docker init script for dev tenant DBs
├── alembic.ini
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── .env.example
```

## Multi-Tenant Database Architecture

The backend uses a **control-plane + tenant DB** pattern:

- **Control-plane DB** (`account_payables`): `organizations`, `users`, `roles`, `user_roles`
- **Tenant DBs** (`ap_<slug>`): all business tables (invoices, vendors, payments, etc.)

Auth routes use `get_control_db()`. Business routes use `get_tenant_db()` which resolves via the `X-Tenant-Slug` request header.

See [multi-tenancy.md](multi-tenancy.md) for full details.

## Seed Data

`python scripts/seed.py` creates:
- 2 Organizations (Acme Corp, TechFlow Inc) with separate databases
- 2 Users (`demo@acme.com`, `admin@techflow.com` — both password: `demo`)
- 4 Roles (admin, ap_manager, ap_clerk, cfo)
- 8 Vendors and 8 Invoices per tenant

## Provisioning New Tenants

```bash
python scripts/create_tenant.py \
  --name "New Corp" --slug newcorp \
  --admin-email admin@newcorp.com --admin-password changeme
```

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

Uses regex-based origin matching to support any tenant subdomain:

```python
allow_origin_regex=r"https?://([\w-]+\.)?(localhost(:\d+)?|app\.com)"
```
