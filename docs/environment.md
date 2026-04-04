# Environment Variables

## Frontend (`frontend/.env`)

| Variable         | Default                 | Description                         |
|------------------|-------------------------|-------------------------------------|
| `PUBLIC_API_URL` | `http://localhost:8000` | Backend API URL (embedded at build time) |
| `BASE_PATH`      | (empty)                 | URL prefix for GitHub Pages deploys |

Copy the example file:

```bash
cd frontend
cp .env.example .env
```

Override at build time for different environments:

```bash
PUBLIC_API_URL=https://api-qa.example.com pnpm build    # QA
PUBLIC_API_URL=https://api.example.com pnpm build        # Production
```

## Backend (`backend/.env`)

| Variable             | Default                                                                  | Description                  |
|----------------------|--------------------------------------------------------------------------|------------------------------|
| `AP_DATABASE_URL`    | `postgresql+asyncpg://postgres:postgres@localhost:5432/account_payables` | Async PostgreSQL connection  |
| `AP_SECRET_KEY`      | `change-me-in-production`                                                | JWT signing key              |
| `AP_S3_ENDPOINT_URL` | `http://localhost:9000`                                                  | MinIO/S3 endpoint            |
| `AP_S3_ACCESS_KEY`   | `minioadmin`                                                             | MinIO/S3 access key          |
| `AP_S3_SECRET_KEY`   | `minioadmin`                                                             | MinIO/S3 secret key          |
| `AP_S3_BUCKET`       | `invoices`                                                               | S3 bucket for invoice files  |
| `AP_CORS_ORIGINS`    | `["http://localhost:7777", "http://localhost:5173"]`                     | Allowed CORS origins         |
| `AP_DEBUG`           | `true`                                                                   | Enable debug logging         |

Copy the example file:

```bash
cd backend
cp .env.example .env
```

Defaults work out of the box with the Docker Compose services. All backend variables are prefixed with `AP_` and loaded via `pydantic-settings` in `app/config.py`.
