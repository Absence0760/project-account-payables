# Vendor Management

## Overview

Vendors enter the system through three channels. Each has a different trust level and verification requirement.

```
ERP Sync ─────────> Active (trusted — verified by ERP)
Manual Create ────> Active (admin-verified on creation)
AI from Invoice ──> Unverified (draft) ──> Review ──> Active or Rejected
```

## Vendor Sources

| Source | How it enters | Initial status | Verification |
|---|---|---|---|
| **ERP Sync** | Pulled from connected ERP via `POST /api/vendors/sync-erp` | `active` | Trusted — ERP is source of truth |
| **Manual** | Created by admin/AP manager via the UI | `active` | Verified on creation (by the creating user) |
| **AI Extracted** | Auto-created when invoice extraction finds no matching vendor | `unverified` | Requires human review before payment |

## Vendor Lifecycle

```
                 ┌──────────┐
 ERP Sync ──────>│  Active   │<──── Verify
                 └────┬─────┘
                      │
 Manual Create ──────>│
                      │
                 ┌────┴─────┐      ┌──────────┐
 AI Extract ───>│Unverified │─────>│ Rejected  │
                 └──────────┘      └──────────┘
                                        │
                      ┌─────────────────┘
                      v
                 ┌──────────┐
                 │ Inactive  │  (deactivated, preserved for history)
                 └──────────┘
```

| Status | Meaning | Can receive payments? |
|---|---|---|
| `active` | Verified, fully operational | Yes |
| `unverified` | AI-created, needs human review | No — blocked from payment runs |
| `inactive` | Deactivated by admin | No |
| `rejected` | Flagged as invalid or duplicate | No |

## Vendor Matching

When an invoice is extracted (via AI/OCR), the system attempts to match the extracted vendor name to an existing vendor. This avoids duplicate vendor creation and links invoices to the correct vendor record.

`services/vendor_matching.match_and_link_vendor` is the single matcher, and it
runs on **every** path that can set an invoice's vendor — not just extraction:

| Caller | When |
|---|---|
| `services/extraction` | After an AI/OCR extraction resolves a vendor name |
| `POST /api/invoices` (manual, no-OCR entry) | On create, so a hand-keyed invoice is linked too |
| `PATCH /api/invoices/{id}` | When the vendor name is (re)saved **and** the link is stale (name changed) or missing. Clearing the name to blank clears `vendor_id` instead — the matcher no-ops on an empty name, and a nameless invoice must not keep an uncorroborated link |

That matters beyond tidy data: `Invoice.vendor_id` — not the free-text
`vendor_name` — is what the credit-memo vendor guard compares, and that guard is
fail-closed (an invoice with no resolved vendor cannot be credited at all). A
rename that left the old link in place, or a manual entry that never got one,
would respectively mis-attribute or block a credit. Re-saving an invoice's
vendor name is also the supported way to resolve an invoice that predates
create-time resolution. See `docs/api-reference.md` § Credit Memos.

Vendors created by the manual-entry path are stamped `source = manual` rather
than `ai_extracted` (`match_and_link_vendor(..., source=...)`); matching itself
is identical.

### Matching Logic (priority order)

1. **Tax ID match** — if the extracted invoice has a vendor tax ID, exact-match against `vendor.tax_id`. Confidence: 1.0.
2. **Exact name match** — case-insensitive match on `vendor.name`. Confidence: 0.98.
3. **Fuzzy name match** — normalize both names (strip suffixes like Inc/LLC/Ltd, remove punctuation, collapse whitespace) and compute Jaccard token similarity. Address overlap boosts the score. Threshold: 0.6.

### Matching is scoped to the invoice's entity (subsidiary)

All three lookups run against **the invoice's own `entity_id` ∪ vendors whose
`entity_id` is NULL** — `vendor_matching._candidate_query`, built on the shared
`tenant.apply_entity_scope(..., include_shared=True)`. Without that scope, a
multi-entity tenant could link subsidiary A's invoice to subsidiary B's vendor
row; because `Invoice.vendor_id` is what the fail-closed credit-memo guard
compares, such a mislink has a money consequence (one subsidiary's credit
applying against another's payable).

What a NULL `entity_id` means differs from `gl_accounts`, where NULL is a
deliberate "shared chart" marker. On `vendors` it means the row was never
stamped with a subsidiary — a pre-multi-entity row that migration `0029`'s
backfill didn't reach, or one auto-created from an invoice that itself carried
no entity. Those rows stay matchable from **every** entity: a supplier is a
real-world counterparty, not subsidiary-private data, and excluding them would
not fail loudly — it would silently mint a duplicate vendor, splitting the
supplier's spend rollup and giving it a second, independently editable
bank-detail record. When the same supplier exists both unstamped and under the
invoice's own entity, the entity's own row wins (the candidate query orders it
first).

`match_and_link_vendor` derives the entity from `invoice.entity_id`, so no
caller passes it explicitly — including an inter-company **mirror** payable,
which sits under the counterparty entity and therefore matches against the
counterparty's vendors automatically. The two exception-agent resolvers that
call `match_vendor` directly (`missing_po_v1`, `multi_po_split_v1`) pass
`entity_id=invoice.entity_id` so an agent can't reach a same-named vendor in
another entity and then PO-match across subsidiaries.

`entity_id=None` (an unstamped invoice, or a caller with no entity in hand) is a
passthrough that searches the whole tenant — the pre-multi-entity behaviour.
That is also why **single-entity tenants see no change at all**: with one default
entity every vendor is either under it or NULL, so `entity ∪ NULL` admits the
whole table.

Ordering also makes the pick deterministic: the tax_id and exact-name lookups
take the first ordered row rather than `scalar_one_or_none()`, so the same
supplier legitimately registered under two subsidiaries (duplicate `tax_id`)
resolves to one row instead of raising and turning invoice creation into a 500.

### Match Outcomes

| Confidence | Action |
|---|---|
| >= 0.8 | Auto-link invoice to vendor |
| 0.6 - 0.8 | Link but flag for review |
| < 0.6 | Create new unverified vendor from invoice data |

### What gets extracted to the new vendor

When no match is found, a new vendor is created with:
- `name` from `invoice.vendor_name`
- `address` from `invoice.vendor_address`
- `tax_id` from `invoice.vendor_tax_id`
- `status` = `unverified`
- `source` = `ai_extracted`

## ERP Vendor Sync

The system can pull the vendor master list from the connected ERP. This ensures vendor data (names, codes, payment terms, bank details) stays in sync.

### Sync Behavior

| Scenario | Action |
|---|---|
| ERP vendor not in local DB | Create new vendor with `source=erp_sync`, `status=active` |
| ERP vendor matches by `erp_vendor_id` | Update fields if changed, update `erp_synced_at` |
| ERP vendor matches by name (no `erp_vendor_id` yet) | Link existing vendor to ERP, update fields |
| Unverified vendor matches an ERP vendor by name | Auto-verify → set `status=active`, `source=erp_sync` |

### Sync Endpoint

`POST /api/vendors/sync-erp` — triggers a sync pull. Currently uses mock data; in production, calls the ERP adapter to fetch the vendor list.

Each synced vendor stores:
- `erp_vendor_id` — the vendor's ID in the external ERP (for two-way mapping)
- `erp_synced_at` — timestamp of last sync

## Vendor Fields

| Field | Type | Description |
|---|---|---|
| name | String | Vendor display name |
| code | String | Short code (e.g., "OSC") |
| email | String | Contact email |
| phone | String | Contact phone |
| address | String | Full address |
| tax_id | String | EIN / VAT number |
| payment_terms | String | Default payment terms |
| bank_details | JSONB | Bank account info (routing, account, SWIFT) |
| accepts_virtual_cards | Boolean | Whether vendor accepts card payments |
| status | String | active / unverified / inactive / rejected |
| source | String | manual / erp_sync / ai_extracted |
| verified_by | String | Who verified the vendor |
| verified_at | DateTime | When verified |
| erp_vendor_id | String | Vendor ID in the external ERP |
| erp_synced_at | DateTime | Last ERP sync timestamp |

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/vendors` | List vendors (filterable by status, source, search) |
| `GET` | `/api/vendors/{id}` | Get single vendor |
| `POST` | `/api/vendors` | Create vendor (manual, admin-verified) |
| `PATCH` | `/api/vendors/{id}` | Update vendor fields |
| `DELETE` | `/api/vendors/{id}` | Delete vendor |
| `POST` | `/api/vendors/{id}/verify` | Verify an unverified vendor → active |
| `POST` | `/api/vendors/{id}/reject` | Reject a vendor → rejected |
| `POST` | `/api/vendors/sync-erp` | Pull vendors from connected ERP |

## User Interface

### Vendors Page (`/vendors`)

**Top bar:** Search + "Sync from ERP" button

**Filter chips:** All / Unverified (with red count badge) / Active / Rejected

**Table columns:**
| Column | Description |
|---|---|
| Vendor | Name |
| Code | Short code |
| Email | Contact |
| Status | Badge: Active (green), Unverified (yellow), Rejected (red) |
| Source | Badge: Manual, ERP Sync, AI Extracted |
| Invoices | Count of linked invoices |
| ERP | Link icon if `erp_vendor_id` is set |
| Actions | Verify / Reject buttons (for unverified vendors) |

Unverified rows highlighted yellow. Rejected rows dimmed.

## Role Access

| Action | Admin | AP Manager | AP Clerk | CFO |
|---|---|---|---|---|
| View vendors | Yes | Yes | No | Yes |
| Create vendor | Yes | Yes | No | No |
| Edit vendor | Yes | Yes | No | No |
| Verify vendor | Yes | Yes | No | No |
| Reject vendor | Yes | Yes | No | No |
| Sync from ERP | Yes | No | No | No |
| Delete vendor | Yes | No | No | No |

## Integration with Invoice Processing

1. **Upload** — invoice received, no vendor linked yet
2. **Extraction** — AI extracts vendor name, address, tax ID
3. **Vendor Matching** — system fuzzy-matches against existing vendors
   - Match found → invoice linked to vendor
   - No match → new unverified vendor created, invoice linked
4. **Review** — reviewer sees the vendor (with unverified badge if new)
5. **Payment** — only invoices linked to `active` vendors can be paid
   - Unverified vendors block the invoice from entering the payment queue

This ensures no payment goes to a vendor that hasn't been verified, while still allowing the invoice processing pipeline to continue (extraction, review, approval) before verification.

## Implementation Status

| Feature | Status |
|---|---|
| Vendor model with status/source/ERP fields | Done |
| Vendor CRUD API with verify/reject endpoints | Done |
| Vendor matching service (fuzzy + tax ID + exact) | Done |
| AI auto-creation on extraction | Done |
| Vendor matching wired into extraction pipeline | Done |
| ERP vendor sync service | Done |
| Vendor sync endpoint (mock data) | Done |
| Vendors page with status filters and verification UI | Done |
| Seed script: new columns for existing DBs | Done |
| Real ERP vendor list fetch (via adapter) | Planned |
| Two-way sync (push new vendors to ERP) | Planned |
| Vendor deduplication UI | Planned |
| Vendor bank detail change approval workflow | Planned |
