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
| `punchout_url` | Punch-out site URL — the supplier hosted-catalog endpoint a buyer punches out to (see Punch-out below) |
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

## Punch-out — live cXML / OCI round-trip

A `punchout` catalog points at a supplier's hosted catalog site (`punchout_url`)
and is **not** populated with `catalog_items`. The live punch-out round-trip is
implemented behind a pluggable adapter family, local-first with a `mock` default
so the whole flow runs under `pnpm dev` with no external supplier or credential.

### Flow

```
buyer  ─ POST /catalogs/{id}/punchout/start ─►  adapter.build_setup_request
                                                  (PunchOutSetupRequest → start URL)
       ◄─ start_url + buyer_cookie ──────────  persist PunchoutSession(pending)
buyer's browser visits start_url at the supplier's site, shops, checks out
supplier ─ POST /catalogs/punchout/return/{slug} ─► adapter.parse_order_message
            (PunchOutOrderMessage cart, HMAC-signed)   (cart → normalized items)
                                                  match BuyerCookie → store cart,
                                                  PunchoutSession(pending→returned)
buyer  ─ POST /catalogs/punchout/sessions/{id}/convert ─► PurchaseRequisition
                                                  (returned → converted, idempotent)
```

On the frontend, a successful convert routes the buyer to
`/requisitions?id=<new_requisition_id>`, which deep-links straight into that
draft requisition's detail modal (the `id` param is transient — consumed once
and scrubbed from the URL).

### Adapters (`services/punchout_adapters/`)

Registry decorator `@register_punchout_adapter`; selection via
`Organization.settings.punchout.provider` → `FEOH_PUNCHOUT_PROVIDER` (default
`mock`). Interface (`base.py`):

- `build_setup_request(ctx: PunchoutSetupContext) -> PunchoutStartResult` — build
  the outbound PunchOutSetupRequest and return the supplier **start URL**.
- `parse_order_message(headers, body) -> PunchoutCart | None` — parse a returned
  cart into normalized `PunchoutCartItem`s (money is `Decimal`); `None` on an
  unparseable body or a missing BuyerCookie (refuse a cart we can't correlate).
- `test_connection() -> bool`.

Registered:

- **`mock`** (in-process, local-first default) — synthesises a start URL off the
  catalog's `punchout_url` + the buyer cookie, and parses either a dev JSON cart
  envelope (`{buyer_cookie, currency, items[]}`) **or** a real cXML
  PunchOutOrderMessage. No supplier, no network.
- **`cxml`** (real) — builds a real cXML PunchOutSetupRequest and parses a real
  PunchOutOrderMessage (`services/punchout_adapters/cxml.py`, reusing the
  e_invoice XXE-hardened parser). The supplier shared secret comes from
  `Organization.settings.punchout.shared_secret` → `FEOH_PUNCHOUT_SHARED_SECRET`
  with **no hardcoded fallback** — an unconfigured adapter **fails closed**
  (`punchout_not_configured`), mirroring the PEPPOL `as4_gateway` posture. The
  OCI shape slots in behind the same interface (`protocol="oci"`).

#### Every cXML `ItemIn` field is read from the sub-element that owns it

`_parse_item_in` resolves the price from `ItemDetail > UnitPrice > Money`, the
description and UoM from inside `ItemDetail`, and the SKU from `ItemID` —
never by scanning the whole `ItemIn` subtree. That scoping is load-bearing:
cXML lets `ItemIn` carry `Shipping`, `Tax`, `SpendDetail` and
`Distribution > Charge` as **siblings** of `ItemDetail`, each with its own
`<Money>` and `<Description>`. A whole-subtree scan let the last one win, so a
line quoting 250.00 with 200.00 of tax booked at 200.00 and described as
"Sales tax" — a plausible price that then flowed into the requisition, the PO,
and the budget's committed spend. An `ItemIn` with no `ItemDetail` yields no
price (`0`) rather than borrowing a sibling block's number: a zero line is
visibly wrong to the buyer approving the requisition, a tax-priced one is not.

### Session lifecycle

`PunchoutSession` (tenant-scoped, migration `0045_punchout_sessions`) carries
`buyer_cookie` (unique correlation token), `status`
(`pending → returned → converted`, plus `expired` / `cancelled`),
`requested_by_user_id`, `start_url`, the returned `cart_items` (JSONB — money as
string-Decimal, no PII), `cart_total` (`Numeric(15,2)`), and
`converted_requisition_id`. `org_id` + `entity_id` scope it like every other
procurement row.

### Endpoints

| Endpoint | Auth | Notes |
|----------|------|-------|
| `POST /catalogs/{id}/punchout/start` | admin / ap_manager / **ap_clerk** | Buyers shop, so a clerk may start. 422 (`catalog_not_punchout` / `no_punchout_url` / `punchout_not_configured`) fails closed. |
| `GET /catalogs/punchout/sessions/{id}` | admin / ap_manager / ap_clerk / cfo | Read the session + (once returned) the cart. |
| `POST /catalogs/punchout/sessions/{id}/convert` | admin / ap_manager / ap_clerk | Returned cart → requisition. **Idempotent + row-locked** (`SELECT … FOR UPDATE` on the session; a session already carrying `converted_requisition_id` returns its existing requisition with `created=false`). Reuses `requisition_service` primitives (`line_total` / `recompute_total` / `next_requisition_number`) — never duplicated. |
| `POST /catalogs/punchout/return/{slug}` | **PUBLIC-by-design** | The supplier cart return. No JWT. |

Every state-changing op writes a `dispatch_audit` row
(`punchout.session_started` / `punchout.cart_returned` /
`punchout.session_converted`).

### The public cart-return endpoint (security)

`POST /api/catalogs/punchout/return/{tenant_slug}` is public-by-design — the
supplier (or the buyer's browser POSTing on its behalf) returns the cart there.
It mirrors the PEPPOL inbound webhook posture exactly:

- a body-size cap before buffering (memory-exhaustion guard,
  `FEOH_PUNCHOUT_RETURN_MAX_BYTES`),
- a **shared-secret HMAC-SHA256** over the raw body is the gate
  (`FEOH_PUNCHOUT_RETURN_SIGNING_SECRET`; verified via the shared
  `webhook_security.verify_hmac_sha256`). An empty secret falls back to
  `FEOH_DEBUG` (local-dev convenience — the BuyerCookie match is then the sole
  gate); deployed envs set the real secret via sops. Boot refuses
  (`app/main.py::lifespan`, `FEOH_DEBUG=false`) if `FEOH_PUNCHOUT_PROVIDER` is
  live (non-`mock`) without the secret set — mirrors the PEPPOL-inbound boot
  guard,
- the tenant is in the **URL path** (never a spoofable header),
- the **BuyerCookie** (in the body, cross-checked against the query string)
  correlates the cart to exactly one **pending** session — a redelivery onto an
  already-returned session is dropped,
- **every rejection path returns 204 silently** (a 4xx would enumerate tenants /
  cookies / probe the secret); no supplier secret or cart value is ever logged.

### Env vars

| Variable | Default | Purpose |
|----------|---------|---------|
| `FEOH_PUNCHOUT_PROVIDER` | `mock` | Adapter — `mock` (in-process default) \| `cxml`. Per-org override `Organization.settings.punchout.provider`. |
| `FEOH_PUNCHOUT_SHARED_SECRET` | (empty) | cXML supplier credential — **no hardcoded fallback**; sops in deployed. Per-org override `…punchout.shared_secret`. |
| `FEOH_PUNCHOUT_RETURN_SIGNING_SECRET` | (empty) | HMAC key the supplier signs the cart-return POST with — **no hardcoded fallback**; boot refuses if `FEOH_PUNCHOUT_PROVIDER` is live (non-`mock`) without it; the committed `.env.development` sets a NON-secret dev value. |
| `FEOH_PUNCHOUT_RETURN_MAX_BYTES` | `4194304` | Hard cap on the cart-return body before parsing. |

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

`backend/tests/test_punchout.py` (realdb) — the punch-out round-trip: start
(mock → start URL + pending session; non-punch-out / no-URL 422), the public
secret-gated cart return (BuyerCookie + HMAC match stores the exact-`Decimal`
cart; bad signature / unknown cookie / cookie mismatch → silent 204, no state
change), convert (returned cart → requisition, exact total, idempotent + row-
locked replay; pending session 422), RBAC (CFO read-only can't start/convert but
can read), and tenant isolation.
