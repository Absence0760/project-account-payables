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
| `created_at` / `updated_at` | TIMESTAMPTZ | Standard TimestampMixin                              |

No MFA columns — MFA is a Phase 2 addition once we have demand.

## Endpoints

All under `/api/portal/*`. Tenant resolved via the usual `X-Tenant-Slug`
header. RBAC coverage gate in `tests/test_rbac.py` asserts every endpoint
uses `get_current_vendor_user` (except `/portal/auth/login` and
`/portal/auth/logout`, which are listed in `NO_AUTH_REQUIRED`).

### Auth (`portal_auth.py`)

| Method | Path                             | Notes                                                                         |
|--------|----------------------------------|-------------------------------------------------------------------------------|
| POST   | `/portal/auth/login`             | email + password → `{access_token, must_change_password}`                     |
| POST   | `/portal/auth/logout`            | Adds `jti` to the shared Redis blocklist                                      |
| GET    | `/portal/auth/me`                | Returns the vendor-user + vendor summary                                      |
| POST   | `/portal/auth/change-password`   | Used by the forced first-login rotation and voluntary rotations               |

### Invoices + payments (`portal.py`)

| Method | Path                             | Notes                                                                         |
|--------|----------------------------------|-------------------------------------------------------------------------------|
| GET    | `/portal/invoices`               | Vendor-scoped list                                                            |
| GET    | `/portal/invoices/{id}`          | 404 for "doesn't exist" AND "belongs to another vendor" (no ID enumeration)   |
| POST   | `/portal/invoices`               | Multipart PDF upload — routes into the same extraction pipeline as AP uploads |
| GET    | `/portal/payments`               | Payments joined to Invoice to filter on `vendor_id`                           |

### Admin invite (`vendors.py`)

| Method | Path                                                 | Notes                                 |
|--------|------------------------------------------------------|---------------------------------------|
| GET    | `/vendors/{id}/portal-users`                         | List portal users for a vendor        |
| POST   | `/vendors/{id}/portal-users`                         | Invite — temp password + welcome email |
| DELETE | `/vendors/{id}/portal-users/{vendor_user_id}`        | Remove a portal user                  |

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
| `/portal/login`               | Sign-in form                                           |
| `/portal/change-password`     | Forced first-login rotation                            |
| `/portal/invoices`            | List + upload                                          |
| `/portal/payments`            | Payment history                                        |

## Phase 2 (deferred)

Items intentionally out of scope for the initial landing cut. Add these only
when there's demand from the first paying customer:

- Bank-detail changes (with AP admin approval workflow for fraud mitigation)
- W-9 / W-8 upload + storage
- PO flip — create invoice pre-populated from a PO
- Virtual card viewing (secure, one-time access)
- Notification preferences (email-on-paid, email-on-rejected)
- Dynamic-discount offers
- In-app per-invoice chat between vendor and AP team
- MFA for portal users

## Operational notes

- Portal users have no relationship to the control-plane `User` table. Adding
  one via the admin UI (or SCIM) does not create a portal login.
- Deleting a `Vendor` cascades to `vendor_users` (`ON DELETE CASCADE`). An
  orphaned portal user row is therefore impossible.
- The temp password is returned in the invite response body in addition to
  being emailed — in local dev where SMTP is a stub, the admin can still
  share it out of band.
