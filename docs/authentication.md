# Authentication

JWT-based authentication using `python-jose` for token handling and `passlib` with bcrypt for password hashing. Tokens are server-side revocable via a Redis blocklist.

## Auth Flow

1. User visits a tenant subdomain (e.g., `acme.localhost:7777`)
2. Frontend extracts the tenant slug from the subdomain
3. User submits email/password at `/login`
4. Frontend POSTs `{ email, password }` to `/api/auth/login`
5. Backend validates credentials against the **control-plane DB** (where users live)
6. Backend returns `{ access_token, token_type }` (JWT with a unique `jti` claim)
7. Frontend stores the JWT in `localStorage`
8. All subsequent requests include both:
   - `Authorization: Bearer <token>` header
   - `X-Tenant-Slug: <slug>` header
9. On 401 responses, the token is cleared and the user is redirected to `/login`

## Backend Implementation

### Login endpoint

`POST /api/auth/login` — accepts email and password, verifies against the hashed password in the **control-plane database**. Returns either a signed JWT (`{access_token, ...}`) or, when MFA is in play, a short-lived challenge token (`{mfa_required: true, mfa_challenge_token, methods, must_enroll}`). See the **MFA** section below for the full flow. This endpoint uses `get_control_db` (not the tenant DB).

### Logout endpoint

`POST /api/auth/logout` — revokes the current token by adding its `jti` (unique token ID) to a Redis blocklist. The blocklist entry expires automatically when the token would have expired, so Redis doesn't accumulate stale entries.

### Current user endpoint

`GET /api/auth/me` — returns the authenticated user's profile including their roles (e.g. `["admin", "ap_manager"]`). Used by the frontend layout to validate the session on page load and determine role-based UI visibility.

`PATCH /api/auth/me` — self-service endpoint for updating name and password (requires current password).

### Protected routes

All endpoints except the explicit allowlist (login flow, OIDC handshake, signup, webhooks, SCIM, health) require a valid Bearer token AND the right role. Authentication and role checks are enforced via FastAPI dependencies in `app/api/deps.py` (`get_current_user`, `require_roles`). See **Role-based access control** below for the full permission matrix.

## Token Revocation (Blocklist)

Stateless JWTs can't be invalidated server-side by default — once issued, they're valid until they expire. To support secure logout, the app uses a Redis-backed token blocklist.

### How it works

1. Every JWT includes a `jti` (JWT ID) — a unique UUID generated at token creation
2. On logout, `POST /api/auth/logout` adds the `jti` to Redis with a TTL equal to the token's remaining lifetime
3. On every authenticated request, `get_current_user` checks Redis for the `jti` — if found, the request is rejected with 401
4. Redis entries auto-expire when the token would have expired, so no cleanup is needed

### Why Redis

- Fast O(1) lookups on every request (sub-millisecond)
- TTL-based auto-expiry keeps the blocklist small
- Already running in the stack for cache/queue duties

### Data stored in Redis

```
Key:    token:blocked:<jti>
Value:  "1"
TTL:    remaining seconds until token expiry
```

### Failure mode

If Redis is unavailable, the blocklist check is skipped — tokens behave as standard stateless JWTs. This is a deliberate choice: a Redis outage should not lock out all users. The trade-off is that tokens cannot be revoked during the outage.

## Session management (concurrent cap + forced logout)

Every successful login, MFA verify, and SSO callback **also** registers the newly-minted JTI in a per-user Redis sorted set (`active_jtis:<user_id>`, scored by issue time). This gives the app two SOC 2-relevant controls on top of the blocklist:

### Concurrent session limit

Configured by `AP_MAX_CONCURRENT_SESSIONS` (default `5`; set to `0` to disable). On each login, `track_session` adds the JTI and — if the user is over the cap — evicts the oldest entries and adds them to the blocklist. The evicted sessions stop authenticating on their next request.

### Forced logout on role change

`PATCH /api/admin/users/{id}` snapshots the target's role set before applying changes. If roles change or `is_active` flips from true to false, the admin path calls `revoke_user_sessions(user_id)`, which blocklists every tracked JTI and clears the set. `DELETE /api/admin/users/{id}` does the same on deletion so a tombstoned user can't keep calling the API.

### Session set TTL

The sorted set carries a TTL equal to the access-token lifetime, refreshed on every login. Inactive users' sets age out naturally — Redis never accumulates stale entries.

### Logout

`POST /api/auth/logout` now both blocklists the current JTI **and** drops it from the tracking set (via `end_session`). A user who logs out gains a free slot under the concurrent-session cap for their next login.

## Auth event audit logging

Every auth action writes a row to the tenant `audit_log` table via `app/services/audit_dispatch.py::dispatch_auth_audit`. Because auth endpoints run on the control-plane DB (where users live) but the audit log is tenant-scoped, the helper resolves the tenant DB from the user's `organization_id` and opens a short-lived session to write the row. Failures are caught + logged at WARN level — auth itself never errors because the audit write couldn't complete.

Action names:

| Event | Action |
|---|---|
| Successful password login | `auth.login.success` |
| Failed password login (bad password / no password) | `auth.login.failure` |
| Logout | `auth.logout` |
| MFA challenge issued during login | `auth.mfa.challenge_issued` |
| Successful MFA verify | `auth.mfa.verify.success` |
| Failed MFA verify | `auth.mfa.verify.failure` |
| Successful SSO login | `auth.sso.login.success` |
| Failed SSO login (code exchange / ID token / domain blocked) | `auth.sso.login.failure` |

Login-failure rows for unknown emails are dropped — without an `organization_id` there is no tenant DB to route to. Failures for known users carry the email, IP (when the client is reachable), and a machine-readable `reason`.

### Database separation

- **Auth routes** (`/api/auth/*`) read from the control-plane DB (`account_payables`) — this is where `users`, `organizations`, and `roles` tables live
- **Business routes** (`/api/invoices`, `/api/vendors`, `/api/dashboard`) read from the tenant DB (`ap_<slug>`) — resolved via the `X-Tenant-Slug` header

### Cross-tenant guard

`app/tenant.py::get_tenant` is the single chokepoint every business
route flows through. It refuses to resolve a tenant if the caller's
JWT identifies a different organization than the slug — the header
alone is **not** trusted to decide which tenant's data is read.

For employee tokens (`typ` is anything other than `"vendor"`), the
guard requires `payload["org"] == organization.id`. Mismatch → 403.
Vendor-portal tokens (`typ="vendor"`) are exempt because `VendorUser`
rows live in the per-tenant DB and a cross-tenant attempt fails
naturally on the user-lookup query in
`app/api/portal_deps.py::get_current_vendor_user`. Unauthenticated
requests pass through to the downstream auth dep (which 401s).

Without this guard, an authenticated user from tenant A could read
or mutate tenant B's data simply by sending
`X-Tenant-Slug: <other-tenant>` on the request. The guard is pinned
by `backend/tests/test_tenant_isolation.py` (unit) and
`frontend/tests-e2e/auth/tenant-isolation.spec.ts` (e2e).

## Frontend Implementation

- Auth state is managed in `src/lib/stores/auth.svelte.ts` (Svelte 5 runes)
- Tenant slug is extracted by `src/lib/tenant.ts` from the subdomain
- API client (`src/lib/api.ts`) automatically attaches both the Bearer token and the `X-Tenant-Slug` header to every request
- The root `+layout.svelte` guards all routes — unauthenticated users are redirected to `/login`
- If no tenant subdomain is detected, a "no tenant" page is shown instead
- The login page has its own layout without the sidebar

## Configuration

| Variable        | Default                    | Description               |
|-----------------|----------------------------|---------------------------|
| `AP_SECRET_KEY` | `change-me-in-production`  | JWT signing key           |
| `AP_ACCESS_TOKEN_EXPIRE_MINUTES` | `30`      | Token lifetime in minutes |
| `AP_REDIS_URL`  | `redis://localhost:6379`   | Redis URL for blocklist + active-session tracking |
| `AP_MAX_CONCURRENT_SESSIONS` | `5`        | Concurrent sessions per user. `0` disables the cap. |

Set `AP_SECRET_KEY` to a strong, random value in production.

## RBAC (Role-Based Access Control)

The database supports four roles:
- **admin** — full access to all features, user management, workflow configuration
- **ap_manager** — review and approve invoices, manage vendors and payments
- **ap_clerk** — upload and edit invoices, submit for review (cannot approve, delete, or change status)
- **cfo** — approve high-value invoices, view reports, manage vendors and payments

Roles are returned by `GET /api/auth/me` in the `roles` array. The frontend uses these to control sidebar navigation visibility, button visibility, and action availability (see [user-management.md](user-management.md) for the full matrix).

Backend API-level role enforcement is in place via `Depends(require_roles(...))` on every protected endpoint. The full permission matrix is in the **RBAC** section below. Frontend gates exist for UX (hiding buttons, sidebar items) but are not the security boundary — the backend is.

## Testing Auth via curl

```bash
# Login and capture token. NOTE: when AP_MFA_ENABLED=true and the user has MFA
# enrolled (or the org enforces it), the response is an MFAChallengeResponse
# (no `access_token` field) and you'll need to complete /api/auth/mfa/verify
# next. The snippet below assumes MFA is off (the local-dev default).
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@acme.com","password":"demo"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Use the token (include tenant slug header)
curl http://localhost:8000/api/invoices \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Slug: acme"

# Get current user
curl http://localhost:8000/api/auth/me \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Slug: acme"

# Logout (revoke the token)
curl -s -X POST http://localhost:8000/api/auth/logout \
  -H "Authorization: Bearer $TOKEN"

# Verify the token is revoked (should return 401)
curl http://localhost:8000/api/auth/me \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Slug: acme"
```

---

## SSO (OIDC via Okta + Microsoft Entra)

A single OIDC flow supports both Okta and Entra because they're both OIDC-compliant — the only per-tenant variation is the discovery URL. Config lives on `Organization.settings.sso`:

```json
{
  "sso": {
    "enabled": true,
    "provider": "okta",
    "discovery_url": "https://acme.okta.com/.well-known/openid-configuration",
    "client_id": "...",
    "client_secret": "...",
    "scim_bearer_hash": "<sha256 hex>",
    "allowed_email_domains": ["acme.com"]
  }
}
```

### Flow

1. User clicks **Sign in with Okta/Microsoft** on `/login` (button renders only when `GET /api/auth/sso/config?slug=<tenant>` returns `enabled: true`).
2. Browser navigates to `GET /api/auth/sso/authorize?slug=<tenant>` on the backend.
3. Backend fetches the discovery doc (cached in Redis for 24h), mints a one-shot `state` + `nonce` into Redis (keyed to the tenant slug), and **302-redirects** to the IdP's `authorization_endpoint` with `redirect_uri` pointing to the *tenant's own subdomain* (built from `AP_TENANT_URL_TEMPLATE`).
4. User authenticates on the IdP.
5. IdP redirects back to `<tenant>.app.com/login/sso-callback?code=...&state=...`.
6. Frontend callback page POSTs `{code, state}` to `/api/auth/sso/callback`.
7. Backend:
   - Consumes the state (single-use) and extracts the bound tenant + nonce.
   - Exchanges the code for tokens via the IdP's `token_endpoint`.
   - Validates the ID token against the IdP's JWKS (iss, aud, exp, nonce).
   - Optionally enforces `allowed_email_domains`.
   - JIT-provisions the user (match order: `(sso_provider, sso_provider_id)` → `(organization_id, email)` → new user with least-privilege `ap_clerk` role, or `admin` if it's the first user in the org).
   - Mints our own JWT and returns `{access_token, must_change_password, tenant_slug}`.
8. Frontend stores the JWT, fetches `/api/auth/me`, and routes to `/change-password` if flagged or `/` otherwise.

### Why callback URLs are per-tenant

Each customer registers their own Okta/Entra app with `redirect_uri = https://<theirtenant>.app.com/login/sso-callback`. That way the IdP redirects directly to the tenant origin and our localStorage JWT works without cross-origin hops. In dev, `AP_TENANT_URL_TEMPLATE=http://{slug}.localhost:7777` gives each tenant their own callback URL for free.

### JIT user provisioning

First SSO login provisions the user. Three match paths:

| Match on | When it fires |
|---|---|
| `(sso_provider, sso_provider_id)` | Durable — survives email changes |
| `(organization_id, email)` | First SSO login for an existing password user — links their row and sets `sso_provider_id` |
| Create new | No match → new row with `hashed_password=NULL` (SSO-only), `must_change_password=false` |

New users get `ap_clerk` role by default (least privilege). The first user ever in an org gets `admin`.

### OIDC endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/auth/sso/config?slug=<s>` | Unauthenticated. Returns `{enabled, provider}`. Never leaks secrets. |
| `GET` | `/api/auth/sso/authorize?slug=<s>` | 302 to IdP. |
| `POST` | `/api/auth/sso/callback` | `{code, state}` → `{access_token, must_change_password, tenant_slug}`. |

### Not in this pass

- **SAML 2.0** — tracked in Priority 7 roadmap. OIDC covers ~90% of "SSO" asks; SAML remains a separate code path for regulated industries that require it.

---

## Multi-factor authentication (MFA)

TOTP-based two-factor with optional email backup. Per-user opt-in by default; admins can flip a tenant-wide enforcement switch under **Organization → Security**.

### Master switch

`AP_MFA_ENABLED` (default `false`) is the platform-level gate. When false, all MFA endpoints, login challenges, and enforcement are skipped — useful for local dev where you don't want to scan a QR code every time. Flip to `true` in any deployed environment.

### Factors

| Factor | Identifier | Notes |
|---|---|---|
| **TOTP** | `totp` | RFC 6238, 30-second window, ±1 step skew tolerance. `pyotp` under the hood. Compatible with Google Authenticator, 1Password, Authy, Microsoft Authenticator. |
| **Email OTP** | `email` | 6-digit code emailed via the configured `AP_EMAIL_PROVIDER`. Lives in Redis with a `AP_MFA_EMAIL_OTP_TTL_SECONDS` TTL (default 6 minutes). Only the SHA-256 of the code is stored — Redis dumps don't reveal codes. Single-use. |

Email is offered as a backup so a lost phone doesn't lock the account out. It's also the only available factor for users under org-enforcement who haven't enrolled TOTP yet (verifying email proves inbox ownership before they're allowed to enroll).

### Login flow

```
POST /api/auth/login {email, password}
  └─ MFA off OR user not enrolled AND org doesn't require it
        → 200 {access_token, token_type, must_change_password}
  └─ MFA required
        → 200 {mfa_required: true, mfa_challenge_token, methods, must_enroll}

  (browser stashes mfa_challenge_token in sessionStorage, navigates to /login/mfa)

POST /api/auth/mfa/challenge/email {challenge_token}    # only if user picks email
  └─ 204 No Content (issues + emails a 6-digit code)

POST /api/auth/mfa/verify {challenge_token, code, method}
  └─ 200 {access_token, token_type, must_change_password}
```

The challenge token is itself a short-lived JWT (`AP_MFA_CHALLENGE_TTL_SECONDS`, default 5 minutes) with `typ: mfa_challenge`. That keeps the flow stateless — no DB row to garbage-collect, no Redis lookup on every check. A regular access token won't satisfy the challenge endpoint and vice versa.

### Per-user enrollment

```
POST /api/auth/mfa/enroll                # mints secret + provisioning URI + QR (data URL)
POST /api/auth/mfa/enroll/verify {code}  # confirms scan worked, flips mfa_enabled true
POST /api/auth/mfa/disable    {password} # turns it off (re-confirms password first)
```

The QR code is returned inline as a `data:image/png;base64,...` URL so the frontend doesn't need a separate authed image endpoint. The plaintext base32 secret is also returned so users without a camera-equipped scanner can paste it manually.

Disable requires password re-entry — a stolen session shouldn't be able to silently strip MFA off. If the org enforces MFA, disable is blocked outright.

### Org enforcement

`Organization.settings.mfa.required: bool` — toggled from **Organization → Security** (admin only). When true:

- Every user without `mfa_enabled` is gated. They can complete one email-OTP login but are routed straight to `/profile` to enroll TOTP.
- Per-user disable is blocked — if you don't want enforcement, turn off the org setting first.
- New users (including SCIM-provisioned ones) follow the same gate.

### What stays where

| Data | Lives in | Why |
|---|---|---|
| `User.mfa_secret` (base32) | control-plane DB | Per-user, durable, written once at enrollment. |
| `User.mfa_enabled`, `mfa_enrolled_at` | control-plane DB | Drives login-flow decisions. |
| `Organization.settings.mfa.required` | control-plane DB (JSONB) | Org-wide policy; lives next to other settings. |
| Email-OTP hash | Redis (`mfa:email_otp:<user_id>`) | Short-lived, single-use, no need to persist. |
| MFA challenge token | client only (sessionStorage) | Stateless JWT — server doesn't need to remember it. |

### SSO + MFA

OIDC SSO sign-in does **not** trigger our MFA challenge — the IdP is the source of truth for "did this person prove a second factor." Configure MFA in Okta/Entra itself if you want it enforced for SSO users.

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/auth/login` | Returns either a token or an MFA challenge. |
| `POST` | `/api/auth/mfa/challenge/email` | Sends a 6-digit code to the user's email. |
| `POST` | `/api/auth/mfa/verify` | Trades a challenge token + code for an access token. |
| `POST` | `/api/auth/mfa/enroll` | Starts TOTP enrollment — returns secret + QR. |
| `POST` | `/api/auth/mfa/enroll/verify` | Confirms enrollment with a valid code. |
| `POST` | `/api/auth/mfa/disable` | Turns MFA off (requires password). |

### Not in this pass

- **WebAuthn / passkeys** — tracked in roadmap. TOTP covers the bulk of "we need MFA" asks; WebAuthn is a separate, more involved code path.
- **Backup codes (static)** — email-OTP fills the same recovery role and doesn't require the user to safely store anything.
- **Mobile MFA** — the Flutter app currently expects `TokenResponse` from `/login` and doesn't handle `MFAChallengeResponse`. Mobile users can sign in with `AP_MFA_ENABLED=false`. Mobile MFA is on the roadmap.

---

## Delegation / Out-of-Office

Any user can set a delegate who receives their approval assignments while they are away.

- **Set a delegate:** `POST /api/auth/delegation` with `{delegate_to_id, until}` — `until` is an ISO datetime after which delegation automatically expires.
- **Check status:** `GET /api/auth/delegation` — returns the current delegation (delegate user, expiry) or empty if none is active.
- **Clear:** `DELETE /api/auth/delegation` — removes the delegation immediately.

When a reviewer is OOO (has an active, non-expired delegation), approval assignments auto-route to their delegate. The audit trail records both the original assignee (`WorkflowStep.original_assigned_to`) and the delegate who actually performed the action.

---

## SCIM 2.0 (user provisioning from Okta + Entra)

Automated user lifecycle — IdP pushes create/update/deactivate events to our SCIM endpoints so admins don't hand-manage users.

### Per-tenant bearer auth

Each tenant has its own SCIM bearer token. To generate one, the admin POSTs to `/api/organization/sso/scim-token` (no body). The backend:

1. Mints a 43-char URL-safe token.
2. SHA-256 hashes it.
3. Stores the **hex digest** in `Organization.settings.sso.scim_bearer_hash`.
4. Returns `{token, bearer_hash_prefix}` — the plaintext token is shown **once** and never re-served. The 8-char prefix is a UI identifier so admins can tell which token is currently active.
5. Admin pastes the plaintext token into Okta/Entra's SCIM config alongside the SCIM URL.

Re-calling the endpoint rotates the token: the old hash is overwritten, so any IdP still using the previous token starts seeing 401s.

Every SCIM request Authorization-headers a bearer token; the backend SHA-256s it and looks for a matching `scim_bearer_hash` across all orgs to resolve the tenant. Linear scan, acceptable while tenant count is <<1000.

### Endpoints (all under `/api/scim/v2`)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/ServiceProviderConfig` | Discovery doc — Okta/Entra probe this first. |
| `GET` | `/Schemas/{id}` | Minimal User schema — Entra probes this before syncing. |
| `GET` | `/Users` | List + filter + paginate. Supports `userName eq`, `emails eq`, `externalId eq`, `active eq`. |
| `GET` | `/Users/{id}` | Fetch one user. |
| `POST` | `/Users` | Create. Returns 409 `uniqueness` on duplicate userName. |
| `PATCH` | `/Users/{id}` | Partial update. Supports the ops Okta + Entra send (active toggle, userName/externalId/name replace, root-object replace). |
| `DELETE` | `/Users/{id}` | **Soft delete** — sets `is_active=false`. Preserves audit trail. |

### Filter syntax

Only the filter subset Okta + Entra actually use:

```
userName eq "alice@acme.com"
emails eq "alice@acme.com"
externalId eq "okta-user-id"
active eq true
```

Anything else returns a `400 invalidFilter` with a clear message so the IdP surfaces a useful error.

### Not in this pass

- **`/Groups` endpoints** — group sync requires mapping IdP groups to our `Role` rows. Design work pending (how is "admin group members get admin role" expressed? Per-tenant config or convention?). Tracked in the roadmap.

---

## Role-based access control (RBAC)

Every authenticated endpoint declares the roles it accepts via a single dependency, `require_roles(*roles)` in `app/api/deps.py`. The check is **any-of**: a user passes if they hold at least one of the listed roles. This mirrors the frontend's `hasAnyRole()` gate so backend and UI permissions can't drift apart.

```python
from app.api.deps import ROLE_ADMIN, ROLE_AP_MANAGER, require_roles

@router.post("/invoices/{invoice_id}/approve")
async def approve_invoice(
    invoice_id: uuid.UUID,
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
):
    ...
```

Failed checks return `403 Forbidden` with `{"detail": "Your role does not permit this action."}` and emit a `WARNING`-level log (`RBAC denied: user=… roles=… required_any=… METHOD PATH`) so monitoring can pick up brute-force probing.

### Permission matrix

The matrix below is the source of truth — it mirrors `frontend/src/lib/components/Sidebar.svelte` (page visibility) and the `!isClerkOnly` / `isManager` / `isCfo` checks in invoice + workflow components. Roles are non-exclusive: a user may hold any combination.

| Endpoint area | Read | Write |
|---|---|---|
| `/admin/*` (user CRUD, role list) | admin | admin |
| `/organization` settings + tests + SCIM token | admin | admin |
| `/workflows` (definition CUD) | any-authenticated | admin |
| `/exceptions` (list + resolve) | admin · ap_manager | admin · ap_manager |
| `/vendors/{id}/verify`, `/reject`, `/sync-erp` | — | admin · ap_manager |
| `/vendors` create / patch / delete | — | admin · ap_manager |
| `/gl-accounts` create / sync-erp | any-authenticated (read) | admin · ap_manager |
| `/purchase-orders` sync-erp | any-authenticated (read) | admin · ap_manager |
| `/invoices/{id}/assign` (route to a reviewer) | — | admin · ap_manager |
| `/invoices` mutate (create / patch / delete / line-items / bulk) | any-authenticated (read) | admin · ap_manager · cfo |
| `/invoices/{id}/upload`, `/extract`, `/reset-extraction` | — | admin · ap_manager · cfo |
| `/invoices/{id}/approve`, `/reject`, `/resubmit`, `/complete`, `/send-to-erp`, `/retry-erp` | — | admin · ap_manager · cfo |
| `/payments/*` (incl. runs create + execute) | admin · ap_manager · cfo | admin · ap_manager · cfo |
| `/cards` (list / dashboard / generate / cancel / details / rebates) | admin · ap_manager · cfo | admin · ap_manager · cfo |
| `/dashboard` | any-authenticated | — |
| `/auth/me`, `/auth/mfa/*` (per-user MFA mgmt), `/auth/change-password` | any-authenticated | any-authenticated |

### "Read open to all authenticated" surfaces

Invoices, workflow definitions list/active-steps, GL accounts list, and POs list are readable by every authenticated user (including pure clerks). Clerks can see the work; they just can't take action on it. This matches the frontend, where the invoice list page is visible to clerks but write controls are hidden.

### Endpoints that intentionally do **not** require a JWT

These are explicitly listed in `tests/test_rbac.py::NO_AUTH_REQUIRED` and use other auth mechanisms or are public:

- `POST /auth/login`, `POST /auth/mfa/challenge/email`, `POST /auth/mfa/verify` — pre-login flow, gated by password + challenge token.
- `POST /auth/logout` — uses Bearer header but reads it directly, not via `Depends`.
- `GET /auth/sso/*` and `POST /auth/sso/callback` — OIDC handshake.
- `POST /signup/*`, `GET /signup/slug-check` — public signup.
- `POST /cards/webhook/{provider}`, `POST /erp/webhook/{erp_type}` — provider-signed webhooks.
- `GET /scim/v2/*`, `POST /scim/v2/Users`, etc. — per-tenant SCIM bearer token validated inside the handler.

### Coverage gate

`tests/test_rbac.py::test_every_endpoint_requires_auth_or_is_explicitly_public` walks every router at import time. If a new endpoint is added without an auth dependency and is not on the `NO_AUTH_REQUIRED` allowlist, the test fails. This is the *one* test that has to fail noisily in CI when someone forgets RBAC — the kind of mistake that put us in the "any authenticated user can hit any endpoint" hole that this work fixed.

A companion test catches the inverse: if `NO_AUTH_REQUIRED` references an endpoint that no longer exists, it fails so the allowlist can't silently rot.

### Adding a new endpoint

1. Pick the right role tuple from the matrix above (or invent one and update this doc).
2. Add `user: User = Depends(require_roles(ROLE_X, ...))` to the endpoint signature.
3. If the endpoint genuinely cannot take a JWT (webhook, login flow), add it to `NO_AUTH_REQUIRED` in `tests/test_rbac.py` with a one-line comment explaining why.
4. Run `pytest tests/test_rbac.py` — the coverage gate will tell you if anything's missing.

### Not in this pass

- **Segregation of duties (SoD)** — users currently can approve invoices they themselves created. The classic AP SoD invariant ("approver != creator") is a sensible follow-up but not part of basic RBAC. Tracked in the roadmap.
- **Per-org custom roles** — the four roles (admin, ap_manager, ap_clerk, cfo) are hard-coded. Custom-role configuration would need a tenant-scoped permission table and policy engine.
- **Audit log of denied requests** — denials are logged via Python `logging.warning` for now, not persisted to the `audit_log` table. If oncall wants to query historical denials, surface them via centralized log shipping (planned under SOC 2 readiness).
