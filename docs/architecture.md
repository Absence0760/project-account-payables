# Architecture

## System Overview

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

## Tech Stack

| Layer          | Tech                                    |
|----------------|-----------------------------------------|
| Frontend       | SvelteKit 2, Svelte 5, TypeScript       |
| Backend        | FastAPI, SQLAlchemy 2 (async), Pydantic |
| Database       | PostgreSQL 16                           |
| Cache/Queue    | Redis 7                                 |
| Object Storage | MinIO (S3-compatible)                   |
| Auth           | JWT (python-jose + passlib/bcrypt)      |
| Migrations     | Alembic                                 |
| Package Mgrs   | pnpm (frontend), uv (backend)          |

## Multi-Tenancy Strategy

- Every table carries an `organization_id` foreign key (row-level tenancy).
- PostgreSQL Row-Level Security (RLS) policies enforce isolation at the DB layer.
- FastAPI middleware injects `org_id` from the JWT/session on every request.
- Celery tasks always carry `org_id` in their payload.

## Data Flow

1. User signs in at `/login` — frontend POSTs credentials to `/api/auth/login`
2. Backend returns a JWT token, stored in `localStorage`
3. Token is attached as a `Bearer` header on all subsequent API requests
4. On 401 responses, the token is cleared and the user is redirected to `/login`
5. All API calls go through `src/lib/api.ts` which handles auth headers, error responses, and token lifecycle

## Project Structure

```
project-account-payables/
├── frontend/                  # SvelteKit SPA
│   ├── src/
│   │   ├── lib/
│   │   │   ├── api.ts         # API client (fetch wrapper, auth headers)
│   │   │   ├── components/    # Svelte components
│   │   │   ├── stores/        # Svelte 5 rune stores
│   │   │   └── types/         # TypeScript interfaces
│   │   └── routes/            # SvelteKit file-based routing
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
├── docs/                      # Documentation
└── README.md
```
