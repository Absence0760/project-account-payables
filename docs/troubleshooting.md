# Troubleshooting

## `database "account_payables" does not exist`

A local PostgreSQL (e.g. from Homebrew) is likely running on port 5432, intercepting the connection before the Docker container.

**Fix:** Stop the local Postgres:

```bash
brew services stop postgresql@17   # adjust version as needed
```

Then re-run `seed.py` or restart the backend.

## `passlib` / `bcrypt` errors

`passlib` is incompatible with `bcrypt` 5.x, resulting in:

```
AttributeError: module 'bcrypt' has no attribute '__about__'
```

**Fix:** Pin bcrypt to 4.x:

```bash
pip install "bcrypt>=4.0,<4.1"
```

## Frontend can't connect to backend

- Check that the backend is running on port 8000: `curl http://localhost:8000/api/health`
- Verify `PUBLIC_API_URL` in `frontend/.env` is set to `http://localhost:8000`
- Check CORS: `AP_CORS_ORIGINS` in `backend/.env` must include the frontend URL (`http://localhost:7777`)

## Docker services won't start

```bash
# Check what's running
docker compose ps

# View logs for errors
docker compose logs -f

# Full reset
docker compose down -v
docker compose up -d
```

## MinIO bucket doesn't exist

If file uploads fail because the `invoices` bucket hasn't been created:

1. Open the MinIO console at http://localhost:9001
2. Login with `minioadmin` / `minioadmin`
3. Go to Buckets → Create Bucket → name it `invoices`

Or via CLI:

```bash
mc alias set local http://localhost:9000 minioadmin minioadmin
mc mb local/invoices
```

## Seed script fails

Make sure Docker services are running first:

```bash
cd backend
docker compose up -d
python scripts/seed.py
```

If you see connection errors, wait a few seconds for PostgreSQL to finish starting, then retry.
