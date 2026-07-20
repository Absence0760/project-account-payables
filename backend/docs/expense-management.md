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
**WF4 (Corporate-card import + reconciliation + virtual-card integration)** adds
the `/api/corporate-card-transactions` router: card-feed CSV import, charged
virtual-card sync, and the expense reconciliation surface (match-suggestions,
match/unmatch, ignore, create-expense-from-card). See the Corporate-card
reconciliation routes section below.

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
| `expense_reports` | `ExpenseReport` | A grouping of expenses an employee submits for approval + reimbursement. `report_number`, `title`, `employee_user_id` (control-plane User id, no cross-DB FK), `status` (draft → submitted → pending_approval → approved/rejected → reimbursed/cancelled), `submitted_at`/`approved_at`/`approved_by`, `total_amount` (recomputed from attached expenses, denominated in `currency`), `currency`, `reporting_currency`/`reporting_amount`/`reporting_fx_rate`/`reporting_fx_locked_at` (the total re-expressed in the org reporting currency, rate locked at submit — what the CFO gate compares), `notes`. |
| `expenses` | `Expense` | A single expense line. `report_id` (nullable — an expense can exist before being grouped), `expense_date`, `merchant`, `category`, `description`, `amount`, `currency`, `converted_currency`/`converted_amount`/`converted_fx_rate`/`converted_fx_locked_at` (the line re-expressed in the owning report's currency, rate locked on attach/edit), `gl_account_id` (FK → `gl_accounts`), `receipt_file_key`, `payment_method` (out_of_pocket / corporate_card / virtual_card), `card_transaction_id`, `policy_violations` (JSONB list), `status`, `reimbursable`, `mileage_miles`. |
| `expense_policies` | `ExpensePolicy` | A reimbursement policy. `name`, `active`, `category` (NULL = all), `threshold_currency` (the unit **every** money threshold below is denominated in; NULL = the org's reporting currency — migration `0077`), `per_diem_amount`/`per_diem_currency` (the latter descriptive only, kept in step with `threshold_currency`), `mileage_rate` (per mile), `category_limit`, `requires_preapproval_above`, `requires_receipt_above`, `rules` (JSONB). *Defined in WF1; enforced in WF3.* |
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
| POST | `/api/expenses` | Create an expense. `amount` must be **strictly positive** (`gt=0` → 422 otherwise; a negative line could net a report under the CFO threshold while hiding a large expense). Lands under the selected entity (or the tenant default). If `report_id` is supplied, the report's `total_amount` is recomputed. |
| GET | `/api/expenses/receipt/{file_key:path}` | Download proxy for a stored receipt. Cross-tenant-checked (first key segment must equal the caller's org); same 404 for wrong-org and missing-file. Declared before `/{expense_id}` so `receipt` isn't captured as an id. |
| POST | `/api/expenses/{id}/receipt` | Upload a receipt to S3 (`upload_expense_receipt`) and stamp `receipt_file_key`. |
| GET | `/api/expenses/export` | **(WF2)** Stream the filtered expense register as `text/csv` (`expenses_<today>.csv` via Content-Disposition). `?status=&category=&date_from=&date_to=&report_id=`; entity-scoped, no pagination (full filtered set). Outer-joins `GLAccount` (gl code) + `ExpenseReport` (report number) so an uncoded/unattached expense still emits a row. Serialised by `report_export.export_expense_register` (the `expense_register` exporter). Read RBAC (incl. CFO). Declared before `/{expense_id}`. |
| POST | `/api/expenses/bulk-gl-code` | **(WF2)** Set `gl_account_id` on many expenses at once (`null` clears it). Body `{ expense_ids: [uuid], gl_account_id: uuid\|null }`. Each id resolved within the entity scope (out-of-scope/cross-tenant id → 404); a non-`null` GL is validated against the org's chart. One `expense.bulk_gl_coded` audit row per expense; returns `{ updated }`. Mutation RBAC (`admin`/`ap_manager`/`ap_clerk`). Declared before `/{expense_id}`. |
| GET | `/api/expenses/{id}` | Get one expense. |
| PATCH | `/api/expenses/{id}` | Update mutable fields (`amount` still `gt=0`). Audits only when a field actually changed. An `amount` change or a `report_id` move recomputes the affected report total(s) — and is **409** if any affected report has left `draft` into a locked state (submitted/pending_approval/approved/reimbursed), so an edit can't silently move a total the CFO gate / approval signature already ran against. |
| DELETE | `/api/expenses/{id}` | Delete an expense; recomputes the owning report total if it was attached. **409** if the owning report is locked (submitted/approved/…) — deleting would shrink a total past its approval. |
| GET | `/api/expense-reports` | List, paginated, entity-scoped; `?status=` filter. |
| POST | `/api/expense-reports` | Create a report. `employee_user_id` defaults to the caller. |
| GET | `/api/expense-reports/{id}` | Get one report (with its expenses). |
| GET | `/api/expense-reports/{id}/summary` | **(WF2)** Aggregate the report's attached expenses: `{ total, count, by_category: [{category, total, count}], by_status: [{status, total, count}] }`. SUMs run in Postgres over the `Numeric` column (exact); serialised as float to match `ExpenseResponse.amount` (read-only display rollup). Read RBAC (incl. CFO). |
| PATCH | `/api/expense-reports/{id}` | Update mutable report fields. **409** once the report is locked (submitted/pending_approval/approved/reimbursed) — report-level fields (currency in particular) reinterpret a total the approval already ran against. |
| POST | `/api/expense-reports/{id}/expenses` | Attach (or `detach: true`) expense ids; recomputes `total_amount`. Each id is looked up in this tenant's `expenses` table, so a cross-tenant/unknown id is a 404. Detaching nulls `report_id` (the expense outlives the report). Composition is only mutable while the **target** report is a `draft` (**409** otherwise), and an expense can't be moved off a **locked** source report (**409**) — terminal `rejected`/`cancelled` reports stay detachable so their expenses can be re-reported. |
| POST | `/api/expense-reports/{id}/submit` | **(WF3)** `draft → submitted`. Runs the policy engine over the report's expenses; if any BLOCKING violation (missing required receipt, or required pre-approval absent) is present, returns **422** with `{ detail: { message, violations: [...] } }` and does NOT transition. On success stamps `submitted_at` and moves every child expense to `submitted`. Invalid source status → 422. RBAC `admin`/`ap_manager`/`ap_clerk` (the owner submits). Audited `expense_report.submitted`. |
| POST | `/api/expense-reports/{id}/approve` | **(WF3)** `submitted → approved`. Segregation of duties: the approver must differ from the report's `employee_user_id` (reuses `approval_chain.check_segregation` → **403**). CFO gate: when `total_amount` exceeds `Organization.settings.expense_approval.cfo_threshold` (default `5000`, Decimal math), only `cfo`/`admin` may approve (else 403). Stamps `approved_at` + `approved_by` (the approver's user id) and moves child expenses to `approved`. Invalid source status → 422. RBAC `admin`/`ap_manager`/`cfo`. Audited `expense_report.approved`. |
| POST | `/api/expense-reports/{id}/reject` | **(WF3)** `submitted → rejected`. Body `{ reason? }`. Returns each child expense to `draft` so they can be corrected and re-reported (`rejected` is terminal for the report row). Invalid source status → 422. RBAC `admin`/`ap_manager`. Audited `expense_report.rejected`. |

### Corporate-card reconciliation routes (WF4)

Router `app/api/expense_cards.py`, prefix `/api/corporate-card-transactions`. Read = all roles; mutate = `admin`/`ap_manager` (create-expense also allows `ap_clerk`). Every mutation is audited and entity-scoped; PII is `card_last_four` only.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/corporate-card-transactions` | List, paginated, entity-scoped; `?reconciliation_status=&virtual_card_id=&date_from=&date_to=` filters. Ordered by `txn_date` desc. |
| POST | `/api/corporate-card-transactions/import-csv` | Import a card-feed CSV (`import_corporate_card_csv`). Columns: `external_txn_id,date,posted_date,merchant,amount,currency,card_last_four,card_ref`. Dedupes on `(org, external_txn_id)` — already-imported and in-file duplicate rows are skipped (counted). All rows in one upload share an `import_batch`. Returns `ImportResult.to_dict()` (`{imported, skipped, errors}`). Audited `card_txn.imported`. Mutate `admin`/`ap_manager`. Declared before `/{txn_id}`. |
| POST | `/api/corporate-card-transactions/sync-virtual-cards` | **(item 5)** Create card-transaction rows from this tenant's charged `VirtualCard` rows (`status in (charged, completed)` with `amount_charged`). Idempotent via the synthetic `external_txn_id = vc:<provider_card_id>`; already-synced cards are skipped. `virtual_card_id`/`amount`/`merchant`/`entity_id` carried over. Returns `{created, skipped}`. Audited `card_txn.virtual_cards_synced`. Mutate `admin`/`ap_manager`. Declared before `/{txn_id}`. |
| GET | `/api/corporate-card-transactions/{id}/match-suggestions` | Ranked candidate expenses: `amount` exact (`Decimal ==`) + `card_transaction_id IS NULL` + `expense_date` within ±5d of `txn_date`, ranked by fuzzy merchant similarity (token Jaccard) then date proximity. Returns `[{expense, score}]`. Read all roles. |
| POST | `/api/corporate-card-transactions/{id}/match` | Body `{ expense_id }`. Reconcile: sets `txn.matched_expense_id` + `reconciliation_status=matched` AND `expense.card_transaction_id` + `expense.payment_method` (`virtual_card` when `txn.virtual_card_id` set, else `corporate_card`). **409** if either side already matched. Audited both sides (`card_txn.matched` + `expense.card_matched`). Mutate `admin`/`ap_manager`. |
| POST | `/api/corporate-card-transactions/{id}/unmatch` | Clear both sides; `reconciliation_status=unmatched`. Audited both sides. Mutate `admin`/`ap_manager`. |
| POST | `/api/corporate-card-transactions/{id}/ignore` | `reconciliation_status=ignored` (deliberately not reconciled — refunds/fees). Audited `card_txn.ignored`. Mutate `admin`/`ap_manager`. |
| POST | `/api/corporate-card-transactions/{id}/create-expense` | Mint an `Expense` from the txn (`expense_date=txn_date`, merchant, amount, currency, `payment_method` per `virtual_card_id`, entity carried over), then match it both sides. **409** if the txn is already matched. Audited `expense.created` + the match pair. Mutate `admin`/`ap_manager`/`ap_clerk`. |

#### Reconciliation strategy

Match-suggestion mirrors `services/bank_reconciliation.py`'s amount-exact + date-window approach (the window const `_CARD_MATCH_WINDOW_DAYS = 5` lives locally in `services/expense_card_reconciliation.py`). Candidates are pulled by exact `Decimal` amount equality + the unmatched filter in SQL; the ±N-day date window is applied in Python; results are ranked by fuzzy merchant similarity (`vendor_matching._normalize`/`_similarity`) descending, then by smallest date gap. All money math is `Decimal`; only the response serialiser does `float(...)`.

Virtual-card sync (`sync_virtual_cards`) carries charged virtual-card spend into the same feed so virtual cards reconcile through one surface. Dedupe is the synthetic `external_txn_id = vc:<provider_card_id>` backed by the `uq_corporate_card_txn_external` partial-unique index (no new webhook — the card-charge webhook already exists). Each synced txn keeps the card's own `entity_id`.

### Policy engine (WF3)

`app/services/expense_policy.py` is pure — no LLM, no DB, no network. The
caller (`api/expenses.py`) loads the active `ExpensePolicy` rows (and any
approved `ExpensePreapproval` coverage) from the tenant DB and hands them in:

- `evaluate_expense(expense, policies, approved_preapproval_amount=None,
  default_threshold_currency="USD")` → `list[dict]`. A policy applies when it is
  `active` and its `category` is NULL (all) or matches the expense category.
  Rules: `category_limit` exceeded; `receipt_required` (over
  `requires_receipt_above` and no `receipt_file_key` — **blocking**);
  `preapproval_required` (over `requires_preapproval_above` with no approved
  pre-approval covering it — **blocking**); `per_diem_exceeded`. All comparisons
  are `Decimal`, and all of them are **currency-aware** — see below.
- `threshold_currency_for(policy, default_currency)` → the unit a policy's money
  thresholds are read in.
- `mileage_reimbursement(expense, policies)` → `Decimal`
  (`mileage_miles * mileage_rate` from the first applicable policy with a rate;
  denominated in that policy's threshold currency).
- `evaluate_report(report, expenses, policies, preapproval_amount_by_expense=…,
  default_threshold_currency=…)` → aggregate violations, each tagged with its
  source `expense_id`.
- `blocking_violations(violations)` filters to the `BLOCKING_CODES` subset
  (`receipt_required`, `preapproval_required`) — the ones that block submission.

Each violation dict is `{code, message, policy_id?, limit?, actual?, currency?,
comparison?, expense_currency?, expense_id?}` (advisory, PII-free — amounts and
ISO codes only). `evaluate_expense` is wired into expense **create**, **PATCH**,
and **receipt upload** as a best-effort refresh of `Expense.policy_violations`
(a policy-engine error never breaks the write).

#### Threshold currency — what a policy's numbers mean

A policy's money thresholds (`category_limit`, `per_diem_amount`,
`requires_receipt_above`, `requires_preapproval_above`) are denominated in
`ExpensePolicy.threshold_currency` (migration `0077`). Before the column existed
the engine compared them to `expense.amount` as bare numbers, so a €200 EUR
expense was judged against a USD 100 limit as "200 > 100" — and
`receipt_required` is a **blocking** code, so a policy could block a compliant
expense (¥10 000 ≈ $65 read as "10000 > 5000") or fail to block a
non-compliant one.

**Where the rate comes from.** Nowhere new — the engine performs no FX call and
stays pure. A policy threshold is a *standing rule*, not a transaction, so there
is no moment at which a rate could honestly be locked onto the policy row (and a
rate locked when the rule was written would be stale for every expense it ever
judges). Instead the engine reuses the rate a **write path already locked onto
the expense** (`expenses.converted_*`, § Multi-currency reports) via
`expense_currency.expense_amount_in_currency`, which resolves in this order:

1. a locked conversion **into the threshold currency** → that figure;
2. the expense already denominated in the threshold currency → its face amount;
3. otherwise `None` — the comparison cannot be made.

A lock into some *other* currency (the line's report currency, say GBP) is never
reused for a EUR threshold — it says nothing about it.

**Unresolvable (case 3) is fail-closed, per threshold:**

| Threshold | Blocking? | Behaviour when the comparison can't be made |
|---|---|---|
| `requires_receipt_above` | yes | **Requires the receipt.** The violation is raised unless a `receipt_file_key` is present — a receipt is evidence that doesn't depend on the rate, so the rule stays satisfiable. |
| `requires_preapproval_above` | yes | **Requires the pre-approval.** Raised unless an approved pre-approval covers the expense *in the expense's own currency* (`_approved_preapproval_amount` filters on `ExpensePreapproval.currency`, so that check stands on its own — a €500 pre-approval never satisfies a $500 expense). |
| `category_limit` | no | Flagged for review (advisory badge). |
| `per_diem_amount` | no | Flagged for review (advisory badge). |

Every such violation carries `comparison: "unresolved"` plus `currency` (the
threshold's unit) and `expense_currency` (the unit of the `actual` figure), so
the UI can say *why* it flagged instead of asserting a comparison that never
happened. Attaching the line to a report locks a rate and the next evaluation
compares for real.

**Existing rows have `threshold_currency = NULL`, and it was not backfilled.**
NULL is a defined state — *"the org's reporting currency"*
(`currency_conversion.resolve_reporting_currency`), resolved at evaluation time
and passed in as `default_threshold_currency` by `api/expenses.py`. That is the
unit a bare threshold number already implicitly had everywhere else (the CFO
expense threshold, the policy table in the UI). It was not written into the rows
because the reporting currency lives in the **control-plane**
`organizations.settings`, which a **tenant**-DB migration cannot read; and the
only in-table candidate, `per_diem_currency`, is server-defaulted `'USD'` on
every row and was never read, so copying it would have frozen the exact
silent-USD guess being removed. `per_diem_currency` is now descriptive only —
the API keeps it in step with `threshold_currency` on write.

### Policy + pre-approval CRUD (WF3)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/expense-policies` | List policies; `?active=&category=` filters. Read RBAC (all four roles). |
| POST | `/api/expense-policies` | Create a policy. `threshold_currency` is optional (uppercased + shape-checked as a 3-letter ISO 4217 code; omitted / blank → NULL = the org's reporting currency) and, when given, also sets `per_diem_currency` unless that was named explicitly. Mutation RBAC (`admin`/`ap_manager`). Audited `expense_policy.created`. |
| GET/PATCH/DELETE | `/api/expense-policies/{id}` | Get / update / delete. Mutations `admin`/`ap_manager`, audited. |
| GET | `/api/expense-preapprovals` | List requests; `?status=&requester_user_id=` filters. Read RBAC (all four). |
| POST | `/api/expense-preapprovals` | Raise a request — `requester_user_id` is always the authenticated user (the body field is ignored so SoD on the decision side stays meaningful); status `pending`. RBAC `admin`/`ap_manager`/`ap_clerk`. Audited. |
| GET | `/api/expense-preapprovals/{id}` | Get one. Read RBAC. |
| POST | `/api/expense-preapprovals/{id}/approve` \| `/reject` | Decide. `check_segregation` blocks the requester from deciding their own request (403). Stamps `decided_by` + `decided_at` + status; invalid source status → 422. RBAC `admin`/`ap_manager`. Audited `expense_preapproval.{approved,rejected}`. |

### CFO approval threshold

`Organization.settings.expense_approval.cfo_threshold` (a Decimal-as-string,
default `5000`) gates report approval: a report whose total exceeds it requires
the `cfo` (or `admin`) role. It's read off the injected `Organization` row in
the approve handler; writing it (if a settings UI is added) uses the
`flag_modified(org, 'settings')` idiom against the control DB.

The threshold is a **bare number denominated in the org's reporting currency**
(`currency_conversion.resolve_reporting_currency`), so the comparison uses
`ExpenseReport.reporting_amount` — the total converted into that currency at a
rate locked when the report was **submitted** — not the report's own-currency
`total_amount`. Without that step a 4 900 EUR report slips under a 5 000 USD
threshold, i.e. filing in a weaker currency dodges CFO review. When the
reporting figure cannot be established (a foreign-currency report and no usable
rate) the gate **fails closed**: CFO/admin sign-off is required. The approve
audit row records `gate_total` + `gate_currency` so the decision is replayable
without re-deriving a rate. See § Multi-currency reports.

The threshold is parsed through the shared fail-closed helper
`approval_chain.cfo_gate_applies` (the same one the invoice-approval and
auto-approve gates use). A **malformed** `cfo_threshold` (a typo like `"5,000"`,
an empty string, or a non-finite value) is treated as **"CFO approval
required"** — it never silently skips the gate and never 500s the approve
endpoint; a `cfo`/`admin` can still approve past it. See
`workflow-design.md` § Approval Thresholds.

### Storage

`app/services/storage.py::upload_expense_receipt` mirrors
`upload_contract_file`: key scheme `<org_id>/expenses/<expense_id>/<safe-filename>`
(the leading `org_id` segment is the cross-tenant download gate). It accepts
the invoice-grade content types (PDF / PNG / JPEG / TIFF / XML — receipts are
photographed; no Word). Download reuses the module-level `get_file`.

### Total recompute

A report's `total_amount` is always derived, never client-supplied
(`_recompute_report_total`). It sums each attached line's **rate-locked**
`converted_amount` — or, for a line already denominated in the report's
currency, its exact face `amount`. See § Multi-currency reports. It fires on
attach/detach, on creating an expense already pointed at a report, on an
expense amount/currency change or report move, on a report currency change,
and again at submit. Every figure is `Decimal`, quantized to 2 dp
`ROUND_HALF_UP` — never float.

## Multi-currency reports

An employee on one trip legitimately spends in several currencies, so a report
is **not** constrained to a single currency. Instead every line is converted,
and the conversion is **locked** — the same convention the invoice path uses
(`currency_conversion.materialize_reporting_amount` → `invoices.reporting_*`)
and the payment path uses (`payments.fx_rate` / `fx_locked_at`). Previously the
total was a naive `SUM(expenses.amount)` across currencies: $100.00 USD plus
€200.00 EUR on a USD report reported `300.00 USD`, and that fabricated figure
fed the CFO gate (issue #157).

`app/services/expense_currency.py` owns both layers:

| Layer | Columns | Target currency | Rate locked |
|---|---|---|---|
| Line → report | `expenses.converted_*` | the owning `ExpenseReport.currency` | on create-with-report / amount-or-currency edit / attach / report-currency change |
| Report → reporting base | `expense_reports.reporting_*` | `resolve_reporting_currency(org.settings)` | at **submit** |

Rules that keep the numbers honest:

- **Locked, never recomputed on read.** No read path calls the FX adapter, so a
  market move cannot rewrite a submitted report's total. Re-locking happens only
  on a write that changes what is being converted — and those writes are already
  refused once the report leaves `draft` (`_require_report_unlocked`, issue #155).
- **Unconvertible is excluded, not face-valued.** A foreign line with no usable
  lock contributes nothing to the total and is counted in `unconverted_count`.
  Attaching a line we cannot convert is refused with **422** (currency codes
  only in the message — no PII), and **submit** refuses while any unconverted
  line is attached (legacy rows predating the columns), listing their ids.
- **Detach clears the lock** — it was an expression in *that* report's currency.
- **Same currency is a no-op fetch**: rate `1`, no adapter call, exact face value.
- **Local-first**: the FX provider comes from `Organization.settings.fx` via the
  existing `fx_adapters` registry, defaulting to the deterministic `mock`
  adapter — multi-currency reports work with no cloud account.

`GET /api/expense-reports/{id}/summary` exposes `currency`, `total_exact`,
`unconverted_count`, and a `by_currency[]` breakdown (`original_amount` /
`report_amount` / `count` / `unconverted_count`) alongside the legacy `total`;
`by_category` / `by_status` gain `total_exact` + `unconverted_count`.
`ExpenseResponse` gains `converted_*` and `ExpenseReportResponse` gains
`total_amount_exact` + `reporting_*`, all as **exact decimal strings** (the
pre-existing `amount` / `total_amount` floats stay for client back-compat).

### Migration 0076

`backend/alembic/versions/0076_expense_currency_conversion.py` adds the four
`expenses.converted_*` and four `expense_reports.reporting_*` columns.
Tenant-DB only (both halves gated on the table existing, so it no-ops on the
control plane), idempotent `ADD COLUMN IF NOT EXISTS` / `DROP COLUMN IF
EXISTS`, fanned out by `scripts/migrate_all_tenants.py`. All nullable with **no
backfill**: a same-currency line falls back to its exact face amount, and
inventing a rate for a historical foreign line inside a migration would
fabricate history — it is counted as unconverted and blocks submission until
re-attached (which locks a real rate).

### Migration 0077

`backend/alembic/versions/0077_expense_policy_currency.py` adds
`expense_policies.threshold_currency` (`varchar(3)`, nullable). Tenant-DB only
(gated on the table existing, so it no-ops on the control plane), idempotent
`ADD COLUMN IF NOT EXISTS` / `DROP COLUMN IF EXISTS`, fanned out by
`scripts/migrate_all_tenants.py`. **No backfill** — NULL means "the org's
reporting currency", resolved at evaluation time; see § Threshold currency for
why writing a value here would be both impossible (the reporting currency is
control-plane state) and wrong (the only in-table candidate is a defaulted
`'USD'`).

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

`backend/tests/test_expense_currency.py` (issue #157) — the multi-currency
layer. Pure unit cases over `rollup_report_lines` / `report_amount_for_gate` /
`lock_expense_conversion` (unconverted lines excluded not face-valued, a lock
into a stale currency ignored, exact `Decimal` + 8-dp rate, PII-free error), and
`realdb` end-to-end cases: the issue's exact reproduction ($100.00 USD +
€200.00 EUR on a USD report totals `317.39`, never `300.00`), the summary's
`by_currency` breakdown, rate stability when the org's FX config is re-pointed
mid-flight, 422 on an unconvertible attach, submit blocked by a legacy
unconverted line, re-lock on report-currency change and on a line-currency
edit, lock cleared on detach, and the CFO gate — not dodgeable by splitting
across currencies, comparing in the ORG REPORTING currency (a 4 900 EUR report
is held as USD 5 326.09), failing closed with no reporting figure, honouring
`settings.reporting_currency`, and unchanged for the single-currency case. Plus
the currency-matched pre-approval cover check. All deterministic against the
`mock` FX adapter.

`backend/tests/test_expense_policy.py` (WF3, pure/DB-free) — the policy engine:
category limits, receipt-required, pre-approval-required (+ coverage), per-diem,
category matching (NULL = all), the active flag, mileage reimbursement (Decimal),
report aggregation, the blocking-subset filter, and the **currency dimension**:
`threshold_currency_for` resolution, a €200 EUR expense NOT judged against a USD
100 limit as bare numbers, a locked ¥10 000 → $64.94 conversion clearing a USD
100 limit that bare numbers would have flagged, a lock into a third currency not
reused, the per-threshold fail-closed table above (receipt demanded, receipt
still clears it, pre-approval cover in the expense currency still satisfies,
advisory limits flagged not blocking), and a PII-free violation payload.

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
`draft`; invalid-state 422 guards; exact `Numeric` policy money round-trips;
`threshold_currency` round-trip + normalization (`eur` → `EUR`, `per_diem_currency`
following, `EUROS` → 422, omitted → NULL); and the defect end-to-end on the real
write path — a ¥10 000 JPY expense flagged `unresolved` against USD thresholds
while unattached, then clean once attaching it to a USD report locks a rate.

`backend/tests/test_expense_cards.py` (WF4, `realdb`) — CSV import (rows land,
exact `Numeric` round-trip, shared `import_batch`) + dedupe-skip (re-import and
in-file duplicates), virtual-card sync (charged-only, `vc:` external id) +
idempotent re-run, match-suggestion amount + ±5d date windowing, match/unmatch
round-trip asserting both-sides linkage + `payment_method` (corporate vs
virtual), create-expense-from-card, ignore, already-matched 409, list status
filter, CFO mutation denial (read still allowed), tenant isolation, and audit
rows on the trail.

The frontend `/expenses` workspace is covered by the Playwright specs
`frontend/tests-e2e/expenses/expenses.spec.ts` (create an expense with a
receipt, KPIs update, GL-code it, build + attach + submit a report, export the
CSV), `frontend/tests-e2e/expenses/expense-approval.spec.ts` (WF3 — create a
policy, an expense that violates it shows a badge, build + submit a report, a
different manager approves → `approved`), and
`frontend/tests-e2e/expenses/expense-cards.spec.ts` (WF4 — import a card CSV,
re-import dedupes, sync virtual cards, match a txn to an expense, ignore one).

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
- **WF4 — Corporate card import + reconciliation + virtual-card integration.**
  *(Shipped — see the corporate-card reconciliation routes + reconciliation
  strategy above.)* The `/api/corporate-card-transactions` router imports card
  feeds into `corporate_card_transactions` (idempotent on `external_txn_id`),
  syncs charged virtual-card spend into the same feed (`vc:<provider_card_id>`),
  suggests + applies amount/date+merchant matches to expenses (both-sides FK +
  `payment_method`), and supports unmatch / ignore / create-expense-from-card.
  The frontend adds a Cards tab (KPIs, status filter chips, import-CSV +
  sync-virtual-cards actions, per-row Match/Create-expense/Ignore/Unmatch).
  Deferred: live card-network feed connectors (Stripe Issuing / direct bank
  feeds beyond the existing virtual-card program), auto-apply of high-confidence
  matches, and multi-currency reconciliation.
