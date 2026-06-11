"""Dev entrypoint — run with `python main.py` or `uv run main.py`.

Local-dev only. Loads env files so a fresh clone runs with zero setup:
committed `.env.development` (safe local defaults) fills the gaps, while a
gitignored `.env` (personal overrides) and the real process environment both
win over it (`override=False`). Deployed environments run `app.main:app`
straight — never this file — so this dotenv loading never touches Lambda/prod
(see backend/CLAUDE.md § "No dotenv in Lambda paths").
"""

from pathlib import Path

import uvicorn
from dotenv import load_dotenv

_HERE = Path(__file__).parent
# Precedence (highest first): real env > personal .env > committed .env.development.
load_dotenv(_HERE / ".env", override=False)
load_dotenv(_HERE / ".env.development", override=False)

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
