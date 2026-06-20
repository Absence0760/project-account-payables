# Public Developer API & Webhooks

Programmatic, versioned access to the platform for external integrators —
authenticated with per-org **API keys** (not the SPA's JWT session). The first
slice shipped API-key auth + key management + a small read-only `/api/v1`
surface; the second shipped **outbound webhooks** (see [Outbound
webhooks](#outbound-webhooks)); the third shipped the **published, versioned
OpenAPI spec + a developer docs page** for the `/api/v1` surface (see
[Published OpenAPI spec](#published-openapi-spec) and [Versioning &
deprecation policy](#versioning--deprecation-policy)).

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

## Published OpenAPI spec

The `/api/v1` surface ships a **published, versioned OpenAPI document** — the
machine-readable contract integrators code (and generate clients) against.

| Method | Path | Returns |
|--------|------|---------|
| `GET` | `/api/v1/openapi.json` | The OpenAPI 3.1 document for the public surface (JSON). |
| `GET` | `/api/v1/docs` | Swagger UI rendered against that spec (human-readable). |

Both are **public** (no `X-API-Key` needed to *read the contract*) but both
respect the `AP_PUBLIC_API_ENABLED` kill switch: when the public API is off they
`404` — the surface, and therefore its contract, is simply not there. The 404
(rather than a distinct "disabled") matches the opaque-failure posture of the
data routes.

**Scoped, not the whole app.** The spec is generated from the *live* FastAPI
route table (so it can never drift from the routes) but then filtered to the
`/api/v1` paths only and overlaid with a curated security scheme + servers +
version (`app/api/v1_openapi.py::build_public_openapi`). The internal SPA API
(`/api/auth`, `/api/invoices`, `/api/payments`, …) is **never** described here,
and component schemas are pruned to those reachable from the v1 routes — an
internal-only Pydantic model can't leak into the public contract via an orphan.

What the document carries:

- `info.version: "v1"` — the contract version (tracks the path prefix).
- A `servers` entry built from `AP_API_PUBLIC_URL`, so generated clients target
  the right base URL.
- A single `ApiKeyAuth` security scheme (`apiKey` in the `X-API-Key` header),
  applied **globally** — every operation shows the auth requirement.
- The published `V1Invoice` / `V1InvoiceList` component schemas, with `amount`
  typed as a **string** (money-is-exact — exact arithmetic over JSON, no float),
  and the `status` / `page` / `page_size` pagination parameters on the list
  operation.

The route mount is additive in `app/main.py` (`public_v1_openapi_router`); the
generator is pure with respect to the app (reads routes, returns a fresh dict),
so it's safe to build per request.

> Relation to `endpoint-inventory`. The internal `/endpoint-inventory` skill
> enumerates **every** backend route for `/audit/auth` and integrator docs. This
> published spec is the narrower, *contractual* artifact: only the supported
> `/api/v1` routes, with the stability guarantees below. The live `/api/v1`
> routes are the source of truth; the spec is generated from them, not hand-kept.

## Versioning & deprecation policy

The `/api/v1` surface carries an explicit stability contract so integrators can
build against it safely:

- **Versioning is in the path.** A new major version is a new prefix
  (`/api/v2`), served alongside `/api/v1` with its own `openapi.json` + `docs`.
  The `info.version` string tracks the prefix.
- **`v1` is additive-only.** Within `v1` we may **add** optional response fields,
  new endpoints, and new optional query params. We will **not** remove or rename
  a documented field, change its type (e.g. `amount` stays a string), narrow an
  enum, or make an optional param required — those are breaking changes and
  require a new version. The `V1Invoice` schema is decoupled from the internal
  ORM precisely so an internal column change never silently alters the contract.
- **Deprecation window.** When a version is slated for sunset, it is announced in
  this doc and (where applicable) flagged via the standard `Deprecation` /
  `Sunset` response headers and `deprecated: true` in the OpenAPI operation. A
  deprecated version is supported for **at least 6 months** after the
  announcement before removal, with the successor version available for the whole
  window so integrators can migrate.
- **The kill switch is not a deprecation.** `AP_PUBLIC_API_ENABLED=false` is an
  operational stop (incident response), not a contract change; it 404s the whole
  surface immediately and uniformly.

## Config

| Variable | Default | Purpose |
|----------|---------|---------|
| `AP_PUBLIC_API_ENABLED` | `true` | Platform kill switch for the `/api/v1` surface — the read routes **and** the published spec/docs (`/api/v1/openapi.json`, `/api/v1/docs`). The surface is auth-gated regardless; when `false` every key fails closed with the opaque 401 and the spec/docs 404. No secret — API keys are minted per-org and stored hashed. |

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

## Outbound webhooks

The push counterpart of the `/api/v1` pull surface: an org's integrator
subscribes to platform events, and the platform POSTs a **signed** JSON payload
to the subscriber URL when an event fires — bounded retries, dead-letter, and a
redelivery endpoint. Mirrors the inbound-webhook discipline (sign + dedupe).

### Data model (control plane)

Both tables live in the **control plane** keyed by `organization_id` — the SAME
placement as `api_keys` (an outbound webhook is the push counterpart of the
programmatic surface; a subscription belongs to an org, not a tenant DB). Added
to `tenant_provisioning.CONTROL_TABLES`. Migration **0057** (control-plane-only,
gated on the `organizations` table existing — mirrors 0055).

- **`webhook_subscriptions`** — `id`, `organization_id`, `name`, `target_url`
  (http(s)), `event_types` (JSONB subset of the catalog), `signing_secret`,
  `secret_prefix`, `active`, timestamps.
- **`webhook_deliveries`** — `id`, `subscription_id` (FK, `ON DELETE CASCADE`),
  `organization_id`, `event_id`, `event_type`, `payload` (JSONB, frozen at emit),
  `status` (`pending`/`delivered`/`failed`/`dead`), `attempt_count`,
  `next_attempt_at`, `last_attempt_at`, `response_code`. Unique on
  `(subscription_id, event_id)` (`uq_webhook_delivery_sub_event`) — the dedupe
  guard so a re-fired/replayed event can't queue the same delivery twice.

### Event catalog

`invoice.approved`, `payment.settled`, `exception.raised` (see
`app/models/webhook.py::WEBHOOK_EVENT_TYPES`). The catalog is plain strings, so
adding one needs no migration.

### Signing

Each subscription has its own HMAC-SHA256 **signing secret**, generated at
create time (`whsec_<…>`) and returned to the admin **exactly once** (like an
API-key mint). It is stored verbatim because the dispatcher must sign with it —
an HMAC verification key is *symmetric* by definition (the same trade-off the
per-tenant inbound webhook secrets make). List/get responses carry only
`secret_prefix`, never the full secret. The signature is the exact
`webhook_security.verify_hmac_sha256` primitive (HMAC-SHA256 over the raw body,
hex digest), sent in `X-Webhook-Signature`; the receiver re-derives it the same
way. Headers also carry `X-Webhook-Event-Id` + `X-Webhook-Event-Type` so the
receiver can dedupe.

### Payload

```jsonc
{ "id": "invoice.approved:<invoice-id>", "type": "invoice.approved",
  "created_at": "<iso8601>", "organization_id": "<org-id>",
  "data": { "invoice_id": "…", "invoice_number": "…", "vendor_name": "…",
            "amount": "123.45", "currency": "USD", "status": "approved" } }
```

`amount` is an **exact JSON string** (money-is-exact), not a float. Payloads are
PII-free — invoice metadata only, no bank/tax/PAN fields.

### Dispatch + retry + dead-letter

`services/webhooks/` (new package):

- **emit** (`dispatch.emit_event`) — the single chokepoint the event sources
  call. Opens its OWN short-lived control-plane session (the caller's session is
  tenant-scoped), inserts one `WebhookDelivery(status=pending)` per matching
  active subscription (deduped on `(subscription_id, event_id)`), then kicks off
  a fire-and-forget immediate delivery attempt on the running loop. **Never
  raises into the caller** and is a silent no-op when `AP_WEBHOOKS_ENABLED` is
  off (same best-effort contract as `notification_dispatch.notify_event`).
- **deliver** (`delivery.process_delivery`) — signs the byte-identical frozen
  payload, POSTs via `httpx` (10 s timeout), classifies the result. `2xx` →
  `delivered`; otherwise increment `attempt_count` and, if attempts remain
  (max 5), schedule `next_attempt_at` via exponential backoff
  (`30s · 2^(n-1)`); once exhausted → `dead` (dead-letter). A transport error
  (timeout / refused) is a failed attempt with a null `response_code`.
- **retry sweep** (`delivery.run_webhook_delivery_loop`) — background loop, gated
  behind `AP_WEBHOOKS_ENABLED` (OFF by default), re-attempts every due
  `pending`/`failed` delivery. The durable backstop for retries; the immediate
  emit attempt handles the happy path. Local-first: delivery is an in-process
  `httpx` POST — no cloud queue.

### Event sources wired

The emit is hooked into `workflow_engine.transition_invoice` — the single
invoice-status chokepoint — alongside the existing notification hook, keyed off
the resulting status: `approved` → `invoice.approved`, `paid` →
`payment.settled`. Every path that converges on those statuses (the ERP-sync /
payment-webhook / direct-schedule paths) emits exactly once, here.

**`exception.raised` is deferred this slice** — there is no single commit
chokepoint for `Exception` creation (it's scattered across
`invoice_warnings.py`, `extraction.py`, `review.py`, etc.), and the primary site
(`invoice_warnings.py`) was being edited concurrently. The typed helper
`dispatch.emit_exception_raised` already exists, so the follow-up only adds one
emit line at a non-conflicting exception chokepoint. **Follow-up:** wire
`exception.raised` from a single Exception-commit chokepoint (or add one) so the
emit isn't duplicated across the scattered creation sites.

### Management API (`/api/webhooks`)

Admin-gated (JWT + `require_roles(ROLE_ADMIN)`), control-plane. Every mutation
writes a PII-free audit row (`webhook_subscription.created/updated/deleted`,
`webhook_delivery.redelivered`).

| Method | Path | Notes |
|--------|------|-------|
| `POST` | `/api/webhooks` | Create a subscription. Validates http(s) URL + known event types. Returns the `signing_secret` **once**. |
| `GET` | `/api/webhooks` | List this org's subscriptions (metadata only — never the full secret). |
| `PATCH` | `/api/webhooks/{id}` | Update name / target_url / event_types / active. |
| `DELETE` | `/api/webhooks/{id}` | Delete (CASCADE removes its deliveries). 404 (opaque) for wrong-org. |
| `GET` | `/api/webhooks/deliveries` | List deliveries (org-scoped), filter `subscription_id` / `status`, paginated. |
| `POST` | `/api/webhooks/deliveries/{id}/redeliver` | Re-enqueue a `failed`/`dead` delivery (resets the counter) and attempt inline. `409` on an already-`delivered` row (would double-fire). |

### Config

| Variable | Default | Purpose |
|----------|---------|---------|
| `AP_WEBHOOKS_ENABLED` | `false` | Master switch for outbound webhooks — gates BOTH `emit_event` (OFF → silent no-op, no outbound HTTP) and the background retry/delivery sweep. OFF by default so a fresh clone / `pnpm dev` never makes outbound calls. Flip on in deployed envs. No secret — each subscription's signing secret is generated at create time and stored on the row. |
| `AP_WEBHOOKS_DELIVERY_INTERVAL_SECONDS` | `60` | Retry-sweep tick interval. |

## Deferred

These are explicitly **out of scope** for the current slices and tracked as
later roadmap work:

- **`exception.raised` event source** — see [Event sources wired](#event-sources-wired).
- **Full developer portal** — the published [OpenAPI spec + Swagger UI docs
  page](#published-openapi-spec) ship now; a richer hosted portal (guides,
  changelog, API-key self-service from the docs) is later work.
- **Write scopes + endpoints** — only `read` is minted today. The scope plumbing
  (`scopes` column + `require_api_scope`) is in place for it.
- **Per-key rate limiting** — the repo has a Redis sliding-window limiter
  (`services/rate_limit.py`) that a future slice should key on `api_key_id`; not
  wired in this slice to avoid inventing a new policy surface prematurely.
