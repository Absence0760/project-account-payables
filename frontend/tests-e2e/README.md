# frontend/tests-e2e/

Playwright end-to-end tests for the frontend. Targets the seeded `acme` tenant on `http://acme.localhost:7777`.

## Layout

```
tests-e2e/
├── playwright.config.ts                config; webServer block boots `pnpm dev`
├── fixtures/
│   └── helpers.ts                      seeded credentials + signIn / signOut helpers
├── .auth/                              gitignored — reserved for storage-state work
├── auth/                               entry surface — auth, RBAC, tenant isolation
│   ├── auth-wall.spec.ts               protected routes redirect anon to /login
│   ├── login.spec.ts                   form renders / bad creds / happy path / no-tenant
│   ├── signout.spec.ts                 Log Out clears session + post-logout redirect
│   ├── signup.spec.ts                  /signup form + slug-check accept/reject
│   ├── rbac.spec.ts                    sidebar visibility per role (clerk / mgr / cfo)
│   └── tenant-isolation.spec.ts        techflow JWT cannot reach acme data
├── invoices/                           invoice surface
│   ├── list.spec.ts                    list renders, search interactive
│   ├── status-filter.spec.ts           filter chips narrow / restore the table
│   └── detail.spec.ts                  modal heading / line items / activity / close
├── payments/payments.spec.ts           summary cards, tabs, run detail modal
├── vendors/vendors.spec.ts             list, default chip, server-side search
├── workflows/workflows.spec.ts         seeded default workflow + new-workflow modal
├── exceptions/exceptions.spec.ts       seeded exception cards + summary chips
├── admin/admin.spec.ts                 user list + "You" badge + Invite modal
└── smoke/nav.spec.ts                   admin reaches every sidebar route
```

`testDir: '.'` in playwright.config.ts walks recursively, so adding a new spec file under any of these folders picks up automatically. Single-spec folders (payments / vendors / workflows / exceptions / admin) are intentional — they're future-proof for when each surface grows beyond one file.

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
