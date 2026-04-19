# Account Payables — Backend

Python 3.12+ / FastAPI / PostgreSQL / Redis / MinIO

See [`CLAUDE.md`](CLAUDE.md) for full documentation and [`docs/`](docs/) for deep-dives (API, database, adapters, workflows).

## Quick Start

```bash
docker compose up -d
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python scripts/seed.py
python main.py
```

API runs on http://localhost:8000 — Swagger docs at http://localhost:8000/docs

## Multi-Tenancy

Uses database-per-tenant isolation. The `X-Tenant-Slug` header routes requests to the correct tenant DB.

- Control plane DB: `account_payables` (orgs, users, roles)
- Tenant DBs: `ap_acme`, `ap_techflow`, etc.

Provision a new tenant:

```bash
python scripts/create_tenant.py --name "New Corp" --slug newcorp \
  --admin-email admin@newcorp.com --admin-password changeme
```

See [`../docs/multi-tenancy.md`](../docs/multi-tenancy.md) for details.
