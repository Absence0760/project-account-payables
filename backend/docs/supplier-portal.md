# Supplier Portal

Self-service surface for vendors to submit invoices and track payment status.
Runs on the tenant's subdomain (`acme.localhost:7777/portal`) but uses a
separate authentication tree so vendor credentials can't cross into the AP
application.

## Why it's separate from employee auth

- `User` lives in the control plane and carries RBAC roles (`admin`,
  `ap_manager`, `ap_clerk`, `cfo`). A vendor login must never acquire one of
  these roles.
- `VendorUser` lives in the tenant DB alongside `Vendor`. Each row is a
  supplier-portal credential belonging to exactly one `vendor_id`.
- JWTs carry a `typ` claim (`user` vs `vendor`). `get_current_user` rejects
  `typ=vendor`; `get_current_vendor_user` rejects anything else. A bug in
  one dependency tree cannot leak into the other.

## Data model

`vendor_users` (tenant DB, migration 0009):

| Column                 | Type          | Notes                                                  |
|------------------------|---------------|--------------------------------------------------------|
| `id`                   | UUID          | PK                                                     |
| `vendor_id`            | UUID          | FK → `vendors.id` ON DELETE CASCADE, indexed           |
| `email`                | VARCHAR(320)  | UNIQUE — login identifier                              |
| `full_name`            | VARCHAR(255)  |                                                        |
| `hashed_password`      | VARCHAR(255)  | bcrypt                                                 |
| `is_active`            | BOOLEAN       | Soft-disable without deleting                          |
| `must_change_password` | BOOLEAN       | Set on invite; cleared on first successful change      |
| `last_login_at`        | TIMESTAMPTZ   | Updated on each successful login                       |
| `mfa_secret`           | VARCHAR(64)   | base32 TOTP seed (migration 0053); held pending until verified |
| `mfa_enabled`          | BOOLEAN       | True once the vendor verifies a code; gates the login challenge |
| `mfa_enrolled_at`      | TIMESTAMPTZ   | Stamped on successful enrollment                       |
| `created_at` / `updated_at` | TIMESTAMPTZ | Standard TimestampMixin                              |

The three `mfa_*` columns (migration `0053_vendor_mfa`) mirror the `User` MFA
columns exactly. See **MFA (two-factor)** below.

## Endpoints

All under `/api/portal/*`. Tenant resolved via the usual `X-Tenant-Slug`
header. RBAC coverage gate in `tests/test_rbac.py` asserts every endpoint
uses `get_current_vendor_user` (except `/portal/auth/login`,
`/portal/auth/logout`, and `/portal/auth/mfa/challenge`, which are listed in
`NO_AUTH_REQUIRED`).

### Auth (`portal_auth.py`)

| Method | Path                             | Notes                                                                         |
|--------|----------------------------------|-------------------------------------------------------------------------------|
| POST   | `/portal/auth/login`             | email + password → `{access_token, …}` OR (MFA-enrolled) an MFA challenge     |
| POST   | `/portal/auth/logout`            | Adds `jti` to the shared Redis blocklist                                      |
| GET    | `/portal/auth/me`                | Returns the vendor-user + vendor summary (incl. `mfa_enabled`)               |
| POST   | `/portal/auth/change-password`   | Used by the forced first-login rotation and voluntary rotations               |
| POST   | `/portal/auth/mfa/challenge`     | **Public** — trade the login-issued challenge token + a code (`method` totp\|email) for an access token |
| POST   | `/portal/auth/mfa/challenge/email` | **Public** — email the on-demand OTP backup code to the enrolled vendor; 204-silent (no enumeration) |
| POST   | `/portal/auth/mfa/enroll`        | Mint a TOTP secret + QR (pending until verified)                              |
| POST   | `/portal/auth/mfa/verify`        | Verify a code to activate MFA (`mfa_enabled=true`)                            |
| POST   | `/portal/auth/mfa/disable`       | Turn MFA off — re-verifies a current code first                              |

### MFA (two-factor) — `portal_auth.py`

TOTP MFA for supplier-portal vendor users (roadmap Priority 6), mirroring the
employee flow (`docs/authentication.md` § MFA) but scoped to `VendorUser` + the
`typ=vendor` JWT. Reuses the shared TOTP primitives in `services/mfa.py` —
secret generation, provisioning URI, QR, and `verify_totp` (±1 step skew).

- **Master switch.** `AP_MFA_ENABLED` (default `false` for local dev) gates the
  whole feature, exactly like employee MFA. With it off, an enrolled vendor
  still logs in with just a password (no challenge). MFA is **opt-in per vendor
  user**; there is no org-wide enforcement for vendors yet.
- **Enrollment.** `POST /mfa/enroll` mints (or re-issues) a secret + QR data URL
  and returns the secret in plaintext (manual entry); it's held pending until
  `POST /mfa/verify` confirms a valid code and flips `mfa_enabled=true`. The
  secret is never echoed back after activation. `POST /mfa/disable` re-verifies
  a current code before clearing the columns.
- **Login challenge.** When `AP_MFA_ENABLED` is on and the vendor is enrolled,
  `POST /login` returns `PortalMFAChallengeResponse` (`{mfa_required, mfa_challenge_token,
  methods: ["totp", "email"]}`) instead of the access token. The browser submits the code
  to `POST /mfa/challenge` with `method` (`totp` default | `email`), which verifies and
  mints the real vendor access token.
- **Email-OTP backup factor.** Mirrors the employee email-OTP backup
  (`docs/authentication.md` § MFA → Email OTP) for the vendor when they've lost
  their authenticator. `POST /mfa/challenge/email` (public, gated by the same
  `vendor_mfa_challenge` token) issues a 6-digit code via
  `services/mfa.issue_vendor_email_otp` and emails it through the configured
  outbound email adapter (`console` in local dev — local-first, no cloud). The
  code's SHA-256 lives in Redis under a **distinct** keyspace
  (`mfa:vendor_email_otp:<vendor_user_id>`, separate from the employee
  `mfa:email_otp:` prefix) with the `AP_MFA_EMAIL_OTP_TTL_SECONDS` TTL,
  single-use. The vendor then submits it to `POST /mfa/challenge` with
  `method="email"`. The backup is gated on the vendor having actually enrolled
  TOTP (`mfa_enabled` + `mfa_secret`) — it's a fallback to the authenticator, not
  an independent enrollment path. The request endpoint is 204-silent for an
  unenrolled / unknown / inactive account (no enumeration); the OTP and the
  vendor email never appear in logs. No migration — Redis-only, exactly like the
  employee backup.
- **Token-type isolation (cross-auth-leak guard).** The challenge token carries
  `typ=vendor_mfa_challenge` — distinct from both the employee challenge
  (`mfa_challenge`) and the vendor access token (`vendor`). So a challenge token
  can never resolve as an access token through `get_current_vendor_user`, an
  employee challenge can never hit `/portal/auth/mfa/challenge[/email]`, and a
  vendor access token can never satisfy the challenge endpoint.
  `services/mfa.create_vendor_challenge_token` / `decode_vendor_challenge_token`
  enforce the `typ` symmetrically. The email-OTP keyspace is likewise isolated
  from the employee one, so the same UUID value can't collide across surfaces.

### Invoices + payments (`portal.py`)

| Method | Path                             | Notes                                                                         |
|--------|----------------------------------|-------------------------------------------------------------------------------|
| GET    | `/portal/invoices`               | Vendor-scoped list                                                            |
| GET    | `/portal/invoices/{id}`          | 404 for "doesn't exist" AND "belongs to another vendor" (no ID enumeration)   |
| POST   | `/portal/invoices`               | Multipart PDF upload — routes into the same extraction pipeline as AP uploads |
| GET    | `/portal/payments`               | Payments joined to Invoice to filter on `vendor_id`                           |
| GET    | `/portal/payments/{id}/remittance` | Vendor-scoped remittance PDF; ownership via `Payment→Invoice.vendor_id`; 404 on a foreign payment |

### Purchase orders + PO flip (`portal.py`)

| Method | Path                                  | Notes                                                                        |
|--------|---------------------------------------|------------------------------------------------------------------------------|
| GET    | `/portal/purchase-orders`             | Vendor-scoped PO list (with line-item counts)                                |
| GET    | `/portal/purchase-orders/{id}`        | PO detail + line items; 404 for a foreign PO                                 |
| POST   | `/portal/purchase-orders/{id}/flip`   | **PO flip** — create an invoice pre-populated from a vendor-owned PO         |

### Company self-service (`portal.py`)

| Method | Path                                  | Notes                                                                        |
|--------|---------------------------------------|------------------------------------------------------------------------------|
| GET    | `/portal/company`                     | Current company info; bank/tax **masked**; surfaces any pending change       |
| PATCH  | `/portal/company`                     | Update phone/address/email — **applies live**                               |
| POST   | `/portal/company/bank-change`         | **Stages** a `bank_details` change (NOT applied); 202; deduped on pending    |
| POST   | `/portal/company/tax-id-change`       | **Stages** a `tax_id` change (NOT applied); 202                             |
| GET    | `/portal/company/change-requests`     | This vendor's own change requests + statuses                                |
| GET    | `/portal/company/tax-form`            | Whether a W-9/W-8 is on file (PII-free: on-file flag + form type + received date) |
| POST   | `/portal/company/tax-form`            | Multipart upload of the vendor's own signed W-9 (US) / W-8 (foreign) — **applies live** |
| GET    | `/portal/company/tax-form/file`       | Vendor-scoped download proxy of their own uploaded form; 404 on foreign/missing |

#### W-9 / W-8 tax-form upload (self-service)

US suppliers file a **W-9**, foreign suppliers a **W-8** (BEN / BEN-E). The
vendor uploads their own signed form from the portal; AP uses it for 1099 /
withholding compliance. Design notes:

- **Reuses the existing vendor columns.** The form writes
  `Vendor.w9_file_key` + `Vendor.w9_received_date` on the caller's **own**
  vendor row — the same columns the AP-side `POST /api/tax/vendors/{id}/w9`
  upload writes. **No new vendor column, no migration.**
- **Form type without a column.** W-9 vs W-8 is encoded in the S3 key segment
  (`<org>/tax-forms/<vendor>/<form_type>/<file>`, via
  `storage.upload_tax_form_file`) and recovered on read by
  `portal._tax_form_type_from_key`. A W-9 stored via the AP-side path (key
  prefix `<org>/w9/<vendor>/…`, no form-type segment) reads back as `w9`.
- **Applies live, unlike bank/tax-ID changes.** A tax form is a document, not
  a money-routing target, so there's no AP-approval gate. (AP still verifies
  the TIN separately via `POST /api/tax/vendors/{id}/tin-verify`.)
- **Vendor-scoped + cross-tenant gated.** Every handler resolves the vendor by
  `vu.vendor_id` only (cross-vendor → 404, not 403). The download proxy reads
  the key from the vendor row (never the request) **and** cross-checks the
  key's leading org segment against the vendor's org — wrong-org / missing both
  404, no enumeration. Content-type is gated by `ALLOWED_CONTENT_TYPES` (PDF /
  PNG / JPEG / TIFF / XML) at the storage boundary.
- **PII-out-of-logs.** The `vendor.tax_form_uploaded_by_vendor` audit row
  carries only the form type + filename + `vendor_user_id` — never the tax ID.
  The `GET` response carries an on-file boolean + form type + received date,
  never the tax ID or the document bytes.

### Early-payment discount offers (`portal.py`)

Vendor-facing view of the dynamic-discounting offers the AP team extends to the
supplier (see `dynamic-discounting.md`). Every offer the vendor sees is scoped
to their own `vendor_id` **or** to one of their own invoices — a vendor can
never see another vendor's offers (cross-vendor / unknown id → 404, never 403).

| Method | Path                                       | Notes                                                                                  |
|--------|--------------------------------------------|----------------------------------------------------------------------------------------|
| GET    | `/portal/discount-offers`                  | Vendor-scoped offer list; per-tier savings + best capturable tier today; `?status=` filter |
| POST   | `/portal/discount-offers/{id}/accept`      | Accept the discount (`tier_days` or best tier today). **Never moves money** — flips `offered → accepted` only (reuses `discount_offers.accept_offer`); CFO-gated payment run still funds it. Idempotent: re-accepting a non-`offered` offer is a `409`; foreign/unknown id `404` |
| POST   | `/portal/discount-offers/{id}/decline`     | Decline the discount (reuses `discount_offers.decline_offer`); `409` if no longer `offered` |

Both money math (savings) and the lifecycle mutators are the **same pure
`services/discount_offers.py` primitives** the AP `/api/discounts` router uses —
the vendor side never duplicates the Decimal math. Audit rows
(`discount_offer.accepted_by_vendor` / `.declined_by_vendor`) are PII-free
(`actor_id=None`, no values, only the chosen tier). No migration — the
`DiscountOffer` table already exists (migration 0043).

### Notification preferences (`portal.py`)

| Method | Path                                  | Notes                                                                        |
|--------|---------------------------------------|------------------------------------------------------------------------------|
| GET    | `/portal/notification-preferences`    | Calling vendor user's effective email prefs (`email_on_payment`, `email_on_rejection`); defaults on |
| PATCH  | `/portal/notification-preferences`    | Partial update of the **caller's own** `VendorUser` prefs; audited; unspecified fields unchanged |

A vendor portal user controls, per user, whether they get **emailed** when one
of *their own* invoices is **paid** or **rejected**. Stored on
`vendor_users.notification_prefs` (JSONB, migration 0052), keyed by the same
`invoice_paid` / `invoice_rejected` event strings the rest of the system uses
(`{event_type: {"email": bool}}`), mapped to the vendor-friendly
`email_on_payment` / `email_on_rejection` shape by
`services/vendor_notifications.py`. Opt-out, not opt-in: an unset event defaults
to **on**. Vendors have no in-app notification center, so only the `email`
channel exists. The PATCH is scoped to the caller's own row
(`get_current_vendor_user`) — a vendor user can never touch another's prefs —
and writes a PII-free `vendor_user.notification_prefs_updated` audit row (field
names only). Actual email dispatch is wired into the `transition_invoice`
chokepoint; see [notifications.md](notifications.md) § Vendor recipients.

### Admin invite + change-request approval (`vendors.py`)

| Method | Path                                                 | Notes                                 |
|--------|------------------------------------------------------|---------------------------------------|
| GET    | `/vendors/{id}/portal-users`                         | List portal users for a vendor        |
| POST   | `/vendors/{id}/portal-users`                         | Invite — temp password + welcome email |
| DELETE | `/vendors/{id}/portal-users/{vendor_user_id}`        | Remove a portal user                  |
| GET    | `/vendors/change-requests`                           | Pending change-request queue (admin, ap_manager); value masked |
| GET    | `/vendors/{id}/change-requests`                      | One vendor's requests (admin, ap_manager, cfo); value revealed |
| POST   | `/vendors/change-requests/{id}/approve`              | Apply the staged change to the vendor (admin, ap_manager); `FOR UPDATE` locked, exactly-once |
| POST   | `/vendors/change-requests/{id}/reject`               | Mark rejected; never touches the vendor (admin, ap_manager) |

## Self-service change-request gate (fraud prevention)

Bank-detail and tax-ID changes from the portal do **not** apply live — they
stage a `vendor_change_requests` row (`status=pending`) and leave the `Vendor`
row untouched. AP approval is what applies the change:

- **bank_details** → on approve, merged into `Vendor.bank_details` via
  `_merge_bank_details` (the same merge the AP PATCH uses).
- **tax_id** → on approve, sets `Vendor.tax_id` and clears `tin_verified_at`
  (a re-keyed TIN must be re-verified).

The approve handler locks the request row `FOR UPDATE` and 409s if it's already
resolved, so the apply is exactly-once. Every stage / approve / reject writes an
append-only audit row; the audit `details` (and the staging table) carry only
`{change_type, request_id, last4}` — never the full account number or tax ID.
A vendor can hold at most one pending request per `change_type` (dedupe → 409).

This is the fraud control: a redirected bank account has **zero effect on where
money goes** until an AP admin explicitly approves it.

## Security invariants

- **Vendor scoping:** every portal handler filters on
  `Invoice.vendor_id == vu.vendor_id`. `test_supplier_portal.py` asserts
  this at the source level so a regression can't silently broaden a query.
- **404, not 403, on cross-vendor probe:** distinguishes nothing from a
  missing invoice — so the portal can't be used to enumerate invoice IDs
  across tenants' vendors.
- **Token blocklist:** shared with employee auth (Redis `jti`), so a
  compromised portal token can be revoked through the same mechanism.
- **`typ` claim enforced symmetrically:** employee JWT → 401 on portal,
  vendor JWT → 401 on AP — verified in `test_supplier_portal.py`.

## Invoice submission flow

1. Vendor uploads a PDF.
2. An `Invoice` row is created with `vendor_id` + `vendor_name` pre-filled
   from the JWT's `ven` claim (no free-form vendor input — the portal user
   only represents their own vendor).
3. File is uploaded to S3 under `{org_id}/{invoice_id}/{filename}`.
4. A `WorkflowInstance` is created using the active workflow definition.
5. If the extraction step is enabled, the invoice transitions to `pending`
   and extraction is dispatched via the normal `dispatch_extraction` path.
6. An audit log entry is written with `action="invoice.submitted_by_vendor"`
   and `details.source="supplier_portal"` so AP teams can see provenance.

The `actor_id` on the audit log is `NULL` for portal uploads — the actor is
a `VendorUser`, not a `User`, and the two namespaces are deliberately
separate. The `vendor_user_id` is carried in `details` instead.

## Frontend

Separate auth + HTTP surface:

- `$lib/portalApi.ts` — parallel to `$lib/api.ts`, uses `portal_auth_token`
  in localStorage so the AP app and portal can coexist in the same browser.
- `$lib/stores/portalAuth.svelte.ts` — parallel to `auth.svelte.ts`.
- `/portal/+layout.svelte` — portal shell (header, nav, logout). Root
  `+layout.svelte` bypasses all AP-auth logic when the path starts with
  `/portal`.

Routes:

| Route                         | Purpose                                                |
|-------------------------------|--------------------------------------------------------|
| `/portal/login`               | Sign-in form + MFA second-factor step (TOTP, with a "use email code instead" backup) |
| `/portal/change-password`     | Forced first-login rotation                            |
| `/portal/invoices`            | List + upload                                          |
| `/portal/purchase-orders`     | PO list + per-row "Create invoice" (flip)              |
| `/portal/payments`            | Payment history + per-row "Download remittance"        |
| `/portal/discount-offers`     | Early-payment discount offers — accept / decline       |
| `/portal/company`             | Contact (live) + bank/tax change requests (staged) + W-9/W-8 tax-form upload/download (live) + Security (MFA enroll/disable) |
| `/portal/notifications`       | Email preferences (paid / rejected) — vendor-controlled |

The portal company form makes the approval-gating visible: bank/tax changes
show a "pending AP approval" banner (read from `GET /portal/company`'s
`pending_change`) so the vendor isn't surprised the change didn't take effect.

## Phase 2 (shipped)

- [x] PO flip — create invoice pre-populated from a PO (idempotent per `(vendor, po)`)
- [x] Remittance download (reuses `services/remittance_pdf.py`)
- [x] Company info self-update (contact live; bank/tax staged)
- [x] Bank-detail changes with AP admin approval workflow (fraud mitigation)
- [x] W-9 / W-8 upload + storage (self-service; reuses `Vendor.w9_file_key` / `w9_received_date`, no migration — see *W-9 / W-8 tax-form upload* above)
- [x] Dynamic-discount offers — vendor accepts/declines early-payment discounts
  (ties into the dynamic-discounting engine; accept never moves money)
- [x] In-app per-invoice chat between vendor and AP team
- [x] Notification preferences (email-on-paid, email-on-rejected) — per-portal-user, vendor-controlled; wired into the `transition_invoice` dispatch chokepoint
- [x] Virtual card viewing (secure, single-use reveal token) — `GET /portal/cards/{token}` consumes a one-time `CardRevealToken`
- [x] MFA (TOTP) for portal users (migration 0053; opt-in per vendor user, gated by `AP_MFA_ENABLED`)
- [x] MFA email-OTP backup factor for portal users (Redis-only, no migration; on-demand via `POST /portal/auth/mfa/challenge/email`, sent through the email adapter, gated by `AP_MFA_ENABLED`)

## Phase 3 (deferred)

Add these when there's demand from the first paying customer:

- (nothing currently parked here)

## Operational notes

- Portal users have no relationship to the control-plane `User` table. Adding
  one via the admin UI (or SCIM) does not create a portal login.
- Deleting a `Vendor` cascades to `vendor_users` (`ON DELETE CASCADE`). An
  orphaned portal user row is therefore impossible.
- The temp password is returned in the invite response body in addition to
  being emailed — in local dev where SMTP is a stub, the admin can still
  share it out of band.
