# API Reference

FastAPI backend running on http://localhost:8000. Interactive docs available at:

- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc

## Required Headers

### Authentication

All endpoints except the explicit allowlist below require a Bearer token:

```
Authorization: Bearer <jwt_token>
```

**No-JWT endpoints** (use other mechanisms or are public):
- `/api/auth/login`, `/api/auth/mfa/challenge/email`, `/api/auth/mfa/verify` — pre-login flow, gated by password + challenge token
- `/api/auth/sso/*` — OIDC handshake
- `/api/scim/v2/*` — uses per-tenant SCIM bearer (validated inside the handler)
- `/api/signup/*`, `/api/public-config`, `/api/health` — public
- `/api/cards/webhook/{provider}`, `/api/erp/webhook/{erp_type}` — provider-signed webhooks

### Tenant Routing

All business endpoints (invoices, vendors, dashboard, payments, etc.) require:

```
X-Tenant-Slug: acme
```

The frontend sends this automatically based on the subdomain. When testing via curl, include both headers.

### RBAC

Every protected endpoint declares the roles that can call it via `Depends(require_roles(...))`. The four roles are `admin`, `ap_manager`, `ap_clerk`, `cfo`. Failed checks return `403 Forbidden`. The full permission matrix lives in [`docs/authentication.md`](../../docs/authentication.md) § RBAC; the **Roles** column on each table below summarises it for that endpoint.

Notation: `*` = any authenticated role, `admin/manager` = admin or ap_manager, `admin/manager/cfo` = the three non-clerk roles.

## Pagination

List endpoints share one contract, defined once in `app/api/pagination.py`
(the `pagination_params` dependency + `PageMeta` mixin / `paginated()` helper):

- Query params: **`page`** (1-based, default `1`) and **`page_size`** (default
  **20**, max **100** — values above 100 return `422`).
- Response envelope always carries **`items`**, **`total`** (full unpaged
  count), **`page`**, and **`page_size`**:

  ```json
  { "items": [ ... ], "total": 137, "page": 1, "page_size": 20 }
  ```

The frontend renders the first page then a "Load more" control that requests
`page=N+1` and appends. Paginated lists: `/invoices`, `/vendors`, `/payments`,
`/payments/runs/`, `/purchase-orders`, `/goods-receipts`, `/credit-memos`,
`/exceptions`, `/notifications`, `/cards`, `/workflows`, `/admin/users`, and the
supplier-portal `/portal/invoices` + `/portal/payments`.

**Intentionally not paginated** (bounded reference collections returned in
full): `/gl-accounts` (feeds the invoice GL dropdown, which needs every row)
and `/cards/rebates` (whose `total` is a money sum, not a row count).

## Auth

| Method  | Path                         | Roles | Description                                                        |
|---------|------------------------------|-------|--------------------------------------------------------------------|
| `POST`  | `/api/auth/login`            | (public) | Login with email/password. Returns either `TokenResponse` (`{access_token, must_change_password}`) or `MFAChallengeResponse` (`{mfa_required: true, mfa_challenge_token, methods, must_enroll}`) when MFA is in play. |
| `POST`  | `/api/auth/logout`           | * | Revoke current token via Redis blocklist                           |
| `GET`   | `/api/auth/me`               | * | Get current user (roles, `must_change_password`, `mfa_enabled`, `mfa_required_by_org`) |
| `PATCH` | `/api/auth/me`               | * | Update own name or password                                        |
| `POST`  | `/api/auth/change-password`  | * | Set a new password (clears `must_change_password`)                 |

Auth endpoints do **not** require the `X-Tenant-Slug` header.

## MFA

TOTP-based two-factor with email-OTP backup. Master switch `AP_MFA_ENABLED` (default `false` for local dev). See [`docs/authentication.md`](../../docs/authentication.md) § MFA for the full flow.

| Method | Path                              | Roles | Description |
|--------|-----------------------------------|-------|-------------|
| `POST` | `/api/auth/mfa/challenge/email`   | (challenge token) | Body `{challenge_token}`. Generates + emails a 6-digit OTP. Returns 204. |
| `POST` | `/api/auth/mfa/verify`            | (challenge token) | Body `{challenge_token, code, method}` (`method` ∈ `totp`/`email`). Returns `TokenResponse`. |
| `POST` | `/api/auth/mfa/enroll`            | * | Mints a TOTP secret + QR. Returns `{secret, provisioning_uri, qr_code_data_url}`. |
| `POST` | `/api/auth/mfa/enroll/verify`     | * | Body `{code}`. Confirms enrollment, flips `mfa_enabled` true. |
| `POST` | `/api/auth/mfa/disable`           | * | Body `{password}`. Re-confirms password, turns MFA off. Blocked when org enforces MFA. |

The challenge endpoints don't take a JWT — they're authenticated by the short-lived challenge token returned from `/api/auth/login`.

## SSO (OIDC — Okta + Microsoft Entra)

| Method  | Path                          | Roles | Description |
|---------|-------------------------------|-------|-------------|
| `GET`   | `/api/auth/sso/config`        | (public) | `?slug=<s>` — returns `{enabled, provider}` for the login button. Never leaks secrets. |
| `GET`   | `/api/auth/sso/authorize`     | (public) | `?slug=<s>` — 302 to the IdP's authorization endpoint. Mints state+nonce in Redis. |
| `POST`  | `/api/auth/sso/callback`      | (public) | `{code, state}` → `{access_token, must_change_password, tenant_slug}`. JIT-provisions the user. |

See [`docs/authentication.md`](../../docs/authentication.md) § SSO for the full handshake. Note: SSO sign-in does not trigger our MFA challenge — IdPs handle their own MFA.

## SCIM 2.0 (user provisioning from the IdP)

All `/api/scim/v2/*` endpoints authenticate via the **per-tenant SCIM bearer** (not a user JWT). Tenant is resolved by sha256-matching the bearer against `Organization.settings.sso.scim_bearer_hash`.

| Method   | Path                                | Description |
|----------|-------------------------------------|-------------|
| `GET`    | `/api/scim/v2/ServiceProviderConfig` | Discovery doc — Okta/Entra probe this first. |
| `GET`    | `/api/scim/v2/Schemas/{id}`         | Minimal User schema — Entra probes this before syncing. |
| `GET`    | `/api/scim/v2/Users`                | List + paginate. Filters: `userName eq`, `emails eq`, `externalId eq`, `active eq`. |
| `GET`    | `/api/scim/v2/Users/{id}`           | Fetch one user. |
| `POST`   | `/api/scim/v2/Users`                | Create. 409 `uniqueness` on duplicate userName. |
| `PATCH`  | `/api/scim/v2/Users/{id}`           | Partial update — supports the ops Okta + Entra send. |
| `DELETE` | `/api/scim/v2/Users/{id}`           | **Soft delete** — sets `is_active=false`. Preserves audit trail. |

## Signup (anonymous, pre-tenant)

| Method  | Path                      | Description |
|---------|---------------------------|-------------|
| `GET`   | `/api/signup/slug-check`  | `?slug=<s>` — availability check for the signup form's inline UX |
| `POST`  | `/api/signup/start`       | Rate-limited + captcha-verified. Creates `email_verifications` row and sends verification email. |
| `POST`  | `/api/signup/complete`    | Consumes token, provisions tenant (DB + org + admin user), sends welcome email. |

See [`docs/self-service-signup.md`](../../docs/self-service-signup.md) for the full flow.

## Public config

| Method | Path                  | Description |
|--------|-----------------------|-------------|
| `GET`  | `/api/public-config`  | Non-secret config (hcaptcha sitekey, tenant URL template) for the signup form |

## Organization

| Method | Path                              | Roles  | Description |
|--------|-----------------------------------|--------|-------------|
| `GET`  | `/api/organization`               | *      | Get the current tenant's org settings (company, invoice defaults, ERP, extraction, cards, mfa, sso) |
| `PATCH` | `/api/organization`              | admin  | Patch settings. Body `{name?, settings?}` — `settings` is merged into existing JSONB. |
| `POST` | `/api/organization/test-erp`      | admin  | Test ERP connection (uses request body if provided, otherwise saved config) |
| `POST` | `/api/organization/test-extraction` | admin  | Test AI extraction provider connection |
| `POST` | `/api/organization/sso/scim-token` | admin  | Mint (or rotate) the per-tenant SCIM bearer. Returns `{token, bearer_hash_prefix}` ONCE. |

## Invoices

| Method   | Path                                | Roles | Description |
|----------|-------------------------------------|-------|-------------|
| `GET`    | `/api/invoices`                     | *     | List invoices (paginated, filterable). Returns `priors_summary` and `po_match` per row when applicable. |
| `GET`    | `/api/invoices/counts`              | *     | Per-status tallies for the list-page filter chips — `{counts: {status: n}, total}` via a server-side GROUP BY over the whole tenant (accurate past the page window). |
| `GET`    | `/api/invoices/{id}`                | *     | Get single invoice — includes the latest `po_match` JSONB result and any `warnings` |
| `GET`    | `/api/invoices/{id}/priors`         | *     | Priors metadata from latest extraction (vendor cache + RAG neighbors). See [`ai-extraction.md`](ai-extraction.md). |
| `GET`    | `/api/invoices/{id}/line-items`     | *     | Get invoice line items |
| `PUT`    | `/api/invoices/{id}/line-items`     | admin/manager/cfo | Replace all line items |
| `POST`   | `/api/invoices`                     | admin/manager/cfo | Create invoice |
| `PATCH`  | `/api/invoices/{id}`                | admin/manager/cfo | Update invoice |
| `DELETE` | `/api/invoices/{id}`                | admin/manager/cfo | Delete invoice |
| `POST`   | `/api/invoices/bulk/delete`         | admin/manager/cfo | Bulk delete |
| `POST`   | `/api/invoices/bulk/status`         | admin/manager/cfo | Bulk status change |
| `POST`   | `/api/invoices/bulk/export`         | *     | Bulk export (CSV/JSON/XML) |
| `POST`   | `/api/invoices/bulk-recode-gl`      | admin | Bulk GL re-code via vendor priors (+ optional AI fallback). Defaults to dry-run. See [`ai-extraction.md`](ai-extraction.md) § Bulk re-coding. |

**Query parameters for `GET /api/invoices`:** `page`, `page_size`, `status` (comma-sep), `vendor`, `invoice_number`, `po_number`, `description`, `amount_min`, `amount_max`, `due_date_from`, `due_date_to`, `search`.

## Workflow Actions (per invoice)

| Method | Path                              | Roles | Description |
|--------|-----------------------------------|-------|-------------|
| `POST` | `/api/invoices/upload`            | admin/manager/cfo | Multipart file upload. Creates invoice + optionally triggers extraction. |
| `POST` | `/api/invoices/{id}/extract`      | admin/manager/cfo | Re-trigger extraction on `new` or `failed` invoices |
| `POST` | `/api/invoices/{id}/reset-extraction` | admin/manager/cfo | Reset stuck `pending` extraction back to `new` |
| `POST` | `/api/invoices/{id}/assign`       | admin/manager | Assign reviewer (body `{user_id}`) |
| `POST` | `/api/invoices/{id}/approve`      | admin/manager/cfo | Approve. Body may include field corrections. |
| `POST` | `/api/invoices/{id}/reject`       | admin/manager/cfo | Reject (body `{reason}`) |
| `POST` | `/api/invoices/{id}/resubmit`     | admin/manager/cfo | Resubmit after rejection |
| `POST` | `/api/invoices/{id}/send-to-erp`  | admin/manager/cfo | Transition to `sending_to_erp` and dispatch (local thread or SQS) |
| `POST` | `/api/invoices/{id}/retry-erp`    | admin/manager/cfo | Retry a failed ERP push |
| `POST` | `/api/invoices/{id}/complete`     | admin/manager/cfo | Advance to the next workflow step based on the snapshot |
| `GET`  | `/api/invoices/{id}/audit-log`    | * | Full audit trail (with actor names) |
| `GET`  | `/api/invoices/{id}/workflow`     | * | Workflow instance + steps |
| `GET`  | `/api/invoices/{id}/extraction`   | * | AI extraction results |
| `GET`  | `/api/invoices/{id}/export`       | * | Export single invoice |
| `GET`  | `/api/invoices/file/{file_key}`   | * | Proxy invoice file from S3 |

## Workflow Definitions

| Method   | Path                              | Roles | Description |
|----------|-----------------------------------|-------|-------------|
| `GET`    | `/api/workflows/active/steps`     | * | Active steps + approval config (used in invoice modal) |
| `GET`    | `/api/workflows`                  | * | List workflow definitions |
| `GET`    | `/api/workflows/{id}`             | * | Get one |
| `POST`   | `/api/workflows`                  | admin | Create |
| `PATCH`  | `/api/workflows/{id}`             | admin | Update (activating one auto-deactivates the others) |
| `DELETE` | `/api/workflows/{id}`             | admin | Delete (not the default) |

## Vendors

| Method   | Path                            | Roles | Description |
|----------|---------------------------------|-------|-------------|
| `GET`    | `/api/vendors`                  | admin/manager/cfo | List (paginated, filterable by `status`, `source`, `search`) |
| `GET`    | `/api/vendors/{id}`             | admin/manager/cfo | Get one |
| `POST`   | `/api/vendors`                  | admin/manager | Create — auto-marked `active` and `verified_by` set |
| `PATCH`  | `/api/vendors/{id}`             | admin/manager | Update |
| `DELETE` | `/api/vendors/{id}`             | admin/manager | Delete |
| `POST`   | `/api/vendors/{id}/verify`      | admin/manager | Promote `unverified` → `active` |
| `POST`   | `/api/vendors/{id}/reject`      | admin/manager | Mark `rejected` |
| `POST`   | `/api/vendors/sync-erp`         | admin/manager | Pull vendors from connected ERP |

## Purchase Orders

| Method | Path                           | Roles | Description |
|--------|--------------------------------|-------|-------------|
| `GET`  | `/api/purchase-orders`         | * | List POs (filterable by `status`, `vendor_id`, `search`) |
| `POST` | `/api/purchase-orders/sync-erp` | admin/manager | Pull POs from connected ERP |

## GL Accounts

| Method | Path                          | Roles | Description |
|--------|-------------------------------|-------|-------------|
| `GET`  | `/api/gl-accounts`            | * | List Chart of Accounts (filterable, `active_only=true` default) |
| `POST` | `/api/gl-accounts`            | admin/manager | Create a GL account |
| `POST` | `/api/gl-accounts/sync-erp`   | admin/manager | Pull Chart of Accounts from ERP |

## Payments

All payment endpoints require `admin/manager/cfo`.

| Method | Path                          | Description |
|--------|-------------------------------|-------------|
| `GET`  | `/api/payments`               | List payments (filterable by `status`, `method`, `invoice_id`, `search`, amount range) |
| `GET`  | `/api/payments/{id}`          | Get one payment |
| `POST` | `/api/payments`               | Create payment for an invoice |
| `GET`  | `/api/payments/queue`         | Approved invoices sorted by due date |
| `GET`  | `/api/payments/summary`       | Totals: paid, pending, queue count, rebates earned |
| `GET`  | `/api/payments/runs/`         | List payment runs |
| `POST` | `/api/payments/runs`          | Create a payment run |
| `GET`  | `/api/payments/runs/{id}`     | Get payment run details |
| `POST` | `/api/payments/runs/{id}/execute` | Execute a draft payment run via the configured processor; response includes `payments_completed` / `payments_in_flight` / `payments_failed` rollup |
| `POST` | `/api/payments/webhook/{tenant_slug}/{provider}` | (provider-signed) — payment-status webhook from Modern Treasury / similar. Tenant in URL path, signature verified per adapter. |

## Virtual Cards

All card endpoints require `admin/manager/cfo` (except the webhook).

| Method | Path                          | Description |
|--------|-------------------------------|-------------|
| `GET`  | `/api/cards`                  | List virtual cards |
| `GET`  | `/api/cards/dashboard`        | Card spend + rebate KPIs |
| `POST` | `/api/cards/generate`         | Generate one or more cards (returns the new list) |
| `GET`  | `/api/cards/{id}/details`     | Card details (includes PAN — Lithic/Nium fetched on demand) |
| `POST` | `/api/cards/{id}/cancel`      | Cancel/void a card |
| `GET`  | `/api/cards/rebates`          | List rebate rows (billing) |
| `POST` | `/api/cards/webhook/{provider}` | (provider-signed) — Lithic/Nium event webhook |

## Exceptions

All exception endpoints require `admin/manager`.

| Method | Path                              | Description |
|--------|-----------------------------------|-------------|
| `GET`  | `/api/exceptions`                 | List flagged invoices (filter by `status`, `type`, `severity`) |
| `GET`  | `/api/exceptions/summary`         | Counts by status + open-by-type breakdown |
| `POST` | `/api/exceptions/{id}/resolve`    | Body `{resolution, action}` (`resolve`/`escalate`/`dismiss`) |

## Notifications

Per-user, any authenticated role. List + mark endpoints are tenant-scoped to
`recipient_user_id == current user`; a notification belonging to another user
returns the same `404` as a missing one (no enumeration). Preferences are
user-global (control plane). See `notifications.md`.

| Method  | Path                                  | Description |
|---------|---------------------------------------|-------------|
| `GET`   | `/api/notifications`                  | List current user's notifications (`?unread_only=&page=&page_size=`); envelope adds `unread` |
| `GET`   | `/api/notifications/unread-count`     | `{unread}` for the sidebar badge |
| `POST`  | `/api/notifications/{id}/read`        | Mark one read (naturally idempotent); `404` if not owned |
| `POST`  | `/api/notifications/read-all`         | Mark all current-user unread → read; returns `{updated}` |
| `GET`   | `/api/notifications/preferences`      | Per-event `{email, in_app}` map (defaults if unset) |
| `PATCH` | `/api/notifications/preferences`      | Partial update; unspecified events unchanged |

## Dashboard

| Method | Path              | Roles | Description |
|--------|-------------------|-------|-------------|
| `GET`  | `/api/dashboard`  | *     | Aggregated KPIs (status counts, aging buckets, top vendors, etc.) |

## Admin (User Management)

All require `admin`.

| Method   | Path                     | Description |
|----------|--------------------------|-------------|
| `GET`    | `/api/admin/users`       | List all users in the organization |
| `GET`    | `/api/admin/roles`       | List all available roles |
| `POST`   | `/api/admin/users`       | Create user (returns temp password) |
| `PATCH`  | `/api/admin/users/{id}`  | Update user (name, email, roles, password, active) |
| `DELETE` | `/api/admin/users/{id}`  | Permanently delete a user |

## ERP Inbound Webhook

| Method | Path                              | Description |
|--------|-----------------------------------|-------------|
| `POST` | `/api/erp/webhook/{erp_type}`     | (provider-signed) — inbound ERP status updates |

## Goods Receipts

Used by 3-way matching. `admin` / `ap_manager` / `ap_clerk`.

| Method | Path                                    | Description |
|--------|-----------------------------------------|-------------|
| `GET`  | `/api/goods-receipts`                   | List goods receipts (paginated, filters by `vendor_id`, `po_id`, `status`) |
| `GET`  | `/api/goods-receipts/{id}`              | Single GR with line items |

## Credit Memos

| Method | Path                              | Roles | Description |
|--------|-----------------------------------|-------|-------------|
| `GET`  | `/api/credit-memos`                | *     | List credit memos (paginated) |
| `POST` | `/api/credit-memos`                | admin, ap_manager, ap_clerk | Create a credit memo against a vendor / original invoice |
| `PATCH`| `/api/credit-memos/{id}`           | admin, ap_manager, ap_clerk | Update memo (amount, status) |
| `POST` | `/api/credit-memos/{id}/apply`     | admin, ap_manager, ap_clerk | Apply an open credit memo against a payable |

## Tax / 1099

| Method | Path                                    | Roles | Description |
|--------|-----------------------------------------|-------|-------------|
| `GET`  | `/api/tax/vendors/{vendor_id}`           | admin, ap_manager, cfo | Vendor's 1099 status (W-9 received, classification, YTD totals) |
| `POST` | `/api/tax/vendors/{vendor_id}/w9`        | admin, ap_manager | Upload signed W-9 PDF + mark vendor 1099-eligible |
| `GET`  | `/api/tax/1099/{year}`                   | admin, ap_manager, cfo | YTD 1099 summary across all eligible vendors |

## Analytics

CFO-grade aggregates beyond the basic dashboard, plus CSV/PDF export and recurring report scheduling.

| Method | Path                                       | Roles | Description |
|--------|--------------------------------------------|-------|-------------|
| `GET`  | `/api/analytics/spend`                      | cfo, admin, ap_manager | Aggregated spend by GL / vendor / cost center / time bucket |
| `GET`  | `/api/analytics/dpo`                        | cfo, admin | Days-payable-outstanding rolling history |
| `GET`  | `/api/analytics/export.csv`                 | cfo, admin, ap_manager | Streaming CSV of the current filtered analytics view |
| `GET`  | `/api/analytics/scheduled-reports`          | cfo, admin | List scheduled-report definitions |
| `POST` | `/api/analytics/scheduled-reports`          | cfo, admin | Create a recurring report (cron + recipients + format) |
| `PATCH`| `/api/analytics/scheduled-reports/{id}`     | cfo, admin | Update a scheduled report |
| `DELETE`|`/api/analytics/scheduled-reports/{id}`     | cfo, admin | Delete a scheduled report |

## Email Intake

Inbound email turns provider attachments into invoices. The public webhook is provider-signed (HMAC over the body); the admin endpoints manage the per-tenant intake address.

| Method | Path                                         | Roles | Description |
|--------|----------------------------------------------|-------|-------------|
| `POST` | `/api/email-intake/inbound/{provider}`       | (provider-signed) | Webhook entrypoint. Returns 204 silently on every rejection (bad signature / unknown provider / unparsable payload) to avoid enumeration. |
| `GET`  | `/api/organization/email-intake`             | admin | Show the current intake address + enabled flag |
| `POST` | `/api/organization/email-intake/rotate-token`| admin | Generate a new token; the old address stops accepting email immediately |

## Health

| Method | Path           | Description |
|--------|----------------|-------------|
| `GET`  | `/api/health`  | Health check |

## Example: full curl flow (MFA off)

```bash
# Login (no tenant header needed). When AP_MFA_ENABLED=true and the user is
# enrolled, the response is an MFAChallengeResponse instead — see
# docs/authentication.md § MFA for the verify flow.
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@acme.com","password":"demo"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# List invoices (requires both auth + tenant headers)
curl http://localhost:8000/api/invoices \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Slug: acme"

# Get dashboard KPIs
curl http://localhost:8000/api/dashboard \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Slug: acme"
```

See [`docs/authentication.md`](../../docs/authentication.md) for full auth + RBAC details and [`docs/multi-tenancy.md`](../../docs/multi-tenancy.md) for tenant routing.
