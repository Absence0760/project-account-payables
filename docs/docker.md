# Docker

Docker Compose manages the infrastructure services: PostgreSQL, Redis, and MinIO.

## Starting Services

```bash
cd backend
docker compose up -d
```

This starts all three services in the background. To start individual services:

```bash
docker compose up -d postgres
docker compose up -d redis
docker compose up -d minio
```

## Services

| Service    | Image              | Port(s)         | Description               |
|------------|--------------------|-----------------|---------------------------|
| PostgreSQL | `postgres:16-alpine` | `5432`        | Primary database          |
| Redis      | `redis:7-alpine`     | `6379`        | Cache / task queue        |
| MinIO      | `minio/minio:latest` | `9000`, `9001`| S3-compatible storage     |

## Default Credentials

| Service    | Username      | Password      |
|------------|---------------|---------------|
| PostgreSQL | `postgres`    | `postgres`    |
| MinIO      | `minioadmin`  | `minioadmin`  |
| Redis      | (none)        | (none)        |

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

# Full reset (removes all data)
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
| `pgdata`    | PostgreSQL | Database files         |
| `miniodata` | MinIO      | Uploaded files/objects |

Redis is in-memory only (no persistence in dev).

## Port Conflicts

If port 5432 is already in use (e.g. local Homebrew Postgres):

```bash
brew services stop postgresql@17   # adjust version as needed
```

Then restart Docker Compose.
