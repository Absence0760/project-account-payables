# Privacy — DSAR export + right-to-erasure (GDPR / CCPA)

Two coupled data-subject rights, implemented together under the admin-only
`/api/privacy` router:

- **DSAR export** (GDPR Art. 15 / CCPA right-to-know) — assemble everything held
  about a data subject into a portable JSON bundle.
- **Right-to-erasure / anonymization** (GDPR Art. 17 / CCPA right-to-delete) —
  irreversibly redact a subject's PII **while preserving the immutable financial
  + audit record**.

Both are synchronous, admin-gated, and audited. Source:
`app/api/privacy.py`, `app/services/privacy_export.py`,
`app/services/privacy_erasure.py`, `app/schemas/privacy.py`,
`app/models/data_subject_request.py`, migration `0054_data_subject_requests`.

## Subject types

A data subject can live in three places, so the API is parameterised by
`subject_type`:

| `subject_type` | Where the PII lives | `identifier` |
|---|---|---|
| `user` | control-plane `User` (an AP-team member) | the user's email |
| `vendor_user` | tenant `VendorUser` (a supplier-portal login) | the vendor user's email |
| `vendor_contact` | tenant `Vendor` contact fields (the supplier company's own contact details) | the **Vendor UUID** |

`vendor_contact` is addressed by the vendor's id rather than an email because
vendor contact PII has no unique natural key (many vendors share a blank or
duplicate email).

## Endpoints

All three routes are `require_roles(ROLE_ADMIN)` — the privacy-officer
privilege.

### `POST /api/privacy/dsar`

Body: `{ "subject_type": "...", "identifier": "..." }`

Resolves the subject (org/tenant-scoped), assembles the bundle, records a
`DataSubjectRequest` row, writes a `privacy.dsar_export` audit row, and returns:

```jsonc
{
  "request_id": "...",
  "subject_type": "vendor_contact",
  "subject_id": "...",
  "generated_at": "2026-06-19T...",
  "data": { /* the portable bundle — see below */ }
}
```

The `data` bundle is intentionally loosely typed (it aggregates heterogeneous
records). Per subject type:

- **user** — `profile` (email, full_name, sso ids, MFA metadata,
  notification_prefs), `roles`, and non-PII `activity` *counts* (audit actions
  authored, in-app notifications). The tenant DB contributes only counts, never
  the content of those rows.
- **vendor_user** — `profile` (email, full_name, last_login, MFA metadata,
  notification_prefs) + the parent `vendor_id`.
- **vendor_contact** — `vendor` (name, code, email, phone, address, tax_id,
  bank_details, beneficial_owner_data), a summary of `related_invoices` /
  `related_payments` (id + **string-Decimal** amount + status + date — never
  float), `portal_users`, and `counts`.

### `POST /api/privacy/erasure`

Body: `{ "subject_type": "...", "identifier": "...", "confirm": true, "note": "GDPR #42" }`

`confirm` must be `true` (erasure is destructive). Redacts PII in place and
returns:

```jsonc
{
  "request_id": "...",
  "subject_type": "vendor_contact",
  "subject_id": "...",
  "status": "completed",   // or "noop" on a re-run
  "already_erased": false,
  "fields_redacted": 6,
  "record_counts": { "vendors": 1, "vendor_contact_fields": 6, ... },
  "completed_at": "2026-06-19T..."
}
```

`note` is an optional, PII-free operator note (legal basis / ticket reference) —
never auto-populated from subject data.

### `GET /api/privacy/requests`

The privacy officer's request history for the tenant — PII-free (subject UUID +
type + status + counts only).

## What is redacted vs. preserved

The governing rule: **legally-required retention wins over erasure for
transactional rows.** We redact PII *text* fields and keep the money trail.

| Subject type | Redacted | Preserved |
|---|---|---|
| `user` | email → tombstone, full_name, sso_provider/id, hashed_password, mfa_secret; deactivated | row id, organization_id, role assignments, **every `audit_log` row authored** (the `actor_id` link stays — non-repudiation) |
| `vendor_user` | email → tombstone, full_name, hashed_password, mfa_secret; deactivated | row id, vendor link |
| `vendor_contact` | vendor email, phone, address, tax_id, bank_details, beneficial_owner_data; the vendor's portal users (as above); supplier-authored **chat message bodies** (free-text PII) | **`vendor.name`** (the legal payee, denormalised onto every Invoice's `vendor_name` money field), **every related Invoice / Payment amount + status + date**, the chat threads + AP-side messages, the `audit_log` |

### Tombstones

Redacted unique fields (email is `UNIQUE` on both `User` and `VendorUser`) are
replaced with `erased+<subject_id>@redacted.invalid` — which stays unique, is
obviously non-deliverable, and lets an operator correlate a row to its erasure
without revealing the original value. Free-text names become `[redacted]`.

### Money trail is never touched

No amount, status, currency, or date is mutated by erasure — only PII text
columns are nulled / tombstoned. This is a project invariant (money is exact);
the erasure service touches no `Numeric`/`Decimal` column.

## Audit trail

Both operations write a **new** append-only audit row through `dispatch_audit`
(`privacy.dsar_export` / `privacy.erasure`). Erasure **never** updates or deletes
an existing `audit_log` row — it respects the migration-0022 immutability
trigger (which rejects every DELETE and every UPDATE touching a column other than
`shipped_at`). The financial/audit history of an erased subject's transactions
therefore survives erasure intact.

The audit `details` and the persisted `DataSubjectRequest` row are **PII-free**:
they carry only the resolved subject UUID + type + non-identifying counts —
never the email / tax-id / bank details that the DSAR *bundle* itself contains.
The bundle is returned in the HTTP response and is never logged or stored.

## Tenant isolation

Subjects span the control plane and the tenant DB. Isolation is enforced at the
data layer:

- The injected `get_tenant` / `get_tenant_db` chokepoint cross-checks the JWT
  `org` claim against the requested tenant (a spoofed `X-Tenant-Slug` alone can't
  widen access).
- `resolve_subject_id` filters by `organization_id` (control-plane `User`,
  tenant `Vendor`) or runs against the tenant DB (`VendorUser`), so a subject in
  tenant A is invisible — neither exportable nor erasable — when acting as
  tenant B. An unresolved subject is a flat `404 Subject not found` (the same
  shape whether it's truly absent or in another tenant, so the response can't be
  used to probe cross-tenant existence).

## Idempotency

Re-running erasure on an already-erased subject is a safe no-op (`status:
"noop"`, `already_erased: true`, no further mutation). The prior run is detected
by the tombstone email (`user` / `vendor_user`) or by all contact fields already
being NULL with no portal user / chat body left to redact (`vendor_contact`).

## Data model + migration

`DataSubjectRequest` (tenant-scoped, `data_subject_requests`, migration
`0054_data_subject_requests`) is the queryable request index — strictly PII-free
(request type/status, resolved subject UUID + type, requesting admin, counts,
timestamps). The migration is gated on the `invoices` table so it no-ops on the
control plane and fans out to every tenant via
`scripts/migrate_all_tenants.py`; it mirrors the model so fresh tenants built via
`tenant_provisioning._create_tenant_tables` (`create_all`) match a migrated one.

## Tests

`backend/tests/test_privacy.py` (real-Postgres `realdb` fixture): DSAR bundle
assembly per subject type; erasure redacts every PII field; erasure leaves
Invoice/Payment money fields **and** the append-only `audit_log` untouched;
erasure idempotency; non-admin 403; and tenant isolation for both export and
erasure.
