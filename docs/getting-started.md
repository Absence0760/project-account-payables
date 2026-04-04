# Getting Started

Full-stack accounts payable application. Everything runs locally for development.

## Prerequisites

- **Node.js** + **pnpm** (frontend)
- **Python 3.12+** (backend)
- **Docker & Docker Compose** (PostgreSQL, Redis, MinIO)

## Quick Start

### 1. Start infrastructure (PostgreSQL, Redis, MinIO)

```bash
cd backend
docker compose up -d
```

This starts:
- **PostgreSQL 16** on `localhost:5432`
- **Redis 7** on `localhost:6379`
- **MinIO** on `localhost:9000` (console: `localhost:9001`)

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

## Demo Login

- **Email:** `demo@acme.com`
- **Password:** `demo`

The demo user is created by `seed.py` along with an organization (Acme Corp), 8 vendors, and 12 sample invoices.

## What's Running Where

| Service        | URL                       |
|----------------|---------------------------|
| Frontend       | http://localhost:7777      |
| Backend API    | http://localhost:8000      |
| Swagger docs   | http://localhost:8000/docs |
| MinIO console  | http://localhost:9001      |
