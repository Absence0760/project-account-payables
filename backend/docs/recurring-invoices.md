# Recurring / Subscription Invoices

Predictable, fixed-cadence spend (rent, SaaS seats, utilities, insurance)
shouldn't need a fresh upload + extraction every period. A
`RecurringInvoiceTemplate` captures the vendor, amount, GL coding, entity and a
simple cadence (monthly / quarterly / annual + day-of-period); the
`recurring_invoices` background sweep generates the next `Invoice` on schedule,
pre-coded and pre-matched, so it lands straight in the approval queue. Common in
Bill.com (recurring bills), Tipalti (subscription spend), Stampli and Airbase —
absent here until this feature.

This is the feature behind roadmap **Priority 1 → Recurring / Subscription
Invoices**.

## Data model

`RecurringInvoiceTemplate` (`app/models/recurring_invoice.py`, table
`recurring_invoice_templates`, migration `0046_recurring_invoices`,
tenant-scoped + `EntityMixin` + `TimestampMixin`). Money is exact: `amount` is
`Numeric(15, 2)` — never float.

| Field | Type | Notes |
|-------|------|-------|
| `id` | uuid PK | |
| `name` | varchar(200) | Human label, e.g. "Acme Towers — monthly rent". Required. |
| `vendor_id` | uuid FK → vendors | Nullable so a template can be drafted before the vendor exists, but the sweep needs it set to generate. Indexed. |
| `vendor_name` | varchar(255) | Denormalised for display (mirrors `Invoice`). |
| `description` | varchar(500) | |
| `amount` | numeric(15,2) | Fixed amount stamped onto each generated invoice. Required to generate. |
| `currency` | varchar(3) | Default `USD`. |
| `gl_account` / `cost_center` / `department` / `project` / `po_number` / `payment_terms` | varchar | Pre-coding carried onto every generated invoice. |
| `cadence` | enum | `monthly` (default), `quarterly`, `annual` — how often the template generates. |
| `day_of_period` | integer | Day-of-month (1–28) the invoice is dated/generated on. Capped at 28 so every month is valid (no Feb-30 clamp guesswork). |
| `start_date` | date | Required. First period the template is eligible for. |
| `end_date` | date | Nullable. After it, the template generates nothing (sweep nulls `next_run_on`). |
| `next_run_on` | date | The next calendar date the sweep should generate for. Advanced after each successful generation. NULL = nothing pending (ended / past `end_date`). Indexed (the sweep's `WHERE`). |
| `last_period_key` | varchar(40) | period_key of the most recently generated invoice (`"2026-06"`, `"2026-Q2"`, `"2026"`). Display + a cheap "already ran this period" guard ahead of the DB unique index. |
| `last_generated_at` | timestamptz | When the last invoice was generated. |
| `generated_count` | integer | Running count of invoices generated. Default 0. The `DELETE` guard checks `> 0`. |
| `status` | enum | `active` (default — generates on schedule) \| `paused` (suspended; skip generation) \| `ended` (terminal). Indexed. |
| `variance_tolerance_pct` | numeric(6,2) | Per-template override of the variance tolerance (percent) used to flag an arrived invoice from this vendor that deviates from `amount`. NULL falls back to the org / platform default. |
| `notes` | varchar(500) | |
| `meta` | jsonb | Free-form bag. No PII / banking data. |
| `organization_id` | uuid | Indexed. |
| `entity_id` | uuid FK → entities | From `EntityMixin`; the generated invoices inherit it. |
| `created_at` / `updated_at` | timestamptz | From `TimestampMixin`. |

The status / cadence string constants live on the model
(`STATUS_ACTIVE`/`_PAUSED`/`_ENDED`, `CADENCE_MONTHLY`/`_QUARTERLY`/`_ANNUAL`);
the Pydantic `Cadence` / `TemplateStatus` `StrEnum`s in
`app/schemas/recurring_invoice.py` mirror them on the wire.

## Idempotency (the generation guard)

The link + idempotency live on the **invoice** side, added by migration `0046`
alongside the template table:

- `invoices.recurring_template_id` — nullable FK → `recurring_invoice_templates`
  (the generated invoice's link back to its template), indexed.
- `invoices.recurring_period_key` — varchar(40), the period this invoice
  satisfies.
- **`uq_invoice_recurring_period`** — a **partial unique index** on
  `(recurring_template_id, recurring_period_key) WHERE recurring_template_id IS
  NOT NULL`.

That partial unique index is the idempotency mechanism: **at most one invoice
per (template, period)**. A double-fire of the same period — two sweep ticks
racing, a retried tick, a manual `generate-now` overlapping the loop — can never
double-create. The losing insert raises `IntegrityError`, the transaction rolls
back, and no second invoice (or duplicate spend) lands. The partial predicate
keeps the index off ordinary, non-recurring invoices.

`last_period_key` on the template is a cheap pre-check (skip the period we
already ran) but is **not** the authority — the DB unique index is. This mirrors
the PEPPOL-inbound dedupe and the discount auto-capture status guard: a
DB-enforced uniqueness invariant, not a Redis TTL that a late redelivery could
slip through.

### period_key format

The period_key is a calendar-period string, stable per cadence:

| Cadence | Format | Example |
|---------|--------|---------|
| `monthly` | `YYYY-MM` | `2026-06` |
| `quarterly` | `YYYY-Qn` | `2026-Q2` |
| `annual` | `YYYY` | `2026` |

## Generation sweep (background service)

`services/recurring_invoices.py` is a long-lived asyncio loop started in
`main.lifespan`, mirroring the `contract_renewal` / `discount_auto_trigger`
pattern (fresh per-tenant engine, one tenant's failure logged but never halting
the sweep). Each tick:

1. Enumerate tenant DBs from the control plane.
2. Per tenant, find `active` templates whose `next_run_on` has arrived
   (`next_run_on <= today`), up to `AP_RECURRING_INVOICES_MAX_PER_SWEEP` per
   tick (the per-tenant cap keeps a backlog from monopolising a tick).
3. For each, compute the `period_key`, generate the next `Invoice` (pre-coded
   from the template: vendor, amount, currency, GL / cost-center / department /
   project / PO / terms, `entity_id`), stamp `recurring_template_id` +
   `recurring_period_key`, and land it in the approval queue (status `new` →
   the normal workflow pipeline takes over).
4. Advance the template: bump `generated_count`, set `last_period_key` /
   `last_generated_at`, and roll `next_run_on` forward by one cadence step
   (nulled once it passes `end_date`).
5. Write a `recurring_template.generated` audit row (`entity_type =
   recurring_template`, `details` carries the new invoice id + period_key — no
   PII).

The generation is **idempotent on `(template, period_key)`** via the DB unique
index above, so a concurrent or retried tick is safe.

**The sweep never moves money.** It only creates an `Invoice` in the queue; the
CFO-gated payment run is what funds it, exactly as for a manually-uploaded bill.

`generate_due_invoices(today=…)` (or the per-tenant inner helper) is callable
directly for a single sweep (CLI / tests) without the loop.

Disabled by default. Env vars:

| Variable | Default | Purpose |
|----------|---------|---------|
| `AP_RECURRING_INVOICES_ENABLED` | `false` | Master switch for the generation sweep. Off in local dev/tests; flip on in deployed envs. |
| `AP_RECURRING_INVOICES_INTERVAL_SECONDS` | `3600` | Sweep interval. |
| `AP_RECURRING_INVOICES_MAX_PER_SWEEP` | `200` | Per-tick cap on invoices generated per tenant (backlog guard). |

## Variance signal

A recurring template promises a fixed amount, but the *arriving* bill from the
vendor can drift (a rate change, an extra seat, a one-off line). When an invoice
from a recurring vendor deviates from the template `amount` beyond tolerance,
it's flagged via `invoice_warnings` — **reusing the data-enrichment
price-variance approach** rather than blindly trusting the schedule. Tolerance
resolves per-template (`variance_tolerance_pct`) → falls back to the org /
platform default. The flag is advisory (a warning surfaced on the invoice), not
a hard block — the reviewer decides.

## API surface

Mounted at `/api/recurring`.

| Method + path | Purpose | RBAC |
|---------------|---------|------|
| `GET /recurring` | List (filters: `status`, `vendor_id`, `search`; paginated `page`) | read |
| `POST /recurring` | Create a template | mutate |
| `GET /recurring/{id}` | Detail | read |
| `PATCH /recurring/{id}` | Update (status excluded — the lifecycle endpoints own it) | mutate |
| `DELETE /recurring/{id}` | Delete — refused `409` once any invoice has been generated (`generated_count > 0`); pause/end instead | mutate |
| `POST /recurring/{id}/pause` | `active` → `paused` | mutate |
| `POST /recurring/{id}/resume` | `paused` → `active` | mutate |
| `POST /recurring/{id}/end` | → `ended` (terminal; nulls `next_run_on`) | mutate |
| `POST /recurring/{id}/generate-now` | Generate this period's invoice on demand (idempotent on the DB unique index — a no-op if this period already generated) | mutate |
| `GET /recurring/{id}/upcoming-schedule?count=` | Projected upcoming generations (no invoice created) — `period_key` + `run_on` + `amount`/`currency` per occurrence | read |
| `GET /recurring/{id}/history` | The invoices generated from this template (links back via `recurring_template_id`) | read |

### RBAC

- **read** = `admin`, `ap_manager`, `ap_clerk`, `cfo`
- **mutate** (create / update / delete / pause / resume / end / generate-now) =
  `admin`, `ap_manager`

Every read/write is **entity-scoped** (the `X-Entity-ID` header narrows to a
subsidiary, like the other business tables).

## Audit actions

Every mutation writes an `audit_log` row via `dispatch_audit` (append-only at
the DB layer). `entity_type` is `recurring_template`:

| Action | Emitted by |
|--------|-----------|
| `recurring_template.created` | `POST /recurring` |
| `recurring_template.updated` | `PATCH /recurring/{id}` (only when fields changed) |
| `recurring_template.paused` | `POST /{id}/pause` |
| `recurring_template.resumed` | `POST /{id}/resume` |
| `recurring_template.ended` | `POST /{id}/end` |
| `recurring_template.deleted` | `DELETE /recurring/{id}` |
| `recurring_template.generated` | a generated invoice (sweep or `generate-now`); `details` carries the invoice id + period_key |

Audit `details` never carry PII / banking data.

## Frontend route

`/recurring`, under the **Billing** nav group (`$lib/nav.ts`). Template CRUD via
a `<Modal>`, status `FilterChips` (`active` / `paused` / `ended`), a KPI row
(active templates, monthly committed, generated this period, next run), and — in
the detail modal — an **upcoming-schedule preview** (from
`GET /{id}/upcoming-schedule`) and the **generated-invoice history** (from
`GET /{id}/history`). Read roles see the page; mutate actions are gated to
admin / ap_manager (`auth.isManager`). See `frontend/CLAUDE.md`.

## Migration

- **0046_recurring_invoices** — creates `recurring_invoice_templates` and adds
  `invoices.recurring_template_id` + `invoices.recurring_period_key` plus the
  partial unique index `uq_invoice_recurring_period`. Tenant DB only (gated on
  the `invoices` table → no-ops on the control plane, fans out to every tenant
  via `scripts/migrate_all_tenants.py`). Idempotent (`CREATE TABLE/INDEX IF NOT
  EXISTS`, `ADD COLUMN IF NOT EXISTS`). Mirrors the ORM model exactly so a fresh
  tenant built by `tenant_provisioning._create_tenant_tables` (`create_all`)
  matches a migrated one.

## Local-first

No new external dependency and no new `pnpm` script. The generation sweep is
disabled by default (`AP_RECURRING_INVOICES_ENABLED=false`), so `pnpm dev` runs
the whole feature — template CRUD, upcoming-schedule preview, generated-invoice
history, and on-demand `generate-now` — with no background loop and no cloud
credential. Flip the switch on in deployed envs to enable scheduled generation.

## Seed data

`scripts/seed_extras.py` adds a couple of idempotent recurring templates per
tenant (a monthly SaaS subscription, a monthly office lease, a quarterly
service, plus a paused one) so the `/recurring` page isn't empty on a freshly
seeded tenant. Additive + skip-if-exists, like the rest of `seed_extras`.

## Tests

- `frontend/tests-e2e/recurring/recurring.spec.ts` — UI list render + KPIs +
  status filter chips; an API-driven template create surfaced in the table;
  open a template, view its upcoming-schedule preview, trigger `generate-now`,
  and confirm the generated invoice appears in the history (or the invoice
  queue). Selectors are by accessible name / aria-label / `data-testid` — no
  brittle CSS, no arbitrary sleeps.
- Backend `pytest` covers the model, the schemas, the router (CRUD + lifecycle +
  the `409`-once-generated delete guard), the period_key derivation, and the
  idempotency invariant (a double generate for one period creates exactly one
  invoice). (Owned by the backend worker.)
</content>
</invoke>
