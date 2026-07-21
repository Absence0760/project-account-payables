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
| Usage | `GET /api/api-keys/{id}/usage?window_days=30` (admin) | Per-key request totals (counts only) from the `api_key_usage` meter — all-time `total_requests`, trailing-`window_days` `window_requests`, `last_used_at`, and a per-day `daily` breakdown. Org-scoped (wrong-org id → opaque 404, same as revoke). **Never** the hash or plaintext. |
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

### Per-key usage metering

Every authenticated `/api/v1` request increments a **per-key, per-day** counter
so usage is queryable for the billing track and for an admin "how busy is this
key" read. The store is the aggregate `api_key_usage` table — one row per
`(api_key_id, usage_date)` holding a running `request_count` — not a per-request
log: aggregation keeps the write a single `INSERT … ON CONFLICT … DO UPDATE`
increment and the read a cheap rollup, and it stores **no** request payloads or
PII (only counts + a UTC day).

The increment rides the **same** best-effort write the `last_used_at` stamp does,
on the `get_api_key_principal` auth path (the upsert + the `last_used_at` UPDATE
are one commit on the request-scoped control session — see `app/api/deps.py::
_record_api_key_usage`). **Metering is best-effort:** if the usage write fails it
is swallowed with a PII-free warning (the key id only, never the key material)
and the request proceeds — a meter failure never breaks an otherwise valid
authenticated call. Reads land via `GET /api/api-keys/{id}/usage` (above).

### Per-key rate limiting

Every authenticated `/api/v1` request is rate-limited **per API key** by the
existing Redis sliding-window limiter (`services/rate_limit.py`), keyed on the
`api_key_id` (not per-IP, not per-org — one key flooding the API can't lock out
another key, even within the same org). The cap is
`AP_PUBLIC_API_RATE_LIMIT_PER_MINUTE` (default **120 req/min**) over a fixed
**60-second** window. A key over its cap gets an
**HTTP `429 Too Many Requests`** with a **`Retry-After`** header (seconds until
the oldest in-window request ages out).

Ordering matters for non-enumeration: the limit is checked **after** the key
authenticates, inside `get_api_key_principal` (just before the principal is
returned). An unauthenticated / garbage / revoked key short-circuits on the
opaque `401` above and never reaches the limiter — so a `429` only ever confirms
a **valid** key that is over its limit, never that a key exists.

**Fails open on a Redis outage.** The limiter raises `RateLimitExceeded` (the
429) which is allowed to propagate, but any *other* failure in the check — e.g.
Redis unreachable — is swallowed with a PII-free warning (the key id only) and
the request proceeds. A Redis blip must not deny otherwise-valid authenticated
API access; this matches the best-effort posture of the adjacent usage meter.
The limiter is also gated by the global `AP_RATE_LIMIT_ENABLED` master switch
(CI's e2e suite flips it off).

### Failure mode

Every API-key failure (missing header, unknown prefix, bad digest, revoked key,
or the platform kill switch `AP_PUBLIC_API_ENABLED=false`) returns the **same
opaque `401 {"detail": "Invalid API key"}`** with `WWW-Authenticate: ApiKey`.
Distinct messages would let a caller enumerate which keys/prefixes exist. A
foreign / missing invoice id on `GET /api/v1/invoices/{id}` is a `404` — and a
key for tenant A literally cannot see tenant B's row (the session is bound to A's
DB), so a cross-tenant id is simply "not found", never leaked.

## `/api/v1` read surface

All routes are behind `require_api_scope("read")` (→ `get_api_key_principal`)
**and** `require_api_entitlement("public_api")` — the public API is a
paid-plan feature, 402 without it — and read through `get_api_key_db` (the
tenant session resolved from the key).

**Local dev:** a freshly provisioned org (including the two demo tenants,
`scripts/seed.py`) starts on the `free` plan, which does not grant
`public_api` — so `/api/v1` 402s out of the box, by design. Unlock it locally
with no cloud account by upgrading via `POST /api/billing/change-plan
{"plan_code": "growth"}` (the `mock` billing adapter handles this with no
Stripe key). See `docs/billing.md` § Default plan catalog + baseline
Subscription and § Entitlement gating.

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
| `AP_PUBLIC_API_RATE_LIMIT_PER_MINUTE` | `120` | Per-API-key request cap on `/api/v1`, over a fixed 60-second window, enforced by the Redis sliding-window limiter keyed on `api_key_id`. A key over its cap gets a 429 + `Retry-After`. Checked after the key authenticates (a bad key still gets the opaque 401, never a 429). Fails open on a Redis outage; gated by the global `AP_RATE_LIMIT_ENABLED` master switch. No secret. |

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

`ApiKeyUsage` (control plane, table `api_key_usage`, migration **0058**, added to
`CONTROL_TABLES`): `id`, `api_key_id` (FK, `ON DELETE CASCADE`),
`organization_id` (FK — denormalised so a billing rollup can `GROUP BY` org
without joining `api_keys`), `usage_date` (UTC day), `request_count`
(`BigInteger`, exact), `created_at`/`updated_at`. Unique on
`(api_key_id, usage_date)` (`uq_api_key_usage_key_day`) — the upsert target for
the per-request increment. Control-plane-only migration (gated on `organizations`
existing — mirrors `0055`); it does **not** fan out to tenant DBs, and a
control-plane-only change needs no `migrate_all_tenants.py` run.

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

`exception.raised` carries the exception classification plus the same invoice
metadata (and a deep link):

```jsonc
{ "id": "exception.raised:<exception-id>", "type": "exception.raised",
  "created_at": "<iso8601>", "organization_id": "<org-id>",
  "data": { "exception_id": "…", "exception_type": "duplicate",
            "severity": "warning", "status": "open",
            "invoice_id": "…", "invoice_number": "…", "vendor_name": "…",
            "amount": "123.45", "currency": "USD", "link": "/invoices/<id>" } }
```

The `id` (and thus the dedupe `event_id`) is `exception.raised:<exception-id>`,
so a re-fire / replay of the same exception delivers once. An invoice-less
exception (e.g. a Positive Pay never-issued cheque) carries `invoice_id` /
`invoice_number` / `vendor_name` / `amount` as `null` and `link: "/exceptions"`.

`amount` is an **exact JSON string** (money-is-exact), not a float. Payloads are
PII-free — invoice/exception metadata only, no bank/tax/PAN fields.

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

**`invoice.approved` / `payment.settled`** are hooked into
`workflow_engine.transition_invoice` — the single invoice-status chokepoint —
alongside the existing notification hook, keyed off the resulting status:
`approved` → `invoice.approved`, `paid` → `payment.settled`. Every path that
converges on those statuses (the ERP-sync / payment-webhook / direct-schedule
paths) emits exactly once, here.

**`exception.raised`** is emitted from the shared exception-create chokepoint
`services/exception_service.create_exception`. Every `Exception` row in the
codebase is now constructed through this one helper — it builds the row,
flushes for the id, then best-effort emits `exception.raised` (the exception id
is the `event_key`, so a re-run / double-fire dedupes on
`(subscription, exception.raised:<exception-id>)`). The five former
construction sites all route through it, so coverage is complete (no
silent partial coverage):

| Source | Exception types | Invoice in payload? |
|--------|-----------------|---------------------|
| `invoice_warnings._ensure_exception` | `duplicate`, `po_mismatch`, `fraud_flag`, `unverified_vendor`, `amount_exceeded`, `missing_data`, `quality_hold`, `contract_noncompliant`, price-variance, LLM-anomaly | yes |
| `extraction.run_extraction` | `duplicate` (semantic), `extraction_failed` | yes |
| `review.reject_invoice` | `review_rejected` | yes |
| `positive_pay` return-processing | `fraud_flag` (altered / never-issued cheque) | only when the cheque maps to an invoice; never-issued → identifiers only |

Each caller keeps its own dedupe-precheck (different uniqueness rules), so the
helper never double-creates; it owns only the construct → flush → emit tail.
Like the invoice-status emit, it never raises into the caller and is a silent
no-op when `AP_WEBHOOKS_ENABLED` is off — a webhook failure can't break
exception creation or the invoice mutation that triggered it.

### Target-URL SSRF guard

A subscription's `target_url` is attacker-controllable input (any tenant admin
sets it), and the dispatcher POSTs **signed invoice/payment payloads** to it —
so the destination is validated, not just the scheme (issue #171). The single
shared validator is `services/webhooks/url_guard.ensure_public_webhook_target`,
enforced at **both** boundaries:

- **Create / update** (`POST /api/webhooks`, `PATCH /api/webhooks/{id}`) — a
  non-public target is rejected with a clean `422` carrying ONE generic message
  (`target_url must be a publicly routable http(s) URL`) for every rejection
  reason, so the response can't be used to probe which internal ranges/hosts
  exist.
- **Immediately before every dispatch** (`delivery.process_delivery`) — the
  stored host is **re-resolved at send time**, closing the TOCTOU / DNS-rebinding
  hole where a hostname passes validation at create and later flips to a private
  address. A refused send is a normal failed attempt (PII-free log — delivery id
  + event type, never the URL/host/address; no `response_code`), so the standard
  retry/backoff → dead-letter path applies.

Blocked: any target whose host resolves to an address that is not globally
routable — loopback (`127/8`, `::1`), RFC1918 private (`10/8`, `172.16/12`,
`192.168/16`), link-local `169.254/16` (incl. the AWS/cloud metadata endpoint
`169.254.169.254`) and IPv6 `fe80::/10`, CGNAT `100.64/10`, unique-local
`fc00::/7`, unspecified (`0.0.0.0`, `::`), multicast, and reserved ranges
(stdlib `ipaddress` `is_global`). **Every** address returned by `getaddrinfo`
(A and AAAA) must pass — one private record poisons the set. A literal-IP host
is judged the same way, and IPv4-mapped IPv6 (`::ffff:10.0.0.1`) is unwrapped
and judged as its embedded IPv4 address. A host that fails to resolve is
rejected (fail-closed) at create time and refused at send time.

**Escape hatch (local-first dev only):** `AP_WEBHOOKS_ALLOW_PRIVATE_TARGETS`
(default `false` = blocking) skips only the address checks — scheme/host shape
is still enforced — so the delivery path can be exercised against `127.0.0.1`
(e.g. a local sink) under `pnpm dev`. The committed `backend/.env.development`
sets it `true`; deployed envs must leave it at the safe default — and this is
enforced, not just documented: `app/main.py::lifespan` **refuses to boot** with
the flag on when `AP_DEBUG=false` (same fail-fast block as the secret-key /
billing-webhook guards).

**Residual risk:** the actual connection is opened by `httpx`, which performs
its own DNS lookup — a narrow rebinding window remains between the send-time
check and the socket connect. Pinning the checked IP for the connection itself
would close it; re-resolving immediately before send is the accepted fix per
issue #171, and the window is bounded to a single race per attempt.

### Management API (`/api/webhooks`)

Admin-gated (JWT + `require_roles(ROLE_ADMIN)`), control-plane. Every mutation
writes a PII-free audit row (`webhook_subscription.created/updated/deleted`,
`webhook_delivery.redelivered`).

| Method | Path | Notes |
|--------|------|-------|
| `POST` | `/api/webhooks` | Create a subscription. Validates http(s) URL + known event types + the [SSRF target guard](#target-url-ssrf-guard). Returns the `signing_secret` **once**. |
| `GET` | `/api/webhooks` | List this org's subscriptions (metadata only — never the full secret). |
| `PATCH` | `/api/webhooks/{id}` | Update name / target_url / event_types / active. A new `target_url` passes the same [SSRF target guard](#target-url-ssrf-guard). |
| `DELETE` | `/api/webhooks/{id}` | Delete (CASCADE removes its deliveries). 404 (opaque) for wrong-org. |
| `GET` | `/api/webhooks/deliveries` | List deliveries (org-scoped), filter `subscription_id` / `status`, paginated. |
| `POST` | `/api/webhooks/deliveries/{id}/redeliver` | Re-enqueue a `failed`/`dead` delivery (resets the counter) and attempt inline. `409` on an already-`delivered` row (would double-fire). |

### Config

| Variable | Default | Purpose |
|----------|---------|---------|
| `AP_WEBHOOKS_ENABLED` | `false` | Master switch for outbound webhooks — gates BOTH `emit_event` (OFF → silent no-op, no outbound HTTP) and the background retry/delivery sweep. OFF by default so a fresh clone / `pnpm dev` never makes outbound calls. Flip on in deployed envs. No secret — each subscription's signing secret is generated at create time and stored on the row. |
| `AP_WEBHOOKS_DELIVERY_INTERVAL_SECONDS` | `60` | Retry-sweep tick interval. |
| `AP_WEBHOOKS_ALLOW_PRIVATE_TARGETS` | `false` | SSRF-guard escape hatch — `true` lets a target URL resolve to a private/loopback address (local-first dev of the delivery path against `127.0.0.1` only; the committed `.env.development` sets it). The safe default blocks non-public targets at create/update AND again before every dispatch. Never enable in a deployed env. See [Target-URL SSRF guard](#target-url-ssrf-guard). |

## Deferred

These are explicitly **out of scope** for the current slices and tracked as
later roadmap work:

- **Full developer portal** — the published [OpenAPI spec + Swagger UI docs
  page](#published-openapi-spec) ship now; a richer hosted portal (guides,
  changelog, API-key self-service from the docs) is later work.
- **Write scopes + endpoints** — only `read` is minted today. The scope plumbing
  (`scopes` column + `require_api_scope`) is in place for it.
- **Frontend key-management UI** — the API-key mint / list / revoke / **usage**
  surface is fully built on the backend (`/api/api-keys/*`, admin-gated), but the
  SvelteKit admin screen to drive it (show the per-key usage chart from
  `GET /api/api-keys/{id}/usage`) is owned by the frontend track and not built
  here. Tracked as frontend roadmap work; the read API it will call is stable.
