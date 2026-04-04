# Account Payables — Backend

Python 3.12+ / FastAPI / PostgreSQL / Redis / MinIO

See [/docs/backend.md](../docs/backend.md) for full documentation.

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
