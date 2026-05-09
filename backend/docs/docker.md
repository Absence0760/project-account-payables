# Docker

Docker Compose manages the infrastructure services: PostgreSQL, Redis, and MinIO.

## Starting Services

```bash
cd backend
docker compose up -d
```

This starts all three services in the background. On first run, PostgreSQL automatically creates three databases: `account_payables` (control plane), `ap_acme`, and `ap_techflow` (dev tenants) via the mounted `init-tenants.sql`.

To start individual services:

```bash
docker compose up -d postgres
docker compose up -d redis
docker compose up -d minio
```

## Services

| Service    | Image                    | Port(s)         | Description                                       |
|------------|--------------------------|-----------------|---------------------------------------------------|
| PostgreSQL | `pgvector/pgvector:pg16` | `5432`          | Primary database (multi-DB) + pgvector extension  |
| Redis      | `redis:7-alpine`         | `6379`          | JWT blocklist + rate-limit counters               |
| MinIO      | `minio/minio:latest`     | `9000`, `9001`  | S3-compatible storage                             |

The PostgreSQL image is `pgvector/pgvector:pg16` (official Postgres 16 + the [pgvector](https://github.com/pgvector/pgvector) extension) because the RAG-based extraction priors use a `vector(1536)` column. The image is binary-compatible with the vanilla `postgres:16` data directory, so switching from plain Postgres doesn't require a volume wipe — just `docker compose down && up -d`. If you do swap images on an existing volume, run `REINDEX DATABASE <name>` on each DB once to rebuild any text-column indexes affected by a collation-version change.

## Default Credentials

| Service    | Username      | Password      |
|------------|---------------|---------------|
| PostgreSQL | `postgres`    | `postgres`    |
| MinIO      | `minioadmin`  | `minioadmin`  |
| Redis      | (none)        | (none)        |

## Databases

PostgreSQL hosts multiple databases for the multi-tenant architecture:

| Database           | Purpose              |
|--------------------|----------------------|
| `account_payables` | Control plane (orgs, users, roles) |
| `ap_acme`          | Acme Corp tenant data |
| `ap_techflow`      | TechFlow tenant data  |

The `init-tenants.sql` file is mounted at `docker-entrypoint-initdb.d/` and runs on first startup only. Additional tenant databases are created by `scripts/create_tenant.py`.

## Common Commands

```bash
# Start all services
docker compose up -d

# Stop all services
docker compose down

# View logs
docker compose logs -f
docker compose logs -f postgres

# Restart a service
docker compose restart postgres

# Check status
docker compose ps

# Full reset (removes all data including tenant DBs)
docker compose down -v
docker compose up -d
```

## Health Checks

All services have health checks configured:

- **PostgreSQL**: `pg_isready -U postgres` (every 5s)
- **Redis**: `redis-cli ping` (every 5s)
- **MinIO**: no explicit health check (starts immediately)

## Volumes

Data is persisted in Docker volumes:

| Volume      | Service    | Description            |
|-------------|------------|------------------------|
| `pgdata`    | PostgreSQL | Database files (all DBs)|
| `miniodata` | MinIO      | Uploaded files/objects |

Redis is in-memory only (no persistence in dev).

## Port Conflicts

If port 5432 is already in use (e.g. local Homebrew Postgres):

```bash
brew services stop postgresql@17   # adjust version as needed
```

Then restart Docker Compose.
