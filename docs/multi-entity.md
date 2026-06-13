# Multi-entity (subsidiaries within a tenant)

Multi-entity lets one organization run several **legal entities / subsidiaries**
inside its single tenant database. This is distinct from multi-tenancy: the
tenant boundary is still the per-org database (`app/tenant.py`,
`X-Tenant-Slug` → `ap_<slug>`). Entities subdivide data *within* one tenant.

**Status: Phase 1 (foundation) shipped.** The schema, the per-tenant Default
entity, and admin CRUD exist; query scoping + the entity switcher are Phase 2
(not yet wired — every list/aggregate still returns all rows regardless of
entity). See `docs/roadmap.md` → Priority 5 → Multi-Entity.

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

Because there's exactly one entity and no query scoping yet, Phase 1 is a pure
no-op behaviorally — a single-entity tenant behaves exactly as before.

## API

`/api/entities` (`app/api/entities.py`):

| Method | Path | Role | Purpose |
|--------|------|------|---------|
| GET | `/api/entities` | any authenticated | list (default first); `?active_only` |
| POST | `/api/entities` | admin | create (validates slug, 409 on dup) |
| PATCH | `/api/entities/{id}` | admin | rename / currency / (de)activate — can't deactivate the default |

Reads are open to all roles because the Phase 2 entity selector needs the list.

## Phase 2+ (not yet built)

- **Scoping + selector** — `get_entity_id` dependency (reads `X-Entity-ID`,
  validates it belongs to the tenant, accepts `all`) + an
  `apply_entity_scope(query, Model, entity_id)` helper across list/aggregate
  endpoints; sidebar entity switcher; per-entity dashboards + an "All entities
  (consolidated)" view (currency-aware via the multi-currency reporting
  rollups). Seeded demo rows already sit under the Default entity.
- **Phase 3** — entity-level COA wired into extraction + bulk-recode.
- **Phase 4** — inter-company invoice routing (entity A payable by entity B).
