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
| **actual** | Realised invoice spend matched to the dimension. Invoices have no `budget_id`, so they're attributed by column — one per dimension, all four covered: `cost_center` → `Invoice.cost_center`, `gl_account` → `Invoice.gl_account`, `department` → `Invoice.department`, `project` → `Invoice.project`. Only invoices in a realised status count — `approved`, `sent_to_erp`, `posted_in_erp`, `payment_scheduled`, `paid`, `done` — so a new/rejected invoice never inflates actual. |
| **remaining** | `allocated - committed - actual` (negative = overspend). |
| **utilization_pct** | `(committed + actual) / allocated * 100`, 2 dp. `0` when allocated is 0. |

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
| POST | `/budgets` | Create. Audited `budget.created`. |
| GET | `/budgets/check` | Overspend pre-check (query `budget_id` + `amount`). |
| GET | `/budgets/{id}` | Detail. |
| GET | `/budgets/{id}/spend` | Computed allocated / committed / actual / remaining / utilization rollup. |
| PATCH | `/budgets/{id}` | Update changed fields. Audited `budget.updated`. |
| DELETE | `/budgets/{id}` | Delete. Audited `budget.deleted`. |

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

`/budgets` workspace: a KPI row (total allocated / budget count / dimension
count), a `FilterChips` row by dimension + a `SearchBox`, and a `DataTable` of
budgets. Clicking a row opens `BudgetModal`, which loads the per-budget spend
rollup (`GET /{id}/spend`) and renders it as KPI cards + a utilization bar
(green < 80% / amber ≥ 80% / red ≥ 100%) over the allocated/committed/actual/
remaining breakdown. Create/edit/delete are gated to admin/cfo.
