# frontend/tests-e2e/

Playwright end-to-end tests for the frontend.

## Parallelism + isolation

Two modes:

**CI: 14 shards × 1 worker.** `.github/workflows/ci.yml` fans the
suite out across 14 GitHub runners via Playwright's `--shard=N/14`.
Each shard is its own job with its own Postgres + Redis + seeded
backend (1 tenant: `e2e1`). Inside a shard, `workers=1` means the
shard's slice of the suite runs sequentially — no within-shard
parallel-worker contention. Specs in a shard share the shard's
tenant, but the serial execution order makes state mutations
predictable (the same model the suite was originally written
under).

**Local: workers=4 by default.** Run from anywhere with
`pnpm test:e2e` and four Playwright workers fan out against four
seeded tenants (`e2e1`..`e2e4`) for a faster inner loop. Each
worker is pinned to its own tenant via the worker-scoped
`tenantSlug` fixture in `fixtures/helpers.ts`; two workers running
the same spec can't collide because they're operating against
different Postgres databases.

| Worker index | Tenant slug | Base URL                     |
| ------------ | ----------- | ---------------------------- |
| 0            | `e2e1`      | `http://e2e1.localhost:7777` |
| 1            | `e2e2`      | `http://e2e2.localhost:7777` |
| 2            | `e2e3`      | `http://e2e3.localhost:7777` |
| 3            | `e2e4`      | `http://e2e4.localhost:7777` |

If a local flake suggests within-worker spec interference, run
serially: `PLAYWRIGHT_WORKERS=1 pnpm test:e2e`. That matches
the CI-shard behaviour.

### Specs that don't follow the worker-tenant pattern

| Spec | Why it's pinned |
| ---- | --------------- |
| `auth/tenant-isolation.spec.ts` | Asserts `acme` cannot read `techflow` data — needs both fixed slugs. |
| `auth/cross-tenant-writes.spec.ts` | Asserts mutation requests with mismatched JWT + header are 403'd. Needs `acme` + `techflow`. |
| `auth/signup.spec.ts` | Uses the no-tenant landing (`http://localhost:7777`). |

## Layout

```
tests-e2e/
├── playwright.config.ts             config; webServer boots `pnpm dev`; workers default 4
├── fixtures/
│   └── helpers.ts                   per-worker fixtures + signIn / tenantPsql / etc.
├── auth/                            login, signup, RBAC, tenant isolation
├── admin/                           user lifecycle, bulk-delete, custom roles
├── invoices/                        list, detail, edit, bulk recode, status transitions
├── payments/                        queue, runs, void/cancel, CFO approval, execute
├── workflows/                       lifecycle, step config, approval matrix, etc.
├── exceptions/                      queue, assign, filter, resolve
├── credit-memos/                    list, pagination
├── organization/                    settings, fraud rules, GL accounts sync
├── purchase-orders/                 list, sync
├── goods-receipts/                  list
├── vendors/                         verify
└── smoke/                           nav smoke + grouped sidebar nav / section tabs / bell + profile popover (section-nav, sidebar specs)
```

`testDir: '.'` walks recursively, so adding a new spec under any of
these folders picks up automatically.

## Local dev loop

Playwright auto-starts the frontend dev server via its `webServer`
block; the backend has to be running separately.

```bash
# terminal 1 — backend
pnpm db:up                    # Postgres + Redis (+ MinIO)
cd backend && python scripts/seed.py    # creates acme + techflow + e2e1..e2e4
pnpm dev:backend              # uvicorn on :8000

# terminal 2 — playwright
pnpm install:frontend         # one-time
cd frontend && pnpm exec playwright install chromium    # one-time browser install
pnpm test:frontend            # workers=4 by default
PLAYWRIGHT_WORKERS=1 pnpm test:frontend    # serial run, easier to debug a flake
```

The `pnpm db:up` / `dev:backend` / `test:frontend` invocations are
the root dispatch scripts; see the repo's root README for the rest.

## Service-backed specs (gated)

Some specs exercise a flow that needs an optional local container the
default `pnpm db:up` stack doesn't include. They live in their own
folders and **skip with an actionable message** when the service is
down (via `skipUnlessReachable` in `fixtures/services.ts`) — so the
normal suite stays green without them, and they run for real once the
container is up.

| Spec | Needs | Bring it up |
|---|---|---|
| `sso/login.spec.ts` | Keycloak + acme SSO seeded | `pnpm idp:up && pnpm idp:seed` |
| `email/signup-email.spec.ts` | Mailpit + backend on `AP_EMAIL_PROVIDER=smtp` | `pnpm mail:up`, restart backend with smtp |
| `scim/provisioning.spec.ts` | (none — CI-safe contract test) | always runs |

Backend-only service flows are pytest integration tests, also gated:
`backend/tests/test_localstack_integration.py` (LocalStack — `pnpm
aws:up`) and `test_stripe_mock_integration.py` (stripe-mock — `pnpm
stripe:up`). `test_ollama_integration.py` is local-only (model
inference is too heavy for CI).

These all run in CI's **`service-e2e`** job (see below), which starts
the containers and seeds SSO before running them.

## CI

`.github/workflows/ci.yml`'s `e2e` job runs the same flow on every
push/PR to main, **sharded across 14 parallel GitHub runners** via
Playwright's `--shard=N/14` flag. Each shard:

- pgvector/pgvector:pg16 + Redis 7 as services (per-shard, isolated)
- `AP_E2E_TENANT_COUNT=1` — each shard only needs one tenant
  (`e2e1`) since it runs `workers=1`. Skips provisioning the other
  three e2e tenants and shaves ~5 s off seed time per shard.
- Python 3.14 → install backend deps →
  `python scripts/seed.py --lean` (creates acme + techflow + e2e1
  in *this shard's* Postgres) → `python main.py &`
- Wait for `/api/health` to answer 200
- pnpm 9 + Node 20 →
  `pnpm install --frozen-lockfile` →
  `pnpm build` →
  `pnpm exec playwright install --with-deps chromium`
- `PLAYWRIGHT_WORKERS=1 pnpm exec playwright test --config=… --shard=${{ matrix.shard }}/14`
- Playwright report + backend log uploaded as
  `playwright-report-shard-${N}` / `backend-log-shard-${N}` on failure

Effective parallelism is **14 shards × 1 worker = 14** parallel test
processes spread across 14 separate runner VMs. `fail-fast: false`
keeps the other shards going when one fails, so a flake in shard 2
doesn't hide a real regression in shard 4.

Each shard runs ~20 specs serially (274 / 14). Wall-clock per shard
is dominated by the per-shard setup (~30 s) + the ~20 specs at
~2 s each = ~70 s total. Local `workers=4` runs the whole 274
specs concurrently in ~2 min.

The sharded `e2e` job has only Postgres + Redis, so the service-backed
specs above just skip there. A separate **`service-e2e`** job (single
runner) starts Keycloak + Mailpit + LocalStack + stripe-mock via
`docker compose` after checkout, seeds acme SSO, points the backend at
them, and runs `sso/ email/ scim/` plus the LocalStack + stripe-mock
pytest integration tests — so those flows actually run in CI. Ollama is
excluded (model inference too heavy for CI runners).

A dedicated **`.github/workflows/sso-e2e.yml`** workflow gives SSO its
own focused green/red signal: it boots **only Keycloak** (not the full
four-service bundle), seeds acme SSO, and runs just `sso/`. It triggers
on push/PR that touch the SSO surface (the backend SSO route + service,
the Keycloak realm + seed script, the login routes, the `sso/` specs,
its `fixtures/`) and on manual `workflow_dispatch` — no schedule. Use it
for fast SSO feedback without waiting on the 25-minute `service-e2e`
bundle; `service-e2e` remains the comprehensive multi-service gate.

## Seeded credentials

Defined in `backend/scripts/seed.py`. Spec files almost always want
the per-worker fixtures (`tenantAdmin` / `tenantManager` /
`tenantClerk` / `tenantCfo`), not the static `acme` / `techflow`
constants — the static set is for the cross-tenant specs only.

| Tenant | Role | Email | Password |
| ------ | ---- | ----- | -------- |
| acme | admin | `demo@acme.com` | `demo` |
| acme | ap_manager | `demo+apmanager@acme.com` | `demo` |
| acme | ap_clerk | `demo+apclerk@acme.com` | `demo` |
| acme | cfo | `demo+cfo@acme.com` | `demo` |
| techflow | admin | `admin@techflow.com` | `demo` |
| `e2e<N>` | admin | `demo+admin@e2e<N>.localhost` | `demo` |
| `e2e<N>` | ap_manager | `demo+manager@e2e<N>.localhost` | `demo` |
| `e2e<N>` | ap_clerk | `demo+clerk@e2e<N>.localhost` | `demo` |
| `e2e<N>` | cfo | `demo+cfo@e2e<N>.localhost` | `demo` |

## Fixture helpers (`fixtures/helpers.ts`)

Reach for these instead of duplicating boilerplate per spec:

- `test` / `expect` — re-export of the extended Playwright `test`.
  **Always import from `'../fixtures/helpers'`, not `@playwright/test`**,
  so the worker-scoped fixtures (`tenantSlug`, `tenantAdmin`, …)
  resolve correctly.
- Worker fixtures: `tenantSlug`, `tenantAdmin`, `tenantManager`,
  `tenantClerk`, `tenantCfo` — destructure in the test signature.
- `signIn(page, creds?)` / `signInAndWait(page, creds?)` — default
  to the worker's admin when called without `creds`.
- `currentTenantSlug()` — read the worker's slug from outside a
  test arg (rarely needed; prefer destructuring the fixture).
- `authToken(page)` — pull the JWT from `localStorage`.
- `tenantHeaders(token, slug?)` + `authedTenantHeaders(page, slug?)`
  — compose the `Authorization` + `X-Tenant-Slug` headers for an
  authenticated, tenant-scoped API request. Default to the
  worker's slug.
- `tenantPsql(query, slug?)` — `psql -d ap_<slug>` for the half
  dozen specs that need to clobber DB state the API doesn't expose
  (hard-delete an approved invoice, force-fail a settled payment,
  etc.). Defaults to the worker's DB.
- `API_BASE`, `ACME_BASE`, `TECHFLOW_BASE`, `NO_TENANT_BASE` — the
  same origins everyone was redeclaring inline.

## Subdomain trick

The frontend resolves the tenant from the subdomain
(`<slug>.localhost:7777` → `<slug>`). Chromium auto-resolves
`*.localhost` to 127.0.0.1 per RFC 6761, so no `/etc/hosts` edits
needed. To exercise the no-tenant marketing landing, override
`baseURL` to `http://localhost:7777` via
`test.use({ baseURL: 'http://localhost:7777' })` in the spec (or
import `NO_TENANT_BASE` and use that).

## Storage-state (future)

The auth-storage-state pattern (sign each user in once during
globalSetup, persist to `.auth/<user>.json`, then
`test.use({ storageState })` per spec) is the standard Playwright
pattern for fast authenticated tests. It's not wired here yet —
login is fast enough that the per-spec inline `signInAndWait` is
fine. Add the globalSetup back when login latency becomes the
bottleneck.
