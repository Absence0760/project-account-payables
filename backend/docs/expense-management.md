# Expense Management

Corporate expense tracking and reimbursement — out-of-pocket and card-funded
expenses, the reports that group them for approval, reimbursement policies,
spend pre-approvals, and corporate-card-transaction reconciliation.

This module is delivered in workflows. **WF1 is the foundation**: the full data
model plus the `/expenses` and `/expense-reports` HTTP API. **WF2 (Submission UX
+ Reporting)** adds the report-summary rollup, the expense-register CSV export,
the bulk GL re-code endpoint, and the full SvelteKit `/expenses` workspace
(two-tab Expenses/Reports page + `ExpenseModal`). **WF3 (Policies + Pre-approval
+ Manager Approval)** adds the policy engine (`services/expense_policy.py`),
policy + pre-approval CRUD routers, and the real report submit → approve →
reject lifecycle (reusing the AP approval infrastructure's `check_segregation`).
Card import/reconciliation lands in WF4 (see the roadmap at the bottom).

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
`expense.receipt_uploaded` / `expense.bulk_gl_coded` (one row per re-coded
expense), `expense_report.created` / `expense_report.updated` /
`expense_report.expenses_attached`. Audit `details` carry field-names and
string-Decimal amounts — never PII.

### Routes

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/expenses` | List, paginated, entity-scoped (`X-Entity-ID`); `?status=` + `?report_id=` filters. |
| POST | `/api/expenses` | Create an expense. Lands under the selected entity (or the tenant default). If `report_id` is supplied, the report's `total_amount` is recomputed. |
| GET | `/api/expenses/receipt/{file_key:path}` | Download proxy for a stored receipt. Cross-tenant-checked (first key segment must equal the caller's org); same 404 for wrong-org and missing-file. Declared before `/{expense_id}` so `receipt` isn't captured as an id. |
| POST | `/api/expenses/{id}/receipt` | Upload a receipt to S3 (`upload_expense_receipt`) and stamp `receipt_file_key`. |
| GET | `/api/expenses/export` | **(WF2)** Stream the filtered expense register as `text/csv` (`expenses_<today>.csv` via Content-Disposition). `?status=&category=&date_from=&date_to=&report_id=`; entity-scoped, no pagination (full filtered set). Outer-joins `GLAccount` (gl code) + `ExpenseReport` (report number) so an uncoded/unattached expense still emits a row. Serialised by `report_export.export_expense_register` (the `expense_register` exporter). Read RBAC (incl. CFO). Declared before `/{expense_id}`. |
| POST | `/api/expenses/bulk-gl-code` | **(WF2)** Set `gl_account_id` on many expenses at once (`null` clears it). Body `{ expense_ids: [uuid], gl_account_id: uuid\|null }`. Each id resolved within the entity scope (out-of-scope/cross-tenant id → 404); a non-`null` GL is validated against the org's chart. One `expense.bulk_gl_coded` audit row per expense; returns `{ updated }`. Mutation RBAC (`admin`/`ap_manager`/`ap_clerk`). Declared before `/{expense_id}`. |
| GET | `/api/expenses/{id}` | Get one expense. |
| PATCH | `/api/expenses/{id}` | Update mutable fields. Audits only when a field actually changed. An `amount` change or a `report_id` move recomputes the affected report total(s). |
| DELETE | `/api/expenses/{id}` | Delete an expense; recomputes the owning report total if it was attached. |
| GET | `/api/expense-reports` | List, paginated, entity-scoped; `?status=` filter. |
| POST | `/api/expense-reports` | Create a report. `employee_user_id` defaults to the caller. |
| GET | `/api/expense-reports/{id}` | Get one report (with its expenses). |
| GET | `/api/expense-reports/{id}/summary` | **(WF2)** Aggregate the report's attached expenses: `{ total, count, by_category: [{category, total, count}], by_status: [{status, total, count}] }`. SUMs run in Postgres over the `Numeric` column (exact); serialised as float to match `ExpenseResponse.amount` (read-only display rollup). Read RBAC (incl. CFO). |
| PATCH | `/api/expense-reports/{id}` | Update mutable report fields. |
| POST | `/api/expense-reports/{id}/expenses` | Attach (or `detach: true`) expense ids; recomputes `total_amount`. Each id is looked up in this tenant's `expenses` table, so a cross-tenant/unknown id is a 404. Detaching nulls `report_id` (the expense outlives the report). |
| POST | `/api/expense-reports/{id}/submit` | **(WF3)** `draft → submitted`. Runs the policy engine over the report's expenses; if any BLOCKING violation (missing required receipt, or required pre-approval absent) is present, returns **422** with `{ detail: { message, violations: [...] } }` and does NOT transition. On success stamps `submitted_at` and moves every child expense to `submitted`. Invalid source status → 422. RBAC `admin`/`ap_manager`/`ap_clerk` (the owner submits). Audited `expense_report.submitted`. |
| POST | `/api/expense-reports/{id}/approve` | **(WF3)** `submitted → approved`. Segregation of duties: the approver must differ from the report's `employee_user_id` (reuses `approval_chain.check_segregation` → **403**). CFO gate: when `total_amount` exceeds `Organization.settings.expense_approval.cfo_threshold` (default `5000`, Decimal math), only `cfo`/`admin` may approve (else 403). Stamps `approved_at` + `approved_by` (the approver's user id) and moves child expenses to `approved`. Invalid source status → 422. RBAC `admin`/`ap_manager`/`cfo`. Audited `expense_report.approved`. |
| POST | `/api/expense-reports/{id}/reject` | **(WF3)** `submitted → rejected`. Body `{ reason? }`. Returns each child expense to `draft` so they can be corrected and re-reported (`rejected` is terminal for the report row). Invalid source status → 422. RBAC `admin`/`ap_manager`. Audited `expense_report.rejected`. |

### Policy engine (WF3)

`app/services/expense_policy.py` is pure — no LLM, no DB, no network. The
caller (`api/expenses.py`) loads the active `ExpensePolicy` rows (and any
approved `ExpensePreapproval` coverage) from the tenant DB and hands them in:

- `evaluate_expense(expense, policies, approved_preapproval_amount=None)` →
  `list[dict]`. A policy applies when it is `active` and its `category` is NULL
  (all) or matches the expense category. Rules: `category_limit` exceeded;
  `receipt_required` (amount > `requires_receipt_above` and no
  `receipt_file_key` — **blocking**); `preapproval_required` (amount >
  `requires_preapproval_above` with no approved pre-approval covering it —
  **blocking**); `per_diem_exceeded`. All comparisons are `Decimal`.
- `mileage_reimbursement(expense, policies)` → `Decimal`
  (`mileage_miles * mileage_rate` from the first applicable policy with a rate).
- `evaluate_report(report, expenses, policies, preapproval_amount_by_expense=…)`
  → aggregate violations, each tagged with its source `expense_id`.
- `blocking_violations(violations)` filters to the `BLOCKING_CODES` subset
  (`receipt_required`, `preapproval_required`) — the ones that block submission.

Each violation dict is `{code, message, policy_id?, limit?, actual?,
expense_id?}` (advisory, PII-free). `evaluate_expense` is wired into expense
**create**, **PATCH**, and **receipt upload** as a best-effort refresh of
`Expense.policy_violations` (a policy-engine error never breaks the write).

### Policy + pre-approval CRUD (WF3)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/expense-policies` | List policies; `?active=&category=` filters. Read RBAC (all four roles). |
| POST | `/api/expense-policies` | Create a policy. Mutation RBAC (`admin`/`ap_manager`). Audited `expense_policy.created`. |
| GET/PATCH/DELETE | `/api/expense-policies/{id}` | Get / update / delete. Mutations `admin`/`ap_manager`, audited. |
| GET | `/api/expense-preapprovals` | List requests; `?status=&requester_user_id=` filters. Read RBAC (all four). |
| POST | `/api/expense-preapprovals` | Raise a request — `requester_user_id` is always the authenticated user (the body field is ignored so SoD on the decision side stays meaningful); status `pending`. RBAC `admin`/`ap_manager`/`ap_clerk`. Audited. |
| GET | `/api/expense-preapprovals/{id}` | Get one. Read RBAC. |
| POST | `/api/expense-preapprovals/{id}/approve` \| `/reject` | Decide. `check_segregation` blocks the requester from deciding their own request (403). Stamps `decided_by` + `decided_at` + status; invalid source status → 422. RBAC `admin`/`ap_manager`. Audited `expense_preapproval.{approved,rejected}`. |

### CFO approval threshold

`Organization.settings.expense_approval.cfo_threshold` (a Decimal-as-string,
default `5000`) gates report approval: a report whose `total_amount` exceeds it
requires the `cfo` (or `admin`) role. It's read off the injected `Organization`
row in the approve handler; writing it (if a settings UI is added) uses the
`flag_modified(org, 'settings')` idiom against the control DB.

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

`backend/tests/test_expense_reporting.py` (WF2) — the report-summary math
(grand total + per-category/per-status rollups, the empty-report and 404 cases,
CFO read access), the CSV export (header + a data row, the `?status=` filter,
CFO can export), and the bulk GL re-code (sets + one audit row per expense,
the `null`-clears case, unknown-GL/unknown-expense 404s, and the CFO-denied
RBAC case).

`backend/tests/test_expense_policy.py` (WF3, pure/DB-free) — the policy engine:
category limits, receipt-required, pre-approval-required (+ coverage), per-diem,
category matching (NULL = all), the active flag, mileage reimbursement (Decimal),
report aggregation, and the blocking-subset filter.

`backend/tests/test_expense_preapprovals.py` (WF3, `realdb`) — pre-approval
create (requester stamped from the caller), list + status filter, manager
approve/reject, self-decision blocked by segregation, double-decision 422, RBAC
(clerk can't approve), tenant isolation, and audit rows.

`backend/tests/test_expense_approval.py` (WF3, `realdb`) — policy CRUD + RBAC +
audit; a violation surfaced on expense create and cleared on receipt upload;
report submit blocked on a missing required receipt (422 + violation list, no
transition); submit success (child statuses + `submitted_at`); approve
self-blocked by segregation; a different manager approving; the CFO threshold
(default 5000 + a custom org override); the reject path returning children to
`draft`; invalid-state 422 guards; and exact `Numeric` policy money round-trips.

The frontend `/expenses` workspace is covered by the Playwright specs
`frontend/tests-e2e/expenses/expenses.spec.ts` (create an expense with a
receipt, KPIs update, GL-code it, build + attach + submit a report, export the
CSV) and `frontend/tests-e2e/expenses/expense-approval.spec.ts` (WF3 — create a
policy, an expense that violates it shows a badge, build + submit a report, a
different manager approves → `approved`).

## Roadmap (WF2–WF4)

- **WF2 — Submission UX + Reporting.** *(Shipped — see the API + Tests sections
  above.)* The report-summary rollup, expense-register CSV export, and bulk GL
  re-code endpoints, plus the SvelteKit `/expenses` workspace (two-tab
  Expenses/Reports page, `ExpenseModal`, KPIs, bulk GL coding, CSV export, and
  the draft→submitted report action). Mobile receipt capture and the deeper
  report approval lifecycle (pending_approval → approved/rejected → reimbursed,
  reusing the AP approval infrastructure) remain follow-on work.
- **WF3 — Policies + Pre-approval + Manager Approval.** *(Shipped — see the
  policy-engine, CRUD, and report-approval sections above.)* The
  `services/expense_policy.py` engine writes `Expense.policy_violations` on
  every expense write; the `/api/expense-policies` + `/api/expense-preapprovals`
  CRUD routers; the report `submit`/`approve`/`reject` lifecycle gating on
  blocking violations, segregation of duties, and the
  `expense_approval.cfo_threshold`. The frontend adds Policies + Pre-approvals
  tabs and the real report submit/approve/reject actions.
- **WF4 — Corporate card import + reconciliation.** Import card feeds into
  `corporate_card_transactions` (idempotent on `external_txn_id`), auto-match
  to expenses, and link to the existing virtual-card program.
