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
| Failed step-up against a second-factor change (enroll / passkey register / passkey delete / disable) | `auth.mfa.step_up.failure` (employee) · `portal.mfa.step_up.failure` (supplier portal) — PII-free, records only the operation name |
| Successful SSO login | `auth.sso.login.success` |
| Failed SSO login (code exchange / ID token / domain blocked) | `auth.sso.login.failure` |
| Successful SAML login | `auth.saml.login.success` |
| Failed SAML login (assertion invalid / issuer / unsolicited / replay / domain blocked) | `auth.saml.login.failure` |

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

## SAML SSO

SAML 2.0 is an **additive, separate** SSO code path (`app/api/auth_saml.py`)
alongside OIDC. Both protocols share the same per-tenant `Organization.settings.sso`
block, discriminated by a `protocol` key (absent / `"oidc"` → OIDC; `"saml"` →
SAML), and the same identity tail: JIT provisioning + JWT mint + session
registration (`app/services/identity_provisioning.py`). Only *verification*
differs. Local testing: [`local-sso-saml.md`](local-sso-saml.md).

**SP-initiated flow.** `GET /auth/saml/login?slug=` builds the AuthnRequest and
302s to the IdP, binding a single-use Redis **RelayState** to `{tenant,
AuthnRequest-ID}`. The IdP HTTP-POSTs the signed `SAMLResponse` to
`POST /auth/saml/acs`; the handler recovers the tenant from the RelayState
(**never** the assertion or a header), verifies the response, JIT-provisions, and
303-redirects to the SPA bridge `/login/saml-callback?code=<once>`. The bridge
`POST`s the one-time code to `/auth/saml/exchange`, which returns the JWT **in the
body** — the token never transits a URL.

**Verification (the load-bearing control).** Delegated to `python3-saml` but
pinned to an explicit hardened posture (the library defaults are unsafe):

- Trust anchor = the tenant's pre-registered `idp_x509_cert` (`x509certMulti` for
  rotation) — never a fingerprint, never a cert embedded in the document. An
  empty/blank/malformed cert fails closed at config resolution.
- `wantAssertionsSigned` + `rejectDeprecatedAlgorithm` (rejects SHA-1/none),
  SHA-256 sig/digest — the SAML analog of the OIDC `ID_TOKEN_ALGORITHMS` pin.
- Issuer pinned to the configured IdP; Audience (= per-tenant `sp_entity_id`) and
  Destination validated from trusted server config, not spoofable `Host` /
  `X-Forwarded-*`.
- InResponseTo **presence** is enforced in the handler (the library only checks it
  when present, so an unsolicited response would otherwise pass).
- Replay dedup of both Assertion + Response IDs, scoped per-tenant
  (`saml:<slug>`), via the shared webhook `SET NX` ledger.
- All IdP XML parsed with python3-saml's DTD/entity-hardened parser (no raw lxml
  → no XXE).

Every rejection fails closed to one generic error + a PII-safe
`auth.saml.login.failure` audit row (reason code only — the email is **omitted**,
tighter than the OIDC `domain_blocked` precedent). The IdP owns MFA, so the app's
MFA challenge is skipped (same as OIDC).

**Provider-discriminated matching.** JIT match is `(sso_provider,
sso_provider_id)` then `(org, email)`. SAML uses `provider="saml"`,
`sso_provider_id=NameID`. A tenant migrating a user from OIDC to SAML won't match
on the durable key the first time — it links by email and rebinds the provider
deterministically. Intentional; no migration needed (config is additive JSONB).

**Config (`settings.sso`, `protocol="saml"`):** `idp_entity_id`, `idp_sso_url`,
`idp_x509_cert` (required), `sp_entity_id` (defaults per-tenant), optional
`idp_x509_cert_multi`, `allowed_email_domains`. The optional SP signing keypair
(only when the IdP requires signed AuthnRequests) is a real secret → `AP_SAML_SP_*`
via sops; empty by default so local Keycloak runs with no SP keypair.

## SSO-only mode

`settings.sso.sso_only` (a per-tenant flag, shared by OIDC + SAML) closes
password login for the whole org. The `/api/auth/login` handler refuses with
`403` + an `auth.login.failure` / `reason=sso_only` audit row **after** verifying
the password (so it reuses the org load and doesn't perturb the unknown-vs-
wrong-password enumeration parity). `services/sso.py::is_sso_only` gates on
`sso.enabled` too, so setting the flag without a working IdP can't lock everyone
out. The public `/auth/{sso,saml}/config` endpoints echo `sso_only` **only when
the IdP config resolves** — so the login page hides the password form for an
SSO-only tenant, but a broken config (enabled=False) leaves password login
visible as the escape hatch. Backend enforcement is the security boundary; the
hidden form is UX.

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

Roles are returned by `GET /api/auth/me` in the `roles` array, and the user's
effective **granular permissions** in the `permissions` array. The frontend uses
roles to control sidebar navigation visibility and most button/action
availability, and `auth.can(perm)` for the *split* sensitive controls (see
[user-management.md](user-management.md) for the full matrix and § Granular
permissions / segregation of duties below).

Backend API-level enforcement is via `Depends(require_roles(...))` on most
protected endpoints and `Depends(require_permission(...))` on the
fraud-sensitive, SoD-splittable subset (see below). The full permission matrix
is in the **RBAC** section below. Frontend gates exist for UX (hiding buttons,
sidebar items) but are not the security boundary — the backend is.

### Granular permissions / segregation of duties

The four system roles bundle whole sets of duties, which conflates
fraud-sensitive ones — e.g. one `ap_manager` could both **approve a vendor
bank-detail change** and **execute a payment run** (the person who can redirect
where money goes can also send it). A granular **permission layer**
(`backend/app/api/permissions.py`) lets an org **split** these duties via custom
roles. It is additive and backward-compatible: the four system roles behave
exactly as before.

- **Catalog** — a small, deliberately-scoped set of *splittable* permission
  constants (dotted strings): `invoice.approve`, `payment_run.approve`,
  `payment.execute`, `payment.void`, `vendor.bank_change.approve`,
  `vendor.block`, `vendor.manage`, `user.manage`. `GET /api/admin/permissions`
  (admin) returns the catalog (key + label) for the role editor. Everything not
  in the catalog stays on `require_roles`.
- **System-role defaults** — a static map (`ROLE_DEFAULT_PERMISSIONS`)
  reproduces today's matrix exactly: `admin` holds all; `ap_manager` holds
  invoice approve + run approve/execute + vendor bank-change/block/manage (NOT
  payment void); `cfo` holds invoice approve + run approve/execute + payment
  void; `ap_clerk` holds none.
- **Custom-role permissions** — a control-plane JSONB column `roles.permissions`
  (migration `0062_role_permissions`, control-plane-only — `roles` is
  control-plane). System roles leave it NULL (they resolve via the default map);
  custom roles store an explicit, sanitized list. A custom role with an empty
  list grants nothing (the inert pre-feature default).
- **Effective permissions** — the union over all the user's roles (system via
  the default map, custom via their stored list). Computed once in
  `get_current_user` (cached on `User.effective_permissions`), exposed on
  `GET /api/auth/me`'s `permissions` array, and enforced by
  `require_permission(*perms)` (any-of semantics, 403 on miss, WARN log; typos
  rejected at import time).
- **Migrated endpoints** — only the splittable sensitive set moved to
  `require_permission`: payment-run create (`payment_run.approve`), payment-run
  execute (`payment.execute`), payment void (`payment.void`), vendor
  create/update/verify/reject (`vendor.manage`), vendor block/unblock
  (`vendor.block`), vendor bank-change approve (`vendor.bank_change.approve`),
  and user create/update/delete/bulk-delete (`user.manage`). Role/permission
  CRUD itself stays admin-only on `require_roles` (managing the catalog must not
  be a grantable permission — that would be a privilege-escalation path).
  - **Role-grant is bounded by the grantor.** `user.manage` lets you assign
    roles, but not roles more powerful than you hold: `_authorize_role_grant`
    refuses to grant the system `admin` role unless the caller is themselves an
    admin, and refuses to grant any role whose catalog permissions aren't a
    subset of the caller's own effective permissions. Otherwise a custom role
    scoped to only `user.manage` could assign itself `admin` and take over the
    org.
  - **Admin-set passwords obey the complexity policy.** An admin resetting a
    user's password (`PATCH /api/admin/users/{id}` `password`) runs it through
    the same `validate_password_complexity` as self-service change-password
    (min 12, upper/lower/digit), so a `user.manage` actor can't reset an
    account to a trivial value and log in as it.
- **Frontend** — `auth.can(perm)` mirrors `require_permission`; the gated
  controls converted so far are payment Execute, payment Void, and vendor
  Block/Unblock. The `/admin/roles` editor renders permission checkboxes from
  the catalog and shows each custom role's grants. This composes with the
  instance-level SoD check (`check_segregation`, approver ≠ creator), which is
  unchanged.

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
   - Validates the ID token against the IdP's JWKS (signature + iss, aud, exp, nonce). Signature verification is pinned to asymmetric algorithms (`RS*`/`ES*`/`PS*`/`EdDSA`) via `services/sso.ID_TOKEN_ALGORITHMS`, so a forged token can't downgrade to HMAC (alg-confusion) or `alg:none`.
   - Optionally enforces `allowed_email_domains`.
   - JIT-provisions the user (match order: `(sso_provider, sso_provider_id)` → `(organization_id, email)` → new user with least-privilege `ap_clerk` role, or `admin` if it's the first user in the org).
   - Mints our own JWT and returns `{access_token, must_change_password, tenant_slug}`.
8. Frontend stores the JWT, fetches `/api/auth/me`, and routes to `/change-password` if flagged or `/` otherwise.

### Why callback URLs are per-tenant

Each customer registers their own Okta/Entra app with `redirect_uri = https://<theirtenant>.app.com/login/sso-callback`. That way the IdP redirects directly to the tenant origin and our localStorage JWT works without cross-origin hops. In dev, `AP_TENANT_URL_TEMPLATE=http://{slug}.localhost:7777` gives each tenant their own callback URL for free.

### Local testing with Keycloak (no cloud account)

You don't need an Okta/Entra tenant to exercise this flow locally. A Keycloak
container under the compose `idp` profile is the dev-laptop equivalent of the
IdP:

```bash
pnpm idp:up        # Keycloak on :8088 (Docker, opt-in)
pnpm idp:seed      # write the acme tenant's settings.sso → local Keycloak
pnpm dev           # then sign in via SSO at http://acme.localhost:7777
```

`demo@acme.com` / `demo` links to the seeded admin; `newhire@acme.com` / `demo`
JIT-provisions a fresh `ap_clerk`. Because the same generic OIDC code path runs,
a flow that works against Keycloak works against Okta/Entra. Full walkthrough:
[`local-sso-keycloak.md`](local-sso-keycloak.md).

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
| **Passkey / WebAuthn** | `passkey` | FIDO2 / WebAuthn — a platform authenticator (Touch ID, Face ID, Windows Hello) or a roaming security key. `py_webauthn` under the hood. A **separate code path** from TOTP (`services/webauthn.py`), opt-in and additive: a user can register one or many passkeys alongside or instead of TOTP. |
| **Email OTP** | `email` | 6-digit code emailed via the configured `AP_EMAIL_PROVIDER`. Lives in Redis with a `AP_MFA_EMAIL_OTP_TTL_SECONDS` TTL (default 6 minutes). Only the SHA-256 of the code is stored — Redis dumps don't reveal codes. Single-use. |

Email is offered as a backup so a lost phone doesn't lock the account out. It's also the only available factor for users under org-enforcement who haven't enrolled TOTP yet (verifying email proves inbox ownership before they're allowed to enroll).

### Passkeys (WebAuthn)

Passkeys are an additional second factor, gated by the same `AP_MFA_ENABLED` master switch. They use the standard two-ceremony WebAuthn protocol:

**Registration (enroll a passkey — authenticated, on `/profile`):**

```
POST /api/auth/mfa/passkey/register {password?|code?|assertion?}   # mints PublicKeyCredentialCreationOptions
  (browser runs navigator.credentials.create())
POST /api/auth/mfa/passkey/register/verify   # verifies + persists a WebAuthnCredential row
GET  /api/auth/mfa/passkey                    # list this account's passkeys (metadata only)
DELETE /api/auth/mfa/passkey/{id} {password?|code?|assertion?}   # remove one — step-up ALWAYS required
```

**Step-up (re-prove account control before a factor change — authenticated):**

```
POST /api/auth/mfa/step-up/passkey {operation}   # → PublicKeyCredentialRequestOptions
  (browser runs navigator.credentials.get())
  → send the signed response as `assertion` in the step-up body of the
    matching call (/mfa/enroll, /mfa/passkey/register, DELETE /mfa/passkey/{id},
    /mfa/disable)
```

**Authentication (passkey LOGIN — the passkey method of the MFA step):**

```
POST /api/auth/mfa/passkey/authenticate         {challenge_token}  # → PublicKeyCredentialRequestOptions
  (browser runs navigator.credentials.get())
POST /api/auth/mfa/passkey/authenticate/verify  {challenge_token, credential}  # → {access_token, ...}
```

Both authenticate endpoints are **pre-access-token, public-by-design**: the login-issued MFA challenge token (`typ: mfa_challenge`, the same credential the `/mfa/verify` path uses) IS the gate; there is no JWT yet. The register / list / delete endpoints require the normal JWT.

The per-ceremony challenge is server-minted and stashed in Redis (`webauthn:reg_challenge:<user_id>` for registration, `webauthn:auth_challenge:<user_id>` for login, `webauthn:stepup_challenge:<operation>:<user_id>` for a step-up — `AP_WEBAUTHN_CHALLENGE_TTL_SECONDS` TTL, single-use) so the verify call can't be fed an attacker-chosen challenge. **Those namespaces are a security boundary, not bookkeeping** — see "Purpose binding" below. On every successful assertion the credential's monotonic signature counter is verified and bumped — a regression (a cloned authenticator) is rejected per the WebAuthn spec. RP ID and allowed origins are configurable (`AP_WEBAUTHN_RP_ID` / `AP_WEBAUTHN_ORIGINS`); the dev defaults (`localhost` / `http://localhost:7777`) work across every tenant subdomain with no cloud account.

Stored material — the credential id, COSE public key, and counter — is not secret in the password sense (the private key never leaves the authenticator) and is never logged. The login challenge offers `passkey` as a method whenever the user has at least one registered credential.

Registering a passkey on an account that **already** has a factor (TOTP enabled, or at least one passkey) is a step-up operation with the same optional `{password?, code?, assertion?}` body as `/mfa/enroll` — otherwise a stolen session could quietly bind an attacker-controlled authenticator to the account. The *first* factor on a bare account needs no step-up.

**Deleting** a passkey is a step-up operation too, and unconditionally: the passkey being deleted is itself a live factor, so `DELETE /api/auth/mfa/passkey/{id}` always requires the password, a current authenticator code, or a passkey assertion. Removing a factor with a stolen token is the same attack as replacing one. The credentials travel in the request **body**, never a query string — a password must not land in access logs or a `Referer` header. An id that isn't the caller's own is still an opaque `404`, checked *before* the step-up so an unknown id can't be used to burn the account's throttle or probe for existence. On top of that, under org-enforced MFA the last surviving factor can't be removed at all.

The `/profile` passkey panel renders one "Confirm your password" field that serves both add and remove, shown only when a step-up actually applies. Leaving it blank on an account that holds a passkey runs the passkey step-up ceremony instead — the only route open to an SSO-only account.

#### Purpose binding — why a step-up assertion is not a login

`clientDataJSON.type` is `"webauthn.get"` for a login assertion and a step-up assertion alike; the authenticator has no idea what the relying party intends to do with the signature. The **challenge is therefore the only thing that can tell the two apart**, which makes the Redis namespace the binding mechanism:

| Ceremony | Redis slot |
|---|---|
| Passkey login | `webauthn:auth_challenge:<user_id>` |
| Step-up | `webauthn:stepup_challenge:<operation>:<user_id>` |

Each verify path reads only its own slot and consumes it single-use, so:

- a **step-up assertion cannot be replayed as a login** — `/mfa/passkey/authenticate/verify` looks in the login slot and finds a different (or no) challenge, and returns the same opaque `401` as any bad signature;
- a **login assertion cannot satisfy a step-up** — an attacker who observed a legitimate sign-in still can't change the victim's factors with it;
- an assertion collected to authorize `passkey_register` **cannot authorize `passkey_delete`** — one consented biometric prompt grants exactly the action it was requested for, not the whole factor-management surface.

`operation` is a closed set (`totp_enroll`, `totp_disable`, `passkey_register`, `passkey_delete`), rejected at the schema, so the challenge namespace can't be widened by the client. `services/webauthn._assertion_challenge_key` owns the mapping and `purpose` is a required argument on `begin_authentication` / `finish_authentication` — a caller cannot forget to say which ceremony it means. Regression tests: `backend/tests/test_webauthn_step_up.py`.

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

POST /api/auth/mfa/verify {challenge_token, code, method}    # totp / email factors
  └─ 200 {access_token, token_type, must_change_password}

# OR — the passkey factor (when methods includes "passkey"):
POST /api/auth/mfa/passkey/authenticate {challenge_token}    # → request options
POST /api/auth/mfa/passkey/authenticate/verify {challenge_token, credential}
  └─ 200 {access_token, token_type, must_change_password}
```

`methods` in the challenge response lists which factors the user can submit (`totp` / `passkey` / `email`), so a user who has registered only a passkey (no TOTP) still trips the MFA gate and is offered `passkey`.

The challenge token is itself a short-lived JWT (`AP_MFA_CHALLENGE_TTL_SECONDS`, default 5 minutes) with `typ: mfa_challenge`. That keeps the flow stateless — no DB row to garbage-collect, no Redis lookup on every check. A regular access token won't satisfy the challenge endpoint and vice versa.

**Token-type isolation is enforced at the dependency, not just the route.** `get_current_user` (in `app/api/deps.py`) rejects every JWT whose `typ` is a non-access type — `vendor`, `mfa_challenge`, or `vendor_mfa_challenge` (`_NON_ACCESS_TOKEN_TYPES`) — so a password-verified-but-MFA-pending caller cannot wield their `mfa_challenge` token as a fully-authenticated Bearer token and skip the second factor. A real access token is `typ="user"` (a missing `typ` is still accepted for legacy tokens predating the claim). Pinned by `backend/tests/test_auth_token_security.py::test_mfa_challenge_token_cannot_act_as_access_token` (and the `vendor_mfa_challenge` sibling), which wire the user lookup to a valid active user so only the type-rejection can produce the 401.

### Per-user enrollment

```
POST /api/auth/mfa/enroll     {password?|code?|assertion?}  # mints a CANDIDATE secret + URI + QR
POST /api/auth/mfa/enroll/verify {code}                     # promotes it, flips mfa_enabled true
POST /api/auth/mfa/disable    {password?|code?|assertion?}  # turns it off (step-up first)
POST /api/auth/mfa/step-up/passkey {operation}              # mint a step-up assertion challenge
```

The QR code is returned inline as a `data:image/png;base64,...` URL so the frontend doesn't need a separate authed image endpoint. The plaintext base32 secret is also returned so users without a camera-equipped scanner can paste it manually.

**Enrollment never disturbs the factor already in force.** `/mfa/enroll` mints a *candidate* secret and parks it in Redis (`mfa:pending_enroll:<user_id>`, `AP_MFA_ENROLL_PENDING_TTL_SECONDS`, default 15 min). `User.mfa_secret` / `mfa_enabled` / `mfa_enrolled_at` are written by `/mfa/enroll/verify` and nowhere else, so an abandoned or half-finished enrollment leaves the account exactly as it was. Previously enroll-start wrote the new secret straight onto the row and cleared `mfa_enabled`, which made *starting* an enrollment a silent second-factor strip.

**Changing an existing factor is a step-up operation.** When the account already has a live factor, `/mfa/enroll` (and `/mfa/passkey/register` — see below) requires one of:

- `password` — the account password, verified through the shared `pwd_context`, exactly like `/mfa/disable`; or
- `code` — a code from the **currently enrolled** authenticator (for the user who has their phone but not their password manager); or
- `assertion` — a **WebAuthn assertion from an already-registered passkey**, obtained from `POST /api/auth/mfa/step-up/passkey` for that same operation.

A "live factor" here means an enabled TOTP secret **or** at least one registered passkey — adding TOTP to a passkey-protected account is as much a factor change as the reverse, so both doors are gated the same way.

Neither field is required for a **first** enrollment: an account with no factor has nothing to protect, so onboarding stays frictionless. A missing or wrong step-up is a `400` with a generic message that reveals nothing about the account.

An SSO-only account — no password, no TOTP secret — has no *stateless* credential to be challenged on, and is still never **exempted**: exempting it would let a stolen JWT plant an attacker-controlled passkey on an account the attacker never proved control of. Instead, if it holds a registered passkey, that passkey **is** the challenge: `POST /api/auth/mfa/step-up/passkey` mints an assertion challenge bound to the operation, and the signed response goes back as `assertion`. That is what makes a passwordless SSO deployment able to enroll, rotate and remove its own factors at all; before it, such an account was locked out of factor management and recovered only via an admin password-set at `POST /api/admin/users/{id}/password` (still the fallback for an account with *no* factor of any kind, which genuinely has nothing to prove). The password / TOTP checks stay pure in `services/mfa.step_up_verified`; the assertion path is `api/auth._step_up_satisfied` because it needs the DB and Redis.

Every step-up check is **throttled and audited**: 5 attempts per minute keyed on the *account* (not the client IP — the attacker already holds the victim's token and can rotate IPs), and a failure writes a PII-free `auth.mfa.step_up.failure` / `portal.mfa.step_up.failure` audit row carrying only the operation name. Without that, a credential-management endpoint that checks a password is an unlimited, silent password oracle. The same throttle + audit covers `/mfa/disable` on both surfaces.

`/mfa/disable` rides the same three-proof gate: password, a code from the authenticator being turned off, or a passkey assertion (`operation=totp_disable`). It used to be password-only, which left an SSO-only account unable to disable its own TOTP. A stolen session still can't silently strip MFA off, and if the org enforces MFA, disable is blocked outright.

**The supplier portal has no passkey step-up.** `WebAuthnCredential` hangs off a control-plane `users.id` and `VendorUser` is tenant-scoped, so a portal user has no passkey to assert with; `/portal/auth/mfa/*` remains password-or-TOTP-code only. Adding portal passkeys would need a tenant-scoped credential table and is not part of this change.

### Org enforcement

`Organization.settings.mfa.required: bool` — toggled from **Organization → Security** (admin only). When true:

- Every user without `mfa_enabled` is gated. They can complete one email-OTP login but are routed straight to `/profile` to enroll TOTP.
- Per-user disable is blocked — if you don't want enforcement, turn off the org setting first.
- New users (including SCIM-provisioned ones) follow the same gate.

### What stays where

| Data | Lives in | Why |
|---|---|---|
| `User.mfa_secret` (base32) | control-plane DB | Per-user, durable, written ONLY by a successful `/mfa/enroll/verify`. |
| Pending (unverified) enrollment secret | Redis (`mfa:pending_enroll:<user_id>`, `mfa:vendor_pending_enroll:<id>`) | The candidate from an in-flight enrollment. Kept off the account row so starting an enrollment can't disturb the factor already in force; TTL `AP_MFA_ENROLL_PENDING_TTL_SECONDS`, consumed on verify. |
| `User.mfa_enabled`, `mfa_enrolled_at` | control-plane DB | Drives login-flow decisions. |
| `WebAuthnCredential` rows (credential id, COSE public key, sign counter) | control-plane DB (`webauthn_credentials`, migration 0063) | One row per registered passkey, keyed by `user_id` (control-plane, never tenant-fanned). Not secret in the password sense; never logged. |
| `Organization.settings.mfa.required` | control-plane DB (JSONB) | Org-wide policy; lives next to other settings. |
| Email-OTP hash | Redis (`mfa:email_otp:<user_id>`) | Short-lived, single-use, no need to persist. |
| WebAuthn ceremony challenge | Redis (`webauthn:{reg,auth}_challenge:<user_id>`) | Short-lived, single-use; the verify call rejects an attacker-chosen challenge. |
| MFA challenge token | client only (sessionStorage) | Stateless JWT — server doesn't need to remember it. |

### SSO + MFA

OIDC SSO sign-in does **not** trigger our MFA challenge — the IdP is the source of truth for "did this person prove a second factor." Configure MFA in Okta/Entra itself if you want it enforced for SSO users.

### Supplier-portal MFA (vendor users)

The supplier portal has its own TOTP MFA (with an email-OTP backup) for `VendorUser`s, mirroring this flow but on the separate vendor auth surface (`typ=vendor` JWT, tenant-scoped). It reuses the same `services/mfa.py` primitives and the same `AP_MFA_ENABLED` master switch.

- **Columns:** `vendor_users.mfa_secret` / `mfa_enabled` / `mfa_enrolled_at` (migration `0053_vendor_mfa`, tenant DB) — the exact shape of the `User` MFA columns. The email-OTP backup needs no column (Redis-only, like the employee one).
- **Opt-in per vendor user.** There's no org-wide enforcement for vendors (unlike employee `Organization.settings.mfa.required`). With `AP_MFA_ENABLED=false` (local-dev default), an enrolled vendor still logs in with just a password.
- **Login challenge** returns `PortalMFAChallengeResponse` (`methods: ["totp", "email"]`); the vendor completes `POST /api/portal/auth/mfa/challenge` (with `method` totp|email) to mint the access token. Enroll / verify / disable live at `/api/portal/auth/mfa/{enroll,verify,disable}`.
- **Same enrollment safety as the employee surface.** `/portal/auth/mfa/enroll` parks a *candidate* secret in Redis (`mfa:vendor_pending_enroll:<id>`) and writes nothing to `vendor_users`; only `/mfa/verify` promotes it. Re-enrolling over a live factor requires the same `{password?, code?}` step-up (portal password or a code from the current authenticator), so a stolen vendor session can't strip or swap the supplier's second factor. First-time enrollment needs no step-up.
- **Email-OTP backup.** When the vendor has lost their authenticator, `POST /api/portal/auth/mfa/challenge/email` (public, gated by the `vendor_mfa_challenge` token) emails a single-use 6-digit code via the configured email adapter (`console` in dev). Its SHA-256 lives in Redis under a distinct keyspace (`mfa:vendor_email_otp:<id>`, separate from the employee `mfa:email_otp:`) with the `AP_MFA_EMAIL_OTP_TTL_SECONDS` TTL. Gated on the vendor having enrolled TOTP — a backup to the authenticator, not a standalone enrollment path. 204-silent for unenrolled / unknown accounts (no enumeration); OTP + email never logged.
- **Token-type isolation.** The portal challenge token carries `typ=vendor_mfa_challenge` — distinct from the employee challenge (`mfa_challenge`) and the vendor access token (`vendor`) — so the three token types can never be substituted for one another across surfaces. Full reference: `backend/docs/supplier-portal.md` § MFA.

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/auth/login` | Returns either a token or an MFA challenge. |
| `POST` | `/api/auth/mfa/challenge/email` | Sends a 6-digit code to the user's email. |
| `POST` | `/api/auth/mfa/verify` | Trades a challenge token + code for an access token. |
| `POST` | `/api/auth/mfa/enroll` | Starts TOTP enrollment — returns a CANDIDATE secret + QR. Step-up (`password` or current `code`) required when a factor is already live. |
| `POST` | `/api/auth/mfa/enroll/verify` | Confirms enrollment with a valid code — the only writer of `mfa_secret`/`mfa_enabled`. |
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
| `PUT` | `/Users/{id}` | **Full-resource replace.** Authentik (and RFC 7644 §3.5.1) update users via PUT, not PATCH. Returns 409 `uniqueness` if the new userName collides with another user. |
| `PATCH` | `/Users/{id}` | Partial update. Supports the ops Okta + Entra send (active toggle, userName/externalId/name replace, root-object replace). |
| `DELETE` | `/Users/{id}` | **Soft delete** — sets `is_active=false`. Preserves audit trail. |
| `GET` | `/Groups` | List + paginate; `displayName eq` filter. |
| `GET` | `/Groups/{id}` | Fetch one group. |
| `POST` | `/Groups` | Create. 409 `uniqueness` on duplicate displayName. |
| `PUT` | `/Groups/{id}` | Full replace (displayName + members). |
| `PATCH` | `/Groups/{id}` | Add/remove/replace members + rename — the op/path shapes Okta/Entra/Authentik send. |
| `DELETE` | `/Groups/{id}` | Remove the group (revokes its mapped role from former members). |

Both `PUT` and `PATCH` `db.refresh()` the row after the flush: the `UPDATE` fires
`updated_at`'s server-side `onupdate`, which SQLAlchemy expires — reloading it in
the async handler avoids a sync lazy-load (`MissingGreenlet` → 500) when the SCIM
response reads `meta.lastModified`.

### Filter syntax

Only the filter subset Okta + Entra actually use:

```
userName eq "alice@acme.com"
emails eq "alice@acme.com"
externalId eq "okta-user-id"
active eq true
```

Anything else returns a `400 invalidFilter` with a clear message so the IdP surfaces a useful error.

### Local testing with Authentik (no cloud account)

You don't need an Okta/Entra tenant to exercise SCIM. A Keycloak container covers
inbound SSO; an **Authentik** container (Docker Compose `idp` profile) covers
outbound SCIM — it's the SCIM *client* that pushes users into `/api/scim/v2`:

```bash
pnpm idp:up        # Keycloak + Authentik (Docker)
pnpm scim:seed     # set the matching SCIM bearer token on the acme tenant
pnpm dev           # the app must be running — Authentik POSTs to :8000
# Authentik admin http://localhost:9002 (akadmin / admin) → Providers → Run sync
# Provisioned users appear at http://acme.localhost:7777/admin
```

Deterministic CI coverage of the same contract (create → filter → PUT → PATCH
deactivate → DELETE, verified in `/admin`) lives in
`frontend/tests-e2e/scim/provisioning.spec.ts` (`pnpm test:scim`) — it runs
without the Authentik container, which CI can't host. Full walkthrough:
[`local-sso-keycloak.md` § Authentik](local-sso-keycloak.md#authentik--local-scim-provisioning).

### Groups → role mapping

`/Groups` maps IdP groups onto our RBAC `Role` rows. Group state (displayName,
externalId, member ids) is stored as JSONB on `settings.sso.scim_groups` —
groups are few per tenant and the only thing we do with one is map it to a role,
so no dedicated table (a control-plane `scim_groups` table is the upgrade path if
volume grows; the service boundary stays the same). The mapping lives in
`settings.sso.scim_group_role_map` (`{displayName: role_name}`).

On any membership change (create / PUT / PATCH / delete), affected users are
**reconciled** (`services/scim_groups.py::reconcile_user_roles`): a user's
SCIM-derived roles = the mapped role of every group they belong to. Only roles
named in the map are added/removed — manual and JIT-default assignments to other
roles are never touched. **Contract:** a role named in the map becomes
IdP-managed for any user the IdP places in (or removes from) a mapped group.
Member ids are validated against real org users before reconciliation, so a
phantom id can't grant a role. Unmapped groups are stored and synced but grant
nothing until an admin adds them to the map.

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

The matrix below is the source of truth — it mirrors the per-route `roles` gates in `frontend/src/lib/nav.ts` (which drives sidebar + section-tab visibility) and the `!isClerkOnly` / `isManager` / `isCfo` checks in invoice + workflow components. Roles are non-exclusive: a user may hold any combination.

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
- **Per-org custom roles with teeth** — *Done.* Custom roles now grant access via the granular permission layer (`roles.permissions` + `require_permission`) — see § Granular permissions / segregation of duties above. A custom role granted, say, only `invoice.approve` can approve invoices but is 403'd on payment execution. Permission CRUD itself stays admin-only on purpose.
- **Audit log of denied requests** — denials are logged via Python `logging.warning` for now, not persisted to the `audit_log` table. If oncall wants to query historical denials, surface them via centralized log shipping (planned under SOC 2 readiness).
