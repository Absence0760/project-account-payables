# Account Payables — Backend

Python 3.12+ / FastAPI / PostgreSQL / Redis / MinIO

## Prerequisites

- Python 3.12+
- Docker & Docker Compose (for PostgreSQL, Redis, MinIO)

## Quick Start

### 1. Start infrastructure services

```bash
cd backend
docker compose up -d
```

This starts:
- **PostgreSQL 16** on `localhost:5432` (user: `postgres`, password: `postgres`, db: `account_payables`)
- **Redis 7** on `localhost:6379`
- **MinIO** on `localhost:9000` (console: `localhost:9001`, user: `minioadmin`, password: `minioadmin`)

### 2. Create Python virtual environment and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 3. Set up environment variables (optional)

Copy the example and adjust if needed:

```bash
cp .env.example .env
```

Defaults work out of the box with the Docker Compose services. Environment variables are prefixed with `AP_`:

| Variable | Default | Description |
|---|---|---|
| `AP_DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/account_payables` | Async PostgreSQL connection string |
| `AP_SECRET_KEY` | `change-me-in-production` | JWT signing key |
| `AP_S3_ENDPOINT_URL` | `http://localhost:9000` | MinIO/S3 endpoint |
| `AP_S3_ACCESS_KEY` | `minioadmin` | MinIO/S3 access key |
| `AP_S3_SECRET_KEY` | `minioadmin` | MinIO/S3 secret key |
| `AP_S3_BUCKET` | `invoices` | S3 bucket for invoice files |
| `AP_DEBUG` | `true` | Enable debug logging |

### 4. Create tables and seed sample data

```bash
source .venv/bin/activate
python scripts/seed.py
```

This creates all database tables and inserts:
- 1 organization (Acme Corp)
- 1 demo user (`demo@acme.com` / password: `demo`)
- 8 vendors
- 12 sample invoices (matching the frontend mock data)

### 5. Run the API server

```bash
source .venv/bin/activate
python main.py
```

The API starts at **http://localhost:8000** with auto-reload enabled.

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- Health check: http://localhost:8000/api/health

## Database Migrations (Alembic)

Generate a new migration after model changes:

```bash
source .venv/bin/activate
alembic revision --autogenerate -m "description of change"
```

Apply migrations:

```bash
alembic upgrade head
```

## API Endpoints

### Auth
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/auth/login` | Login with email/password, returns JWT |
| `GET` | `/api/auth/me` | Get current user (requires Bearer token) |

### Invoices
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/invoices` | List invoices (paginated, filterable) |
| `GET` | `/api/invoices/{id}` | Get single invoice |
| `POST` | `/api/invoices` | Create invoice |
| `PATCH` | `/api/invoices/{id}` | Update invoice |
| `DELETE` | `/api/invoices/{id}` | Delete invoice |

**Query parameters for `GET /api/invoices`:**
`page`, `page_size`, `status`, `vendor`, `invoice_number`, `po_number`, `description`, `amount_min`, `amount_max`, `due_date_from`, `due_date_to`, `search`

### Vendors
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/vendors` | List vendors (paginated) |
| `GET` | `/api/vendors/{id}` | Get single vendor |
| `POST` | `/api/vendors` | Create vendor |
| `PATCH` | `/api/vendors/{id}` | Update vendor |
| `DELETE` | `/api/vendors/{id}` | Delete vendor |

### Dashboard
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/dashboard` | Aggregated KPIs (total invoices, amount, status counts) |

### Authentication

All endpoints except `/api/auth/login` and `/api/health` require a Bearer token:

```bash
# Login
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@acme.com","password":"demo"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Use the token
curl http://localhost:8000/api/invoices \
  -H "Authorization: Bearer $TOKEN"
```

## Running the Full Stack

From the repo root, start everything:

```bash
# Terminal 1 — Infrastructure
cd backend
docker compose up -d

# Terminal 2 — Backend API
cd backend
source .venv/bin/activate
python scripts/seed.py   # first time only
python main.py            # API on :8000

# Terminal 3 — Frontend
cd frontend
pnpm i                    # first time only
pnpm dev                  # Dev server on :7777
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

## Frontend Integration

The SvelteKit frontend connects to this API via the `PUBLIC_API_URL` env var (defaults to `http://localhost:8000`).

**CORS:** Allowed origins are configured in `app/config.py` via `AP_CORS_ORIGINS`. Defaults include `http://localhost:7777` (frontend dev server) and `http://localhost:5173`.

**Auth flow:**
1. Frontend POSTs `{ email, password }` to `/api/auth/login`
2. Backend returns `{ access_token, token_type }` (JWT)
3. Frontend sends `Authorization: Bearer <token>` on all subsequent requests
4. `/api/auth/me` returns the current user (used by frontend to validate the session)

## Troubleshooting

**`database "account_payables" does not exist`**
A local Postgres (e.g. from Homebrew) may be running on port 5432, intercepting the connection before Docker. Stop it: `brew services stop postgresql@17` (adjust version).

**`passlib` / `bcrypt` version error**
`passlib` is incompatible with `bcrypt` 5.x (`AttributeError: module 'bcrypt' has no attribute '__about__'`). Fix: `pip install "bcrypt>=4.0,<4.1"`

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
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
├── scripts/
│   └── seed.py           # Sample data seeder
├── alembic.ini
├── docker-compose.yml    # PostgreSQL, Redis, MinIO
├── Dockerfile
├── pyproject.toml
└── .env.example
```
