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

`POST /api/auth/login` — accepts email and password, verifies against the hashed password in the **control-plane database**, and returns a signed JWT. This endpoint uses `get_control_db` (not the tenant DB).

### Logout endpoint

`POST /api/auth/logout` — revokes the current token by adding its `jti` (unique token ID) to a Redis blocklist. The blocklist entry expires automatically when the token would have expired, so Redis doesn't accumulate stale entries.

### Current user endpoint

`GET /api/auth/me` — returns the authenticated user's profile including their roles (e.g. `["admin", "ap_manager"]`). Used by the frontend layout to validate the session on page load and determine role-based UI visibility.

`PATCH /api/auth/me` — self-service endpoint for updating name and password (requires current password).

### Protected routes

All endpoints except `/api/auth/login` and `/api/health` require a valid Bearer token. Authentication is enforced via FastAPI dependencies in `app/api/deps.py`.

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

### Database separation

- **Auth routes** (`/api/auth/*`) read from the control-plane DB (`account_payables`) — this is where `users`, `organizations`, and `roles` tables live
- **Business routes** (`/api/invoices`, `/api/vendors`, `/api/dashboard`) read from the tenant DB (`ap_<slug>`) — resolved via the `X-Tenant-Slug` header

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
| `AP_REDIS_URL`  | `redis://localhost:6379`   | Redis URL for blocklist   |

Set `AP_SECRET_KEY` to a strong, random value in production.

## RBAC (Role-Based Access Control)

The database supports four roles:
- **admin** — full access to all features, user management, workflow configuration
- **ap_manager** — review and approve invoices, manage vendors and payments
- **ap_clerk** — upload and edit invoices, submit for review (cannot approve, delete, or change status)
- **cfo** — approve high-value invoices, view reports, manage vendors and payments

Roles are returned by `GET /api/auth/me` in the `roles` array. The frontend uses these to control sidebar navigation visibility, button visibility, and action availability (see [user-management.md](user-management.md) for the full matrix).

Backend API-level role enforcement (`Depends(require_role("admin"))`) is planned for a future phase — currently enforcement is UI-only.

## Testing Auth via curl

```bash
# Login and capture token
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
- **MFA enforcement / SSO-only mode** — trivially layered on top later (flag on Organization.settings forcing `hashed_password=NULL` path).

---

## SCIM 2.0 (user provisioning from Okta + Entra)

Automated user lifecycle — IdP pushes create/update/deactivate events to our SCIM endpoints so admins don't hand-manage users.

### Per-tenant bearer auth

Each tenant has its own SCIM bearer token. When an admin generates one (via the Organization settings UI):

1. Backend mints a 43-char URL-safe token.
2. SHA-256 hashes it.
3. Stores the **hex digest** in `Organization.settings.sso.scim_bearer_hash`.
4. Returns the plaintext token **once** to the admin.
5. Admin pastes it into Okta/Entra's SCIM config alongside the SCIM URL.

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
