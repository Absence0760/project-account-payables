# Multi-entity (subsidiaries within a tenant)

Multi-entity lets one organization run several **legal entities / subsidiaries**
inside its single tenant database. This is distinct from multi-tenancy: the
tenant boundary is still the per-org database (`app/tenant.py`,
`X-Tenant-Slug` → `ap_<slug>`). Entities subdivide data *within* one tenant.

**Status: complete (Phases 1–4).** On top of the Phase 1 schema, requests scope
to a selected subsidiary via the `X-Entity-ID` header, new rows are stamped with
an `entity_id`, a sidebar switcher drives it, and the full CFO analytics surface
(`analytics.py`) is scoped (Phases 2/2b). Phase 3 wired the entity-level chart of
accounts into the AI extraction GL catalog + bulk-recode validation and taught
the workflow engine to pick the entity's own definition. Phase 4 added
inter-company invoice routing and a consolidated cross-entity report. See
`docs/roadmap.md` → Priority 5 → Multi-Entity, and *Remaining phases* below for
the per-feature detail.

## Data model

`Entity` (`app/models/entity.py`) is tenant-scoped (lives in each tenant DB):

| Column | Notes |
|--------|-------|
| `id` | uuid PK |
| `organization_id` | `TenantMixin` (the tenant DB is one org; carried for consistency) |
| `name` | display name |
| `slug` | unique within the tenant (`uq_entities_slug`) |
| `currency` | ISO 4217; `NULL` → use the org reporting currency (`resolve_reporting_currency`) |
| `is_default` | exactly one per tenant — enforced by partial unique index `uq_entities_one_default` |
| `is_active` | soft-deactivate; the default entity can't be deactivated |
| `settings` | JSONB, `server_default '{}'` |

Business tables carry a **nullable** `entity_id` FK to `entities.id` via
`EntityMixin` (`app/models/base.py`). The tables that gain it (parent tables;
children inherit through their parent FK):

`invoices`, `vendors`, `purchase_orders`, `goods_receipts`, `payments`,
`payment_runs`, `credit_memos`, `exceptions`, `gl_accounts`,
`workflow_definitions`, `virtual_cards`.

`EntityMixin.entity_id` uses `@declared_attr` (not a plain class attribute like
`TenantMixin.organization_id`) because each table needs its own `ForeignKey`
instance.

### Naming: `entity_id` is overloaded — read carefully

`AuditLog.entity_id` / `entity_type` and `Notification.entity_id` /
`entity_type` already existed and mean **"the row this audit/notification is
about"** (an invoice/vendor/card id), NOT a subsidiary. They are on different
tables, so there's no SQL collision, but don't confuse them: a new
`entity_id` on a *business* table is the subsidiary; on `audit_log` /
`notifications` it's the audited/notified row. The audit table is immutable
(SOX triggers) and widely consumed, so it was intentionally **not** renamed.

### Chart of accounts: shared + per-entity overrides

`GLAccount.entity_id` is the deliberate exception to the backfill: a **NULL**
`entity_id` means the account is **shared across every entity** (today's
behavior), and a set `entity_id` makes it entity-specific. So an entity's
effective chart is `shared (NULL) ∪ its own`. Phase 3 wires this into the
extraction catalog and bulk-recode validation.

## How every tenant gets a Default entity

- **Existing tenants** — migration `0029_entities` (tenant-only, gated on the
  `invoices` table) creates `entities`, inserts one `Default` entity (deriving
  `organization_id` from any populated table), adds `entity_id` columns + FKs +
  indexes, and backfills every business row to the Default entity — **except
  `gl_accounts`**, left NULL (= shared). Idempotent (`IF NOT EXISTS`,
  `pg_constraint` guards, no-op insert when a default already exists).
- **Fresh tenants** — `tenant_provisioning._create_tenant_tables(db_name,
  organization_id=...)` seeds the Default entity right after `create_all`
  (which builds the schema from the models). The `Entity` model carries
  `server_default`s matching the migration so these raw INSERTs work.
- **Seed / e2e** — `scripts/seed.py::finalize_entities` does the same for demo
  + e2e tenants after their data is seeded.
- **Test harness** — `tests/conftest.py` re-seeds the Default entity after each
  per-test TRUNCATE (which wipes `entities` along with the rest), so every
  test starts from the single-entity baseline.

A single-entity tenant still behaves exactly as before: with one entity the
switcher is hidden, the `X-Entity-ID` header is never sent, and every endpoint
returns the consolidated (all-rows) view.

## Phase 2 — request scoping + entity switcher

### The `X-Entity-ID` contract

The frontend sends an optional `X-Entity-ID` header. The backend resolves it in
`app/tenant.py`:

- **absent**, or the literal **`all`** → `None` = the consolidated view (every
  entity's rows). Absent is the backward-compatible default, so any client that
  predates multi-entity keeps seeing everything.
- a **UUID** → validated against this tenant's `entities` table. An id that
  doesn't exist here (including another tenant's entity id) is a **400**, never
  a silent fall-through to "all" — a leaked header can't widen scope.

Three primitives back this (all in `app/tenant.py`):

| Primitive | Use |
|-----------|-----|
| `get_entity_id` (dependency) | resolves the header → validated entity UUID or `None`. Read-side scoping + the GL-account create rule. |
| `get_write_entity_id` (dependency) | the entity a *new* row lands under: the selected entity, else the tenant's default entity (never NULL, so a new row is always visible in some entity-scoped view). |
| `apply_entity_scope(query, Model, entity_id, *, include_shared=False)` | filters a `select()` to one entity; passthrough when `entity_id is None`. `include_shared=True` also admits NULL rows — only the GL chart uses it. |

### What's scoped (read) + how new rows are stamped (write)

| Area | List / aggregate scoped | New-row `entity_id` |
|------|-------------------------|----------------------|
| Invoices | `GET /invoices`, `/invoices/counts` | create + upload + CSV import → write-entity; portal submit → vendor's; PO-flip → PO's; email intake → default |
| Vendors | `GET /vendors` | create + ERP sync + CSV import → write-entity; AI-extraction match-miss → invoice's |
| Payments | `GET /payments`, `/payments/queue`, `/payments/summary`, `/payments/runs/` | payment → its invoice's; payment run → write-entity (each payment still follows its own invoice) |
| Purchase orders | `GET /purchase-orders` | ERP sync → write-entity |
| Goods receipts | `GET /goods-receipts` | (no API create path) |
| Credit memos | `GET /credit-memos` | create → the vendor's entity |
| Exceptions | `GET /exceptions`, `/exceptions/summary` | all 4 creation sites (warnings, extraction dup/fail, review reject) → the invoice's entity |
| GL accounts | `GET /gl-accounts` — **shared (NULL) ∪ entity's own** (`include_shared=True`) | create + ERP sync use `get_entity_id`: consolidated view → NULL (shared), entity selected → entity-specific |
| Virtual cards | `GET /cards`, `/cards/dashboard` (active + spend) | generate → the invoice's entity |
| Dashboard | `GET /dashboard` — every Invoice/Payment/Exception query | n/a |
| CFO analytics (2b) | `GET /analytics/{cashflow_forecast,cashflow_whatif,cash_position,cfo,drill/spend_concentration,drill/dpo,export/{report}}` + `POST /analytics/forecast_variance` — every Invoice/Payment/PaymentSchedule(via Invoice)/PurchaseOrder/Exception query | n/a |

Control-plane `CardRebate` KPIs (payments summary, card dashboard, dashboard
+ CFO rebate yield) stay **org-wide** — rebates live in the control DB, cross-DB from the
tenant's entities. Invoice-id-keyed metrics (dashboard processing-time) inherit
the scope from the scoped invoice query they consume.

### Frontend

- `frontend/src/lib/entity.ts` — tenant-scoped localStorage selection (key
  `selected_entity_id:<slug>`, so a stale entity id never leaks across
  subdomains). `getSelectedEntityId()` returns `null` for the `all` view.
- `frontend/src/lib/api.ts` — sends `X-Entity-ID` on every request/blob when a
  specific entity is selected.
- `frontend/src/lib/stores/entity.svelte.ts` — loads `GET /api/entities`, tracks
  the selection, resets a stale selection to consolidated, and `select()`
  persists + reloads (pages fetch in `$effect`, not SvelteKit `load`, so a hard
  reload is the simplest correct way to re-scope the whole app at once).
- `frontend/src/lib/components/layout/EntitySwitcher.svelte` — sidebar dropdown
  (All entities + each entity, Default first). Renders **only when the tenant
  has >1 entity**, so a single-entity tenant sees the pre-multi-entity UI.

### Deferred from Phase 2 → delivered in Phase 3

- **Per-entity workflow selection.** Originally deferred because scoping the
  `workflow_definitions` list without teaching the engine to *pick* the entity's
  workflow would be incoherent. Now done: `create_workflow_instance` resolves the
  entity's definition (shared NULL fallback) and one default per `(org, entity)`
  is enforced. See *Phase 3 — entity-level COA + per-entity workflow* below.

## API

`/api/entities` (`app/api/entities.py`):

| Method | Path | Role | Purpose |
|--------|------|------|---------|
| GET | `/api/entities` | any authenticated | list (default first); `?active_only` |
| POST | `/api/entities` | admin | create (validates slug, 409 on dup) |
| PATCH | `/api/entities/{id}` | admin | rename / currency / (de)activate — can't deactivate the default |

Reads are open to all roles because the Phase 2 entity selector needs the list.

## Phase 3 — entity-level COA + per-entity workflow (shipped)

### Entity-level chart of accounts (consumers)

The `GET /api/gl-accounts` list already returned shared ∪ entity (`include_shared=True`).
Phase 3 extended the same rule to the two places that *consume* the chart and
previously filtered by `organization_id` only:

- **AI extraction GL catalog** (`services/extraction.py`) — the GL-account hint
  passed to the extractor is now scoped to `shared (NULL) ∪ the invoice's own
  entity_id`, so the model never sees another subsidiary's codes.
- **Bulk re-code validation** (`services/gl_recode.py`) — because one bulk run
  spans invoices in different entities, validity is resolved **per-invoice-entity**:
  a candidate GL code applies iff the account is shared or belongs to that
  invoice's entity. An entity-B-only code is rejected for an entity-A invoice.

Single-entity tenants are a no-op (every account is shared or under the one entity).

### Per-entity workflow selection

`workflow_engine.get_or_create_workflow_definition(db, organization_id, entity_id=None)`
resolves the definition by precedence: the entity's own active definition
(`is_default` first) → a shared/org-wide active definition (`entity_id IS NULL`)
→ auto-create a shared default. `create_workflow_instance` passes
`invoice.entity_id` through. At most one `is_default` per `(organization_id,
entity_id)` is enforced by the partial unique index
`uq_workflow_definitions_one_default` on `(organization_id, COALESCE(entity_id,
'00000000-…'::uuid)) WHERE is_default = true` (migration `0050`, mirrored in the
model's `__table_args__` so fresh `create_all` tenants get it; the migration
defensively demotes any pre-existing duplicate defaults before creating the
index). The snapshot pattern is unchanged. The org-wide "active steps" UI surface
is not yet entity-scoped (a frontend follow-up).

## Phase 4 — inter-company invoice routing + consolidated reporting (shipped)

### Inter-company invoice routing

When entity A bills entity B inside the same tenant, the mirror **payable** is
generated under the counterparty entity so both subsidiaries' books reflect it.
`Invoice` gains two nullable columns (migration `0051`): `counterparty_entity_id`
(FK → `entities.id`, the other subsidiary) and `intercompany_mirror_id`
(self-FK → `invoices.id`, the bidirectional origin↔mirror link).
`services/intercompany.route_intercompany_invoice` creates the mirror under
`entity_id = counterparty_entity_id`, copies the **exact `Decimal` amount** /
currency / vendor, prefixes the number `IC-`, enters the normal workflow, and
writes a PII-free `invoice.intercompany_routed` audit row on **both** invoices.
It is **idempotent** on `intercompany_mirror_id` — a second call returns the
existing mirror, never a duplicate. Surfaced at `POST
/api/invoices/{id}/route-intercompany` (admin / ap_manager; self-billing → 400).
See `backend/docs/inter-company.md`.

### Consolidated reporting across entities

`GET /api/analytics/by-entity` (admin / CFO) returns a per-entity rollup (total
spend, outstanding, invoice count, open exceptions, open-PO amount — money as
string-Decimal) plus a `consolidated` block computed with `entity_id=None` as a
cross-check (it equals the sum across entities). It deliberately **ignores
`X-Entity-ID`** — it reports every entity at once — and reuses the same scoped
helpers as `/analytics/cfo`. The `/cfo` dashboard renders it as a "By entity"
breakdown table (hidden for single-entity tenants).
