# frontend/tests-e2e/

Playwright end-to-end tests for the frontend. Targets the seeded `acme` tenant on `http://acme.localhost:7777`.

## Layout

```
tests-e2e/
├── playwright.config.ts        config; webServer block boots `pnpm dev`
├── fixtures/
│   └── helpers.ts              ACME_ADMIN credentials + signIn() helper
├── .auth/                      gitignored — reserved for future storage-state work
└── login.spec.ts               anon visitor, bad creds, happy-path login
```

## Local dev loop

The backend has to be running separately (Playwright only starts the frontend dev server). One terminal each:

```bash
# terminal 1 — backend
cd backend
docker compose up -d            # Postgres + Redis (+ MinIO)
pip install -e ".[dev]"
python scripts/seed.py          # creates the `acme` tenant + demo users
python main.py                  # uvicorn on :8000

# terminal 2 — playwright
cd frontend
pnpm install
pnpm exec playwright install chromium    # one-time browser install
pnpm test:e2e                            # auto-starts pnpm dev on :7777
pnpm test:e2e:ui                         # same, but with the picker UI
```

## CI

`.github/workflows/ci.yml`'s `e2e` job runs the same flow on every push/PR to main:

- Postgres 16 + Redis 7 as services
- Python 3.12 → install backend deps → `python scripts/seed.py` → `python main.py &`
- Wait for `/api/health` to answer 200
- pnpm 9 + Node 20 → `pnpm install` → `pnpm exec playwright install --with-deps chromium`
- `pnpm test:e2e` (which auto-starts the frontend dev server via the webServer block)
- Playwright report uploaded as an artifact on failure

## Seeded credentials

Defined in `backend/scripts/seed.py`. The fixture file `helpers.ts` re-exports the admin role; add the others as specs need them:

| Role | Email | Password |
|---|---|---|
| admin | `demo@acme.com` | `demo` |
| ap_manager | `demo+apmanager@acme.com` | `demo` |
| ap_clerk | `demo+apclerk@acme.com` | `demo` |
| cfo | `demo+cfo@acme.com` | `demo` |

There's also a second seeded tenant (`techflow`) with `admin@techflow.com` and `clerk@techflow.com` for cross-tenant isolation tests once you start writing them.

## Subdomain trick

The frontend resolves the tenant from the subdomain (`<slug>.localhost:7777` → `<slug>`). Chromium auto-resolves `*.localhost` to 127.0.0.1 per RFC 6761, so no `/etc/hosts` edits needed. To exercise the no-tenant marketing landing, override `baseURL` to `http://localhost:7777` via `test.use({ baseURL: 'http://localhost:7777' })` in the spec.

## Storage-state (future)

The auth-storage-state pattern (sign each user in once during globalSetup, persist to `.auth/<user>.json`, then `test.use({ storageState })` per spec) is the standard Playwright pattern for fast authenticated tests. It's not wired here yet — login is fast enough for the smoke spec to do it inline. Add the globalSetup back when the suite grows past ~10 specs and login latency becomes the bottleneck.
