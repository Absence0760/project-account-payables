# Expense Management

Corporate expense tracking and reimbursement — out-of-pocket and card-funded
expenses, the reports that group them for approval, reimbursement policies,
spend pre-approvals, and corporate-card-transaction reconciliation.

This module is delivered in workflows. **WF1 (this document's scope) is the
foundation**: the full data model plus the `/expenses` and `/expense-reports`
HTTP API. Policy enforcement, card import/reconciliation, pre-approval gating,
and the frontend UX land in later workflows (see the roadmap at the bottom).

## Data model

All five tables are tenant-scoped (live in each `ap_<slug>` DB), carry an
explicit `organization_id uuid NOT NULL` (indexed) and the nullable
`entity_id` FK from `EntityMixin` (multi-entity Phase 2), and `created_at` /
`updated_at` from `TimestampMixin`. Money is `Decimal` in Python /
`Numeric(15, 2)` in columns — never float. Status types are `enum.StrEnum`
mapped to `varchar` columns (`native_enum=False`). All five live in
`backend/app/models/expense.py`.

| Table | Model | Purpose |
|-------|-------|---------|
| `expense_reports` | `ExpenseReport` | A grouping of expenses an employee submits for approval + reimbursement. `report_number`, `title`, `employee_user_id` (control-plane User id, no cross-DB FK), `status` (draft → submitted → pending_approval → approved/rejected → reimbursed/cancelled), `submitted_at`/`approved_at`/`approved_by`, `total_amount` (recomputed from attached expenses), `currency`, `notes`. |
| `expenses` | `Expense` | A single expense line. `report_id` (nullable — an expense can exist before being grouped), `expense_date`, `merchant`, `category`, `description`, `amount`, `currency`, `gl_account_id` (FK → `gl_accounts`), `receipt_file_key`, `payment_method` (out_of_pocket / corporate_card / virtual_card), `card_transaction_id`, `policy_violations` (JSONB list), `status`, `reimbursable`, `mileage_miles`. |
| `expense_policies` | `ExpensePolicy` | A reimbursement policy. `name`, `active`, `category` (NULL = all), `per_diem_amount`/`per_diem_currency`, `mileage_rate` (per mile), `category_limit`, `requires_preapproval_above`, `requires_receipt_above`, `rules` (JSONB). *Defined in WF1; enforced in WF3.* |
| `corporate_card_transactions` | `CorporateCardTransaction` | A card transaction feed row reconciled to an expense. `card_ref`, `card_last_four`, `virtual_card_id` (FK → `virtual_cards`), `txn_date`/`posted_date`, `merchant`, `amount`, `currency`, `external_txn_id` (provider id, drives import idempotency), `matched_expense_id`, `reconciliation_status` (unmatched/matched/ignored), `import_batch`, `raw` (JSONB). *Model in WF1; import/reconcile in WF4.* |
| `expense_preapprovals` | `ExpensePreapproval` | A spend pre-approval raised before an expense is incurred. `requester_user_id`, `title`, `estimated_amount`, `currency`, `category`, `justification`, `status` (pending/approved/rejected), `decided_by`/`decided_at`, `expense_report_id`. *Model in WF1; gating in WF3.* |

### Circular FK (expenses ↔ corporate_card_transactions)

An expense can point at the card transaction that funded it
(`expenses.card_transaction_id`), and a card transaction points back at the
expense it reconciled to (`corporate_card_transactions.matched_expense_id`).
This is a true cycle. It's broken with `use_alter=True` on the ORM side
(`Expense.card_transaction_id`) so `metadata.create_all` emits that FK as a
deferred `ALTER TABLE … ADD CONSTRAINT` after both tables exist. The migration
mirrors this: it creates both tables with the two cross-FK columns as bare
`uuid`, then adds both constraints in idempotent `DO $$ … EXCEPTION WHEN
duplicate_object` blocks (Postgres has no `ADD CONSTRAINT IF NOT EXISTS`). The
constraint names are identical on both sides so a migrated tenant and a
`create_all` tenant carry the same constraints (create_all parity).

### Import idempotency

`corporate_card_transactions` has a **partial unique index**
`uq_corporate_card_txn_external` on `(organization_id, external_txn_id) WHERE
external_txn_id IS NOT NULL`, so re-importing a provider feed can't create
duplicate rows for the same provider transaction, while manually-entered rows
(NULL `external_txn_id`) never collide.

## Migration

`backend/alembic/versions/0039_expense_management.py` (revision
`0039_expense_management`, down_revision `0038_supplier_chat`). It follows the
tenant-only pattern: an `_is_tenant_db()` gate (checks for the `invoices`
table) so it no-ops on the control plane and fans out to every tenant via
`scripts/migrate_all_tenants.py`. Idempotent (`CREATE TABLE IF NOT EXISTS` +
`CREATE INDEX IF NOT EXISTS`). It mirrors the models exactly — every
`index=True` plain index, every `entity_id`/`organization_id` index, and the
partial unique index — so fresh tenants (built via
`tenant_provisioning._create_tenant_tables` → `create_all`) match migrated
ones. A working `downgrade()` drops the cycle-closing FKs first, then the
tables in reverse dependency order.

## API

Two routers in `backend/app/api/expenses.py`, both mounted under `/api`:
`router` (`/expenses`) and `reports_router` (`/expense-reports`).

### RBAC

- **Reads** (list / get): `admin`, `ap_manager`, `ap_clerk`, `cfo`.
- **Mutations** (create / update / delete / receipt upload / attach):
  `admin`, `ap_manager`, `ap_clerk` — a clerk/employee submits their own
  expenses. CFO is read-only here.
- **Receipt download proxy**: plain `get_current_user` (any authenticated AP
  user), gated by the cross-tenant first-segment org check, not by role.

Every mutation writes an audit row via `dispatch_audit` (before `commit`):
`expense.created` / `expense.updated` / `expense.deleted` /
`expense.receipt_uploaded`, `expense_report.created` /
`expense_report.updated` / `expense_report.expenses_attached`. Audit `details`
carry field-names and string-Decimal amounts — never PII.

### Routes

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/expenses` | List, paginated, entity-scoped (`X-Entity-ID`); `?status=` + `?report_id=` filters. |
| POST | `/api/expenses` | Create an expense. Lands under the selected entity (or the tenant default). If `report_id` is supplied, the report's `total_amount` is recomputed. |
| GET | `/api/expenses/receipt/{file_key:path}` | Download proxy for a stored receipt. Cross-tenant-checked (first key segment must equal the caller's org); same 404 for wrong-org and missing-file. Declared before `/{expense_id}` so `receipt` isn't captured as an id. |
| POST | `/api/expenses/{id}/receipt` | Upload a receipt to S3 (`upload_expense_receipt`) and stamp `receipt_file_key`. |
| GET | `/api/expenses/{id}` | Get one expense. |
| PATCH | `/api/expenses/{id}` | Update mutable fields. Audits only when a field actually changed. An `amount` change or a `report_id` move recomputes the affected report total(s). |
| DELETE | `/api/expenses/{id}` | Delete an expense; recomputes the owning report total if it was attached. |
| GET | `/api/expense-reports` | List, paginated, entity-scoped; `?status=` filter. |
| POST | `/api/expense-reports` | Create a report. `employee_user_id` defaults to the caller. |
| GET | `/api/expense-reports/{id}` | Get one report (with its expenses). |
| PATCH | `/api/expense-reports/{id}` | Update mutable report fields. |
| POST | `/api/expense-reports/{id}/expenses` | Attach (or `detach: true`) expense ids; recomputes `total_amount`. Each id is looked up in this tenant's `expenses` table, so a cross-tenant/unknown id is a 404. Detaching nulls `report_id` (the expense outlives the report). |

### Storage

`app/services/storage.py::upload_expense_receipt` mirrors
`upload_contract_file`: key scheme `<org_id>/expenses/<expense_id>/<safe-filename>`
(the leading `org_id` segment is the cross-tenant download gate). It accepts
the invoice-grade content types (PDF / PNG / JPEG / TIFF / XML — receipts are
photographed; no Word). Download reuses the module-level `get_file`.

### Total recompute

A report's `total_amount` is always derived, never client-supplied: it's a
Postgres `SUM(amount)` over the report's currently-attached expenses, coerced
to `Decimal` (`_recompute_report_total`). It fires on attach/detach, on
creating an expense already pointed at a report, and on an expense amount
change or report move.

## Tests

`backend/tests/test_expenses.py` (pytest, async, `realdb` fixture) — expense
CRUD, receipt upload + download round-trip, cross-tenant receipt denial,
report create + attach/detach with exact total recompute, RBAC (CFO can read
but not mutate), tenant isolation, audit rows, exact `Numeric` money
round-trips, and an explicit five-table existence check (create_all parity for
the circular FK). The RBAC coverage gate (`tests/test_rbac.py`) confirms every
expense route carries an auth dependency.

## Roadmap (WF2–WF4)

- **WF2 — Submission UX + report lifecycle.** Frontend expense/report screens
  (Svelte 5 runes, shared components), mobile receipt capture, and the report
  approval flow (submit → pending_approval → approved/rejected → reimbursed)
  reusing the AP approval infrastructure.
- **WF3 — Policies + pre-approvals.** Enforce `ExpensePolicy` (per-diem,
  mileage rate × `mileage_miles`, category limits, receipt-required
  thresholds) writing into `Expense.policy_violations`; gate high-value
  expenses on an approved `ExpensePreapproval`.
- **WF4 — Corporate card import + reconciliation.** Import card feeds into
  `corporate_card_transactions` (idempotent on `external_txn_id`), auto-match
  to expenses, and link to the existing virtual-card program.
