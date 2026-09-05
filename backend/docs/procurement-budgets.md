# Procurement — Budget tracking

Track spend against **department / project / cost-center / GL-account** budgets.
Part of the Procurement / Requisitions module; shares the data model in
`app/models/procurement.py` (migration `0041_procurement`).

A budget is **financial config**: an allocation for one dimension/period. The
spend against it is **computed on read** from requisitions, POs, and invoices —
there is no stored running total, so the numbers never drift out of sync with
the underlying activity.

- Model: `app/models/procurement.py::Budget`
- Service (spend rollup): `app/services/budget_service.py`
- API: `app/api/budgets.py` (`/api/budgets`)
- Schemas: `app/schemas/budget.py`
- Frontend: `routes/budgets/+page.svelte`, `lib/api/budgets.ts`,
  `lib/types/budget.ts`, `lib/components/modals/BudgetModal.svelte`
- Tests: `tests/test_budgets.py`

## Budget dimensions

`BudgetDimension` (StrEnum): `department`, `project`, `cost_center`,
`gl_account`. `dimension_value` is the specific value tracked (e.g.
`"Engineering"`, `"Project X"`, `"CC-100"`, `"6000"`). `period` is a free-form
label (`"2026"`, `"2026-Q2"`) bounded by optional `period_start` / `period_end`.
`amount` is the allocation (`Numeric(15, 2)`, never float). Every budget carries
a nullable `entity_id` (subsidiary scope, `EntityMixin`).

## Compute-on-read spend model

`services/budget_service.compute_budget_spend(db, budget)` returns a
`BudgetSpend` (exact `Decimal`). Every aggregate is a Postgres `SUM` over a
`Numeric` column coerced to `Decimal`; the API serialises to `float` for display
only (mirrors `api/expenses.py::report_summary`).

| Term | Definition |
|------|-----------|
| **allocated** | `budget.amount` — the cap for this dimension/period. |
| **committed** | Earmarked but not yet invoiced. Two legs, summed: (1) `PurchaseRequisition.total` for requisitions linked to the budget (`budget_id == budget.id`) in an **open-commitment** status — `submitted`, `pending_approval`, `approved` (live demand not yet a PO); (2) `PurchaseOrder.total` for the POs those budget-linked requisitions converted into (`converted` reqs joined via `converted_po_id`), excluding cancelled/closed/voided POs. A converted req is counted via its PO (leg 2), **not** the req (leg 1) — `converted` is deliberately omitted from leg 1 so the two never double-count. |
| **actual** | Realised invoice spend matched to the dimension. Invoices have no `budget_id`, so they're attributed by column — one per dimension, all four covered: `cost_center` → `Invoice.cost_center`, `gl_account` → `Invoice.gl_account`, `department` → `Invoice.department`, `project` → `Invoice.project`. Only invoices in a realised status count — `approved`, `sent_to_erp`, `posted_in_erp`, `payment_scheduled`, `paid`, `done` — so a new/rejected invoice never inflates actual. When the budget sets both `period_start`/`period_end`, actual is further bounded to invoices dated inside that window (so a Q1 and a Q2 budget on the same dimension don't both report all-time spend). |
| **remaining** | `allocated - committed - actual` (negative = overspend). |
| **utilization_pct** | `(committed + actual) / allocated * 100`, 2 dp. `0` when allocated is 0. |

**Every leg is scoped by currency.** The legs never convert, so a EUR and a USD
invoice on the same dimension are **not** summed as equal (POs carry no
currency column, so the PO leg filters on the source requisition's currency).

**Only the `actual` (invoice) leg is scoped by entity.** Attribution there is a
fuzzy free-text `dimension_value` match, so narrowing to the budget's own
`entity_id` is genuinely protective — a subsidiary's budget must not pick up a
sibling's spend on a shared cost-center string. The two **committed** legs key
off `PurchaseRequisition.budget_id`, an unambiguous human-declared link, where
the same filter could only *remove* deliberately-linked demand: `committed` read
`0` and `/budgets/check` answered `would_overspend: false` for headroom already
spoken for. The FK is the scope.

**The link is validated at write time.** `POST` / `PATCH /api/requisitions`
404s an unknown `budget_id` (and `vendor_id` / `contract_id` — each used to
reach an FK violation at flush and surface as a 500) and 422s a `budget_id`
whose budget is denominated in another currency, including when only
`currency` changes on a requisition that already names one. Without that, a
requisition could name a budget the rollup then silently dropped. The currency
predicate stays on the read legs as a safety net for links made before the
guard — summing two currencies' face values would be worse than excluding the
row.

### `department` / `project` actuals — resolved

Previously `actual` read `0` for `department` / `project` budgets because
invoices carried no matching column. Invoices now carry indexed
`Invoice.department` and `Invoice.project` columns (migration
`0044_invoice_department_project`), set on invoice create/update like
`cost_center`. `actual` therefore sums realised invoices for **all four**
dimensions — `cost_center`, `gl_account`, `department`, `project` — via the
`_DIMENSION_MATCH_COLUMN` map in `services/budget_service.py`.

## Endpoints

All under `/api/budgets`. **RBAC:** read = `admin` / `ap_manager` / `cfo`;
mutate = `admin` / `cfo` (the CFO owns budgets — `ap_clerk` has no access).
Every list/read is entity-scoped (`X-Entity-ID`) and tenant-isolated
(per-tenant DB session); every mutation writes a `dispatch_audit` row.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/budgets` | Paginated, entity-scoped list. Filters: `dimension`, `period`; `search` (ILIKE on name + dimension_value). |
| GET | `/budgets/summary` | Whole-set KPI rollup — count + per-currency allocation totals (exact decimal strings, never a cross-currency SUM, never FX-converted). Shares `_budget_list_filters` with the list so the `/budgets` "Total allocated" card can't describe a different set than the table. Groups are ordered by currency code, and each page headlines the first one with the rest on a sub-line — so which currency headlines is alphabetical, never largest-total. |
| POST | `/budgets` | Create. Audited `budget.created`. |
| GET | `/budgets/rollup` | **Org-wide budget vs actual** — whole-set allocated / committed / actual / remaining, grouped by currency, computed on read. Shares `_budget_list_filters` + the entity scope with the list. Money is exact decimal strings; never summed across currencies, never FX-converted on a read. Rendered on `/cfo`. |
| GET | `/budgets/check` | Overspend pre-check (query `budget_id` + `amount`). |
| GET | `/budgets/{id}` | Detail. |
| GET | `/budgets/{id}/spend` | Computed allocated / committed / actual / remaining / utilization rollup. |
| PATCH | `/budgets/{id}` | Update changed fields. Audited `budget.updated`. |
| DELETE | `/budgets/{id}` | Delete. Audited `budget.deleted`. |

### `GET /budgets/rollup` contract

The CFO's consolidated view. Before it, the only budget-vs-actual surfaces were
the standalone `/budgets` page and the per-budget `GET /{id}/spend`, so an
org-wide "allocated vs committed vs actual" meant opening budgets one at a time.

```
GET /api/budgets/rollup?dimension=&period=&search=
→ {
    budget_count,                # rows in the filtered set
    by_currency: [{
      currency, budget_count,
      allocated, committed, actual, remaining,   # exact decimal STRINGS
      utilization_pct,           # string, or null — see below
      over_budget_count,         # budgets whose remaining went negative
      excluded_row_count
    }],
    excluded_row_count,          # whole-set total of the per-currency counts
    insufficient_data            # true when the filtered set holds no budgets
  }
```

- **Computed on read**, like the rest of this router: `compute_budget_rollup`
  folds `compute_budget_spend` over the matching budgets. There is no stored
  running total to drift, and the cost is three aggregate queries per budget.
  The set is deliberately the **whole filtered set**, not a page — a partial
  rollup presented as an org-wide total is exactly the dishonesty the
  per-currency grouping exists to prevent.
- **Money is grouped by currency and serialised as exact decimal strings.**
  Unlike the per-budget `BudgetSpendResponse` (which predates the string
  convention and stays `float` for API back-compat), these are org-wide totals
  read off a dashboard, so they never round-trip through a binary float. Rows
  are ordered by currency code ascending — deterministic, and stable as the
  amounts move.
- **`utilization_pct` is `null`, never `"0.00"`, when a currency allocates
  nothing at all.** "0% of the budget is used" and "there is no budget to use"
  are opposite facts and 0% renders as the reassuring one — the same rule
  `analytics.compute_discount_capture` applies to `capture_rate_pct`
  (`../../docs/decisions.md` §34). `insufficient_data` is the same distinction
  at the top level: an empty set reads as "no budgets", not a row of confident
  zeros.
- **RBAC + scope** are the list's own: read `admin` / `ap_manager` / `cfo`,
  entity-scoped via `X-Entity-ID`, tenant-isolated per-tenant DB session. It
  runs the SAME `_budget_list_filters`, so the rollup can never describe a
  different set than the table.

### Currency-excluded rows are disclosed, not forgotten

Every spend leg is scoped by currency (the legs never convert), so a
requisition, PO or invoice denominated in another currency than its budget is
**excluded** from `committed` / `actual`. That is the right call — summing
unlike face values would be worse — but a figure that quietly left rows out
reads exactly like a complete one.

`BudgetSpend.excluded_row_count` counts them, across all three legs, using a
Postgres `FILTER` clause on the same aggregate query (so it costs no extra
round trip). A currency that is NULL counts as excluded too — `(currency = 'X')
IS NOT TRUE`, not `<> 'X'`, which would swallow the NULL. The count surfaces on
BOTH `GET /{id}/spend` and `GET /rollup` (per currency and whole-set), and
`/cfo` renders it as a `role="alert"` line above the table naming the figures
as a **floor** — the same idiom the cash-position card uses for its
`unconverted_count` outflows. See `../../docs/decisions.md` §35.

### `GET /budgets/check` contract

Called by the requisition flow before submit to warn on overspend:

```
GET /api/budgets/check?budget_id=<uuid>&amount=1500.00
→ {
    budget_id, amount,
    allocated, committed, actual,
    remaining,                 # current headroom = allocated - committed - actual
    remaining_after,           # remaining - amount (what would be left)
    would_overspend,           # remaining_after < 0
    currency
  }
```

`would_overspend` is advisory — the check does not block a write; the caller
decides whether to warn or hard-stop. All math is `Decimal`; an unknown
`budget_id` is a 404.

## Frontend

**`/cfo` — org-wide budget vs actual.** A `Budget vs actual` card over
`GET /budgets/rollup`, rendered OUTSIDE the cash-flow `{#if}` (an independent
fetch taking none of that page's controls, so a failed forecast must not hide
the only consolidated allocated-vs-spent view — same reasoning as
`ScheduledReportsPanel`). One table row per currency, each formatted in its
OWN currency; an overspent `remaining` is tinted and a breach banner counts the
over-budget budgets across currencies (a COUNT may cross currencies; the
amounts beside it may not). The `excluded_row_count` disclosure sits above the
table. Pure display helpers + their vitest guard live in
`routes/cfo/budgetRollupSummary.{ts,test.ts}`; e2e in
`tests-e2e/cfo/budget-rollup.spec.ts`.

`/budgets` workspace: a KPI row (total allocated / budget count / dimension
count), a `FilterChips` row by dimension + a `SearchBox`, and a `DataTable` of
budgets. Clicking a row opens `BudgetModal`, which loads the per-budget spend
rollup (`GET /{id}/spend`) and renders it as KPI cards + a utilization bar
(green < 80% / amber ≥ 80% / red ≥ 100%) over the allocated/committed/actual/
remaining breakdown. Create/edit/delete are gated to admin/cfo.
