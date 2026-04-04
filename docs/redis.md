# Redis

Redis 7 running in Docker, reserved for caching and task queue (Celery) in future phases.

## Running

Redis is started as part of the Docker Compose stack:

```bash
cd backend
docker compose up -d redis
```

## Access

- **Host:** `localhost`
- **Port:** `6379`
- **No password** (development only)

## Current Usage

Redis is provisioned but not yet actively used by the application. It is reserved for:

- **Celery task queue** — async jobs for email polling, invoice extraction, and notifications (Phase 2+)
- **Caching** — API response caching and session storage

## Connecting via CLI

```bash
# Using redis-cli directly
redis-cli -h localhost -p 6379

# Or via Docker
docker exec -it backend-redis-1 redis-cli

# Test connection
> PING
PONG
```

## Data Persistence

Redis data is stored in-memory and is **not persisted** across container restarts in the current Docker Compose configuration. This is appropriate for development; production deployments should configure Redis persistence or use a managed service.
