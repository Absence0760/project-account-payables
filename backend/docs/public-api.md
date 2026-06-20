# Public Developer API & Webhooks

Programmatic, versioned access to the platform for external integrators —
authenticated with per-org **API keys** (not the SPA's JWT session). This is the
**first slice**: API-key auth + key management + a small read-only `/api/v1`
surface. Outbound webhooks and a published OpenAPI spec are **later slices** (see
[Deferred](#deferred)).

## Auth model

| Surface | Auth | Header |
|---------|------|--------|
| `/api/v1/*` (programmatic) | API key | `X-API-Key: ap_live_…` |
| `/api/api-keys/*` (key management) | JWT session + `admin` role | `Authorization: Bearer …` + `X-Tenant-Slug` |

A programmatic request carries **no** `X-Tenant-Slug` header and **no** JWT — the
**API key IS the tenant boundary**. The key resolves to its `Organization` in the
control plane, and the org resolves to its tenant DB via the same
`get_tenant_engine(org.db_name)` chokepoint the JWT path uses
(`app/api/deps.py::get_api_key_db`). So every `/api/v1` read is tenant-isolated at
the data layer — there is no header a caller can swap to widen access.

### Why API keys are SHA-256, not bcrypt

API keys are high-entropy random tokens (`secrets.token_urlsafe(32)`), not
user-chosen passwords. They must be **looked up** by the value the caller
presents, and a salted bcrypt hash (the `bcrypt_sha256` password context) is
deliberately un-indexable — you'd have to scan every row and bcrypt-verify each.
So the platform stores an **unsalted `sha256(full_key)`** digest plus an indexed
`key_prefix`, resolves the candidate row(s) by prefix, then **constant-time
compares** the digest (`hmac.compare_digest`). This is the identical pattern the
SCIM bearer token already uses (`Organization.scim_bearer_hash`,
`services/sso.generate_scim_token`). The `bcrypt_sha256` project invariant is the
**password** path; brute-forcing a 256-bit random token is infeasible, so an
unsalted SHA-256 is appropriate here. Rationale is also captured in a code comment
in `app/models/api_key.py` and `app/services/api_keys.py` so a reviewer doesn't
flag it.

## Key lifecycle

| Step | Endpoint | Notes |
|------|----------|-------|
| Mint | `POST /api/api-keys` (admin) | Body `{ "name": "reporting-bot" }`. Returns the **plaintext key exactly once** in `key`, plus metadata in `api_key`. Audited `api_key.created` (PII-free: prefix + name + scopes). |
| List | `GET /api/api-keys` (admin) | Metadata only — prefix, scopes, timestamps. **Never** the hash or plaintext. |
| Revoke | `DELETE /api/api-keys/{id}` (admin) | Soft revoke (`revoked_at` stamped; row kept for audit). Idempotent. Audited `api_key.revoked`. |

- **Plaintext shown once.** The mint response is the only place the full key ever
  appears. It is never stored or logged — only `key_hash` + `key_prefix` persist.
- **Format.** `ap_live_<43 url-safe chars>`. The stable `ap_live` brand makes a
  leaked key recognisable to secret-scanners and greppable.
- **Scopes.** This slice mints `["read"]` only. The `scopes` column is JSONB and
  each route is gated by `require_api_scope(...)`, so a future write surface
  inherits enforcement without a migration.
- **`last_used_at`.** Stamped best-effort on every successful auth (its own
  session; a failure there never breaks a valid request).

### Failure mode

Every API-key failure (missing header, unknown prefix, bad digest, revoked key,
or the platform kill switch `AP_PUBLIC_API_ENABLED=false`) returns the **same
opaque `401 {"detail": "Invalid API key"}`** with `WWW-Authenticate: ApiKey`.
Distinct messages would let a caller enumerate which keys/prefixes exist. A
foreign / missing invoice id on `GET /api/v1/invoices/{id}` is a `404` — and a
key for tenant A literally cannot see tenant B's row (the session is bound to A's
DB), so a cross-tenant id is simply "not found", never leaked.

## `/api/v1` read surface

All routes are behind `require_api_scope("read")` (→ `get_api_key_principal`) and
read through `get_api_key_db` (the tenant session resolved from the key).

| Method | Path | Returns |
|--------|------|---------|
| `GET` | `/api/v1/invoices` | Paginated list. Query: `status`, `page` (≥1), `page_size` (1–200). Shape `{ data: [V1Invoice], page, page_size, total }`. |
| `GET` | `/api/v1/invoices/{id}` | One `V1Invoice`; `404` if not found in this tenant. |

`V1Invoice` (`app/schemas/public_v1.py`) is a **stable, curated subset** of the
internal `Invoice` ORM model — adding an internal column does **not** change the
v1 response; fields must be added here explicitly. **Money (`amount`) serialises
as a JSON string** (e.g. `"123.45"`) so external clients get exact arithmetic
with no float rounding — stricter than the SPA-facing `MoneyAmount` type, which
emits a JSON number. The value is `Decimal` end to end in Python (money-is-exact
invariant).

```
V1Invoice = { id, invoice_number, vendor_name, amount, currency,
              status, invoice_date, due_date, created_at }
```

## Config

| Variable | Default | Purpose |
|----------|---------|---------|
| `AP_PUBLIC_API_ENABLED` | `true` | Platform kill switch for the `/api/v1` surface. The surface is auth-gated regardless; when `false` every key fails closed with the opaque 401. No secret — API keys are minted per-org and stored hashed. |

There is no API-key secret in config or `.env` — each key is generated at mint
time and only its hash persists, so the secrets-via-sops / no-hardcoded-fallback
rule has nothing to hold here.

## Data model

`ApiKey` (control plane, table `api_keys`, migration **0055**, added to
`CONTROL_TABLES`): `id`, `organization_id` (FK), `name`, `key_prefix` (indexed),
`key_hash` (sha256 hex), `scopes` (JSONB), `last_used_at`, `revoked_at`,
`created_at`/`updated_at`. Control-plane-only migration (gated on the
`organizations` table existing — mirrors `0021_scim_bearer_hash`); it does **not**
fan out to tenant DBs.

## Deferred

These are explicitly **out of scope** for this slice and tracked as later
roadmap work:

- **Outbound webhooks** — registering subscriber URLs, signing event payloads
  (the HMAC helpers already exist in `services/webhook_security.py`), retry/DLQ.
- **Published OpenAPI spec / contract** — a versioned, downloadable schema for
  the `/api/v1` surface and a developer portal.
- **Write scopes + endpoints** — only `read` is minted today. The scope plumbing
  (`scopes` column + `require_api_scope`) is in place for it.
- **Per-key rate limiting** — the repo has a Redis sliding-window limiter
  (`services/rate_limit.py`) that a future slice should key on `api_key_id`; not
  wired in this slice to avoid inventing a new policy surface prematurely.
