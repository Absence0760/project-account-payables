# Procurement — Catalog management + Guided buying

Part of the Procurement / Requisitions module. This vertical covers two roadmap
items:

- **Catalog management** — supplier catalogs (internal item lists + punch-out
  links).
- **Guided buying** — steer buyers toward preferred vendors / contracts /
  catalog lines before they raise a requisition.

Built on the shared procurement data model (`app/models/procurement.py`,
migration `0041_procurement`). See the consolidated `procurement.md` for the
module overview.

## Data model

Two tables (already shipped in migration `0041_procurement`):

### `catalogs` (`Catalog`)

| Column | Notes |
|--------|-------|
| `name` | Display name |
| `catalog_type` | `internal` (holds `catalog_items`) or `punchout` (external supplier site) |
| `vendor_id` | FK → `vendors` (nullable) — the owning supplier |
| `punchout_url` | Punch-out site URL — **config only** (see Punch-out below) |
| `is_active` | Inactive catalogs are excluded from guided buying |
| `is_preferred` | **Drives guided buying** — a preferred catalog's vendor is a preferred source |
| `description` | Free-form |
| `entity_id` | Subsidiary scope (`EntityMixin`) |
| `organization_id` | Tenant scope |

### `catalog_items` (`CatalogItem`)

A purchasable line in an `internal` catalog: `sku`, `name`, `description`,
`unit_price` (`Numeric(15,2)` — money is exact, never float), `currency`,
`uom`, `vendor_id`, `gl_account_id`, `category`, `is_active`. Items cascade on
catalog delete (`delete-orphan`).

## RBAC

Catalogs are configuration-like (mirrors vendors):

| Action | Roles |
|--------|-------|
| Read catalogs / items / guided-buying | `admin`, `ap_manager`, `ap_clerk`, `cfo` |
| Create / update / delete catalogs + items | `admin`, `ap_manager` |

Every route is behind the auth dependency + `require_roles(...)` (gated by
`tests/test_rbac.py`). Every mutation writes a `dispatch_audit` row
(`catalog.created/updated/deleted`, `catalog_item.created/updated/deleted`).

## Endpoints (`/api/catalogs`)

| Method + path | Purpose |
|---|---|
| `GET /catalogs` | Paginated, entity-scoped list. Filters: `catalog_type`, `is_active`, `is_preferred`, `search` (name ILIKE). Preferred catalogs ranked first. |
| `POST /catalogs` | Create a catalog. |
| `GET /catalogs/guided-buying` | Guided-buying suggestion (see below). Literal segment declared before `/{catalog_id}`. |
| `GET /catalogs/{id}` | Catalog detail with items. |
| `PATCH /catalogs/{id}` | Update catalog fields. |
| `DELETE /catalogs/{id}` | Delete (items cascade). |
| `GET /catalogs/{id}/items` | List a catalog's items (optional `is_active`). |
| `POST /catalogs/{id}/items` | Add an item. |
| `PATCH /catalogs/items/{item_id}` | Update an item. Literal `items` prefix declared before `/{catalog_id}`. |
| `DELETE /catalogs/items/{item_id}` | Remove an item. |

Money serialises `Decimal` in (request) / `float` out (response), matching the
expenses + contracts schemas. Optional `vendor_id` / `gl_account_id` are
validated against the tenant's `vendors` / `gl_accounts` (a cross-tenant or
unknown id is a 404).

### Route-ordering note

The literal segments `guided-buying` and `items/{item_id}` are declared
**before** the parametric `/{catalog_id}` route so they aren't captured as a
`{catalog_id}` UUID (FastAPI matches in declaration order).

## Guided buying

`GET /api/catalogs/guided-buying?category=&vendor_id=&q=` returns a read-only,
**deterministic** steer (no LLM, no external calls). Logic lives in
`app/services/catalog_service.py` so the router stays thin. Every read is
entity-scoped.

Response (`GuidedBuyingSuggestion`):

```jsonc
{
  "preferred_vendors": [        // ranked highest — curated, negotiated sources
    {
      "vendor_id": "...",
      "vendor_name": "Acme Office",
      "reasons": ["preferred_catalog", "active_contract"],
      "contract_id": "...", "contract_number": "CON-1",
      "catalog_id": "...", "catalog_name": "Office Supplies"
    }
  ],
  "in_contract_vendors": [...], // vendors with an active Contract on file
  "items": [...]                // matching active catalog items, preferred first
}
```

**Ranking** (highest buyer-intent first):

1. **Preferred vendors** — own an active catalog flagged `is_preferred`. When a
   `category` is supplied, the catalog must carry an active item in that
   category to qualify. An active `Contract` (if any) is attached so the
   requisition can link it.
2. **In-contract vendors** — have an `active` `Contract` (one row per vendor,
   most-recently-started representing them). Buying on-contract keeps spend
   on-agreement and feeds spend-to-contract tracking.
3. **Matching items** — active items in active catalogs matching `category` /
   `vendor_id` / free-text `q` (ILIKE over name / sku / description); items from
   preferred catalogs are ranked ahead of the rest. Each list is capped (25
   vendors / 50 items) so the suggestion stays a focused steer, not an export.

Filters are all optional and AND-combined; with no filters the result is the
org's preferred / in-contract vendors plus a sample of catalog items.

## Punch-out (config-only — future extension)

A `punchout` catalog stores its supplier site URL in `punchout_url` and is
**not** populated with `catalog_items`. The live cXML / OCI punch-out
round-trip (PunchOutSetupRequest → supplier session → returned cart) is a
**future extension** — this slice persists the URL and surfaces it in the UI
only. When implemented, the round-trip would live in a new punch-out adapter
(mirroring the other pluggable-provider patterns), keeping a `mock` local-first
default.

## Frontend

- `/catalogs` (`frontend/src/routes/catalogs/+page.svelte`) — workspace: search,
  type/preferred filter chips, `DataTable` of catalogs (clickable rows), a
  create modal, and a collapsible **Guided buying** panel (preferred vendors /
  in-contract vendors / matching items).
- `CatalogModal` (`frontend/src/lib/components/modals/CatalogModal.svelte`) —
  create/detail; in edit mode lists, adds, and removes catalog items inline.
- API helpers: `frontend/src/lib/api/catalogs.ts`; types:
  `frontend/src/lib/types/catalog.ts`.

## Tests

`backend/tests/test_catalogs.py` (realdb) — catalog + item CRUD, nested items,
punch-out flag persistence, cascade-on-delete, guided-buying (preferred +
in-contract + item search), RBAC (clerk read-only / can't mutate; CFO can read
guided buying), tenant isolation, and audit rows.
