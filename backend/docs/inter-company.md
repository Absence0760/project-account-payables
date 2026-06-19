# Inter-company invoice routing (multi-entity)

When two legal entities / subsidiaries of the **same tenant** transact, one
entity bills the other (an *inter-company charge*). Inter-company routing
generates the mirror **payable** under the counterparty entity, linked back to
the origin, so each subsidiary's books reflect the transaction.

This is a multi-entity feature — entities subdivide data *within* a tenant DB,
they are not the tenant boundary. See `../../docs/multi-entity.md`.

## Data model (`app/models/invoice.py`)

Two nullable columns on `Invoice`:

| Column | Type | Meaning |
|--------|------|---------|
| `counterparty_entity_id` | FK → `entities.id`, nullable | Set when this invoice is an inter-company charge; names the OTHER entity. On the generated mirror it points back at the origin's entity. |
| `intercompany_mirror_id` | self-FK → `invoices.id`, nullable | Links an origin invoice to its generated mirror payable, set on **both** rows. Also the idempotency guard. |

Both are NULL on ordinary (non-inter-company) invoices. Migration `0051`
(tenant-DB only, idempotent `ADD COLUMN IF NOT EXISTS` + `pg_constraint`-guarded
FKs, mirroring `0029_entities`) adds them; `create_all` on fresh tenants matches.

## Service (`app/services/intercompany.py`)

`route_intercompany_invoice(db, invoice, *, actor_id=None) -> Invoice`:

1. **Precondition** — `invoice.counterparty_entity_id` is set and `!=`
   `invoice.entity_id` (a subsidiary can't bill itself). Violations raise
   `ValueError` (the route maps it to a 400).
2. **Idempotent** — if `invoice.intercompany_mirror_id` is already set, the
   mirror exists; it's loaded and returned. A second call never creates a
   duplicate payable. (No money moves — the mirror enters the approval queue at
   `new` — but a duplicate payable is a real accounting problem, so the guard is
   mandatory.)
3. **Mirror creation** — a new `Invoice` under `entity_id =
   counterparty_entity_id`, copying `amount` (exact `Decimal`, never float),
   `currency`, `vendor_name`, and `invoice_number` prefixed `IC-`. Its
   `counterparty_entity_id` points back at the origin's entity;
   `intercompany_mirror_id` is set on both rows. Status `new` — it enters the
   normal workflow via `workflow_engine.create_workflow_instance`, NOT past the
   state machine.
4. **Audit** — a PII-free `invoice.intercompany_routed` row on **both** invoices
   (ids + entity ids only) via `dispatch_audit`.

## API (`app/api/invoices.py`)

`POST /api/invoices/{id}/route-intercompany`

- Body: `{ "counterparty_entity_id": "<uuid>" }` (`RouteIntercompanyRequest` in
  `app/schemas/invoice.py`).
- RBAC: `admin` / `ap_manager` (treasury-ish control; clerks excluded). Every
  `/api` route carries an auth dependency.
- Validates the counterparty is a real entity in **this** tenant (the `entities`
  table is tenant-local, so an unknown id can't point at another tenant's
  subsidiary — same guard `tenant.get_entity_id` uses), sets it on the invoice,
  then calls `route_intercompany_invoice`.
- Returns the **mirror** invoice via `InvoiceResponse` (which now surfaces
  `counterparty_entity_id` + `intercompany_mirror_id`).
- Idempotent at the boundary — calling twice returns the same mirror.

## Tests

`backend/tests/test_intercompany.py` (opt-in `realdb`):

- mirror created under the counterparty entity with the exact `Decimal` amount +
  bidirectional link
- idempotency: second call returns the same mirror, invoice count unchanged
- self-billing (counterparty == own entity) → 400, no mirror
- RBAC: ap_clerk → 403
