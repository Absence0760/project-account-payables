# Purchase Requisitions

The requisition vertical of the Procurement module: a buyer raises a purchase
**requisition** (header + line items), routes it through a lightweight approval
state machine, and — once approved — converts it into a `PurchaseOrder`.
Roadmap items: *Purchase requisition creation and approval* +
*Requisition-to-PO conversion*.

Data model (shared foundation, migration `0041_procurement`):
`PurchaseRequisition` + `RequisitionLineItem` in
`backend/app/models/procurement.py`. See `backend/docs/procurement.md` for the
module-wide model overview.

## Lifecycle / approval state machine

A simple status machine (NOT a `WorkflowInstance` chain — modelled on
expense-report approval). `RequisitionStatus`:

```
draft ─submit─▶ pending_approval ─approve─▶ approved ─convert─▶ converted (terminal)
  │                   │  └──reject──▶ rejected ──(reopen)──▶ draft
  │                   │
  └───────────────────┴──────── cancel ───────▶ cancelled (terminal)
```

Authoritative transition table:
`backend/app/services/requisition_service.py::VALID_TRANSITIONS`. An invalid
source → target move is a **422** (never a silent no-op), enforced by
`guard_transition`. `converted` and `cancelled` are terminal; `rejected` can be
re-opened back to `draft`.

- **submit** (`draft → pending_approval`) — stamps `submitted_at`.
- **approve** (`pending_approval → approved`) — stamps `approved_by` /
  `approved_at`. Enforces **segregation of duties**: the approver must differ
  from `requester_user_id` (reuses `approval_chain.check_segregation` via an
  attribute shim → **403** on self-approval).
- **reject** (`pending_approval → rejected`) — records `rejection_reason`.
- **cancel** (`draft` / `submitted` / `pending_approval` / `approved →
  cancelled`).

Header `total` is **always recomputed server-side** from the line items
(`sum(quantity × unit_price)`, exact `Decimal`) on create and on every draft
edit — a client-sent total is ignored, so the header can never drift from its
lines. Editing is allowed on **draft only**; a submitted/approved requisition is
locked (so the approver can't have the spend changed under them).

## Convert-to-PO contract + idempotency

`POST /requisitions/{id}/convert-to-po` turns an **approved** requisition into a
`PurchaseOrder` (+ `POLineItem` rows) inheriting the requisition's entity,
vendor, exact `Decimal` total, and lines. The requisition flips to `converted`
and stores `converted_po_id`.

**Idempotent** (the operation creates money-moving artifacts): a requisition
that already carries a `converted_po_id` returns its existing PO with
`created=false` instead of creating a second one — a double-click or retry never
doubles the spend. Converting a non-approved (and not-yet-converted) requisition
is a **422**. The derived PO number is `PO-<requisition_number>` for
traceability. The conversion is audited (`requisition.converted_to_po`).

## Endpoints

All under `/api/requisitions`. Money is `Decimal` in / `float` out. Entity scope
via `X-Entity-ID`; tenant scope via `X-Tenant-Slug` (the per-tenant DB session).

| Method + path | Purpose | Roles |
|---|---|---|
| `GET /requisitions` | List (paginated, entity-scoped, `?status=`, `?search=` on number/title) | admin, ap_manager, ap_clerk, cfo |
| `POST /requisitions` | Create with line items (computes `total`) | admin, ap_manager, ap_clerk |
| `GET /requisitions/{id}` | Detail + line items | admin, ap_manager, ap_clerk, cfo |
| `PATCH /requisitions/{id}` | Edit (**draft only**; `line_items` fully replaces lines, recomputes total) | admin, ap_manager, ap_clerk |
| `DELETE /requisitions/{id}` | Delete | admin, ap_manager, ap_clerk |
| `POST /requisitions/{id}/submit` | `draft → pending_approval` | admin, ap_manager, ap_clerk |
| `POST /requisitions/{id}/approve` | `pending_approval → approved` (SoD enforced) | admin, ap_manager, cfo |
| `POST /requisitions/{id}/reject` | `pending_approval → rejected` (reason) | admin, ap_manager, cfo |
| `POST /requisitions/{id}/cancel` | `→ cancelled` | admin, ap_manager, ap_clerk |
| `POST /requisitions/{id}/convert-to-po` | `approved → converted` + creates PO (idempotent) | admin, ap_manager |

Literal route segments (`submit`, `approve`, `reject`, `cancel`,
`convert-to-po`) hang off `/{id}` and so are unambiguous; the bare `/{id}`
collection routes are declared after the list/create pair (mirrors the expenses
router ordering rule).

## RBAC

Read: `admin` / `ap_manager` / `ap_clerk` / `cfo`. Mutate (create / edit /
delete / submit / cancel): `admin` / `ap_manager` / `ap_clerk`. Approve / reject:
`admin` / `ap_manager` / `cfo`. Convert-to-PO (the money step): `admin` /
`ap_manager`. Every route carries an auth dependency (gated by
`tests/test_rbac.py`); every mutation writes a `dispatch_audit` row
(`requisition.created` / `.updated` / `.deleted` / `.submitted` / `.approved` /
`.rejected` / `.cancelled` / `.converted_to_po`).

## Frontend

- `frontend/src/routes/requisitions/+page.svelte` — workspace list (PageHeader,
  KPI row, status FilterChips, SearchBox, DataTable with clickable rows; per-row
  Submit / Approve / Reject / Convert to PO / Cancel / Delete actions, SoD-gated
  approve).
- `frontend/src/lib/components/modals/RequisitionModal.svelte` — create / detail
  / edit dialog over the shared `Modal`, with an editable line-item grid and a
  live computed total.
- `frontend/src/lib/api/requisitions.ts` + `frontend/src/lib/types/requisition.ts`
  — typed API helpers + types.

## Tests

`backend/tests/test_requisitions.py` (real-DB `realdb` fixture): CRUD + exact
`Numeric` total recompute, the full approval state machine incl. invalid-state
422s, SoD self-approval 403, convert-to-PO + idempotency (second call returns the
same PO, no second PO row), RBAC, and tenant isolation.
