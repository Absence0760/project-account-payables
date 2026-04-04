# Account Payables

Full-stack accounts payable application. Everything runs locally for development.

## Prerequisites

- **Node.js** + **pnpm** (frontend)
- **Python 3.12+** (backend)
- **Docker & Docker Compose** (Postgres, Redis, MinIO)

## Quick Start

### 1. Start infrastructure (Postgres, Redis, MinIO)

```bash
cd backend
docker compose up -d
```

This starts:
- **PostgreSQL 16** on `localhost:5432` (user: `postgres`, password: `postgres`, db: `account_payables`)
- **Redis 7** on `localhost:6379`
- **MinIO** on `localhost:9000` (console: `localhost:9001`, user: `minioadmin`, password: `minioadmin`)

> **Note:** If you have a local Postgres running on port 5432 (e.g. via Homebrew), stop it first:
> `brew services stop postgresql@17`

### 2. Start the backend API

```bash
cd backend
python3 -m venv .venv        # first time only
source .venv/bin/activate
pip install -e ".[dev]"       # first time only
python scripts/seed.py        # first time only — creates tables + demo data
python main.py
```

### 3. Start the frontend

```bash
cd frontend
pnpm i                    # first time only
cp .env.example .env      # first time only
pnpm dev
```

### 4. Open the app

Go to http://localhost:7777 and sign in with the demo credentials.

## What's Running Where

| Service        | URL                       |
|----------------|---------------------------|
| Frontend       | http://localhost:7777      |
| Backend API    | http://localhost:8000      |
| Swagger docs   | http://localhost:8000/docs |
| MinIO console  | http://localhost:9001      |

## Demo Login

- **Email:** `demo@acme.com`
- **Password:** `demo`

The demo user is created by `seed.py` along with an organization (Acme Corp), 8 vendors, and 12 sample invoices.

## Architecture

```
┌──────────────┐       ┌──────────────┐       ┌─────────────────┐
│   Frontend   │──────>│  Backend API │──────>│  PostgreSQL 16  │
│  SvelteKit   │ HTTP  │   FastAPI    │  SQL  │  (Docker)       │
│  :7777       │       │   :8000      │       │  :5432          │
└──────────────┘       └──────┬───────┘       └─────────────────┘
                              │
                       ┌──────┴───────┐       ┌─────────────────┐
                       │   Redis 7    │       │   MinIO (S3)    │
                       │   :6379      │       │   :9000/:9001   │
                       └──────────────┘       └─────────────────┘
```

### Frontend → Backend Connection

The frontend connects to the backend API via the `PUBLIC_API_URL` environment variable (set in `frontend/.env`). In development this defaults to `http://localhost:8000`.

**Auth flow:**
1. User signs in at `/login` — the frontend POSTs credentials to `/api/auth/login`
2. Backend returns a JWT token
3. Token is stored in `localStorage` and attached as a `Bearer` header on all subsequent API requests
4. On 401 responses, the token is cleared and the user is redirected to `/login`

All API calls go through a single client module (`src/lib/api.ts`) that handles auth headers, error responses, and token lifecycle.

## Environment Configuration

### Frontend (`frontend/.env`)

| Variable         | Default                  | Description                           |
|------------------|--------------------------|---------------------------------------|
| `PUBLIC_API_URL` | `http://localhost:8000`  | Backend API URL                       |
| `BASE_PATH`      | (empty)                  | URL prefix for GitHub Pages deploys   |

Override `PUBLIC_API_URL` at build time for different environments:

```bash
# QA
PUBLIC_API_URL=https://api-qa.example.com pnpm build

# Production
PUBLIC_API_URL=https://api.example.com pnpm build
```

### Backend (`backend/.env`)

| Variable             | Default                                                                    | Description                     |
|----------------------|----------------------------------------------------------------------------|---------------------------------|
| `AP_DATABASE_URL`    | `postgresql+asyncpg://postgres:postgres@localhost:5432/account_payables`   | Async PostgreSQL connection     |
| `AP_SECRET_KEY`      | `change-me-in-production`                                                  | JWT signing key                 |
| `AP_S3_ENDPOINT_URL` | `http://localhost:9000`                                                    | MinIO/S3 endpoint               |
| `AP_S3_ACCESS_KEY`   | `minioadmin`                                                               | MinIO/S3 access key             |
| `AP_S3_SECRET_KEY`   | `minioadmin`                                                               | MinIO/S3 secret key             |
| `AP_S3_BUCKET`       | `invoices`                                                                 | S3 bucket for invoice files     |
| `AP_CORS_ORIGINS`    | `["http://localhost:7777", "http://localhost:5173"]`                       | Allowed CORS origins            |
| `AP_DEBUG`           | `true`                                                                     | Enable debug logging            |

## Tech Stack

| Layer          | Tech                                    |
|----------------|-----------------------------------------|
| Frontend       | SvelteKit 2, Svelte 5, TypeScript       |
| Backend        | FastAPI, SQLAlchemy 2 (async), Pydantic |
| Database       | PostgreSQL 16                           |
| Cache          | Redis 7                                 |
| Object Storage | MinIO (S3-compatible)                   |
| Auth           | JWT (python-jose + passlib/bcrypt)      |

## Project Structure

```
project-account-payables/
├── frontend/                  # SvelteKit SPA
│   ├── src/
│   │   ├── lib/
│   │   │   ├── api.ts         # API client (fetch wrapper, auth headers)
│   │   │   ├── components/    # Svelte components
│   │   │   ├── stores/        # Svelte 5 rune stores
│   │   │   │   ├── auth.svelte.ts      # Auth state, login/logout
│   │   │   │   ├── invoices.svelte.ts  # Invoice data (API-backed)
│   │   │   │   └── sidebar.svelte.ts   # Sidebar UI state
│   │   │   └── types/         # TypeScript interfaces
│   │   └── routes/
│   │       ├── +layout.svelte          # App shell with auth guard
│   │       ├── +page.svelte            # Dashboard (API: GET /api/dashboard)
│   │       ├── invoices/+page.svelte   # Invoice list (API: GET /api/invoices)
│   │       └── login/+page.svelte      # Login page
│   ├── .env                   # PUBLIC_API_URL config
│   └── .env.example
├── backend/                   # FastAPI API
│   ├── app/
│   │   ├── api/               # Route handlers
│   │   ├── models/            # SQLAlchemy ORM models
│   │   ├── schemas/           # Pydantic request/response models
│   │   ├── config.py          # Settings (pydantic-settings)
│   │   ├── database.py        # Async engine & session
│   │   └── main.py            # FastAPI app entrypoint
│   ├── alembic/               # Database migrations
│   ├── scripts/seed.py        # Sample data seeder
│   └── docker-compose.yml     # Postgres, Redis, MinIO
└── README.md
```

## Troubleshooting

**`database "account_payables" does not exist`**
A local Postgres is likely running on port 5432 and intercepting the connection. Stop it with `brew services stop postgresql@17` (adjust version as needed), then re-run `seed.py`.

**`passlib` / `bcrypt` errors**
The `passlib` library is incompatible with `bcrypt` 5.x. Pin it: `pip install "bcrypt>=4.0,<4.1"`
