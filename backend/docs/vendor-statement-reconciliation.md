# Vendor Statement Reconciliation

A supplier periodically sends a **statement of open items** — every invoice it
believes we still owe, as of a `statement_date`. Reconciling that statement
against our own AP ledger is a core month-end-close task that's entirely manual
today: the clerk eyeballs the supplier's list against our open invoices and
chases the differences. This feature makes that tie-out a structured,
auditable, repeatable run: paste (or upload) the supplier's lines, and a pure
engine matches them to our open invoices and classifies every difference into a
durable review queue.

This is the feature behind roadmap **Vendor Statement Reconciliation**.

## Not bank reconciliation

The two sound similar and even share CSV-sniffing idioms, but they reconcile
opposite ends of the AP lifecycle:

| | Vendor statement reconciliation | Bank reconciliation (`docs/bank-reconciliation.md`) |
|---|---|---|
| What we tie out | The supplier's **open balances** ↔ our **AP ledger** (open invoices) | **Cleared payments** ↔ **bank lines** |
| When | **Before** money moves — pre-close, "do we agree on what's owed?" | **After** money moves — "did the cash that left match the bank?" |
| Our side of the match | `Invoice` rows NOT yet settled (status `NOT IN (paid, done)`) | `Payment` rows that should appear on the statement |
| Their side | Supplier statement of open items | Bank's transaction export |
| Output | Per-line classification → a recon **line** review queue | Per-transaction match method + confidence → exceptions |
| Models | `vendor_statement_reconciliations` + `_lines` | `bank_statements` + `bank_transactions` |

Vendor-statement reconciliation answers *"do the supplier and we agree on the
open balance?"*; bank reconciliation answers *"did the cash that already moved
land where the bank says?"*. This doc is the former.

## Data model

Two tenant-scoped tables in `app/models/vendor_statement_recon.py`
(`EntityMixin` for multi-entity; the run also has `TimestampMixin`), migration
`0047_vendor_statement_recon`. Money is exact: every amount column is
`Numeric(18, 2)` — never float.

### `VendorStatementReconciliation` (the run)

One reconciliation run for one vendor, as of one `statement_date`.

| Field | Type | Notes |
|-------|------|-------|
| `id` | uuid PK | |
| `organization_id` | uuid | Indexed. |
| `vendor_id` | uuid FK → vendors (nullable) | Nullable so the row survives a vendor delete, but a run is always created against a real vendor. Indexed. |
| `vendor_name` | varchar(255) | Denormalised for display (mirrors `Invoice`). |
| `statement_date` | date | "As of" date the statement covers. Required. |
| `statement_reference` | varchar(120) | The supplier's own statement number / reference, when present. |
| `currency` | varchar(3) | Default `USD`. |
| `source_format` | varchar(20) | `manual` (pasted lines, default) \| `csv` (uploaded file) \| `pdf` (reserved — see Deferred). |
| `file_key` | varchar(512) | S3 key of the uploaded statement, kept for audit replay. **Always NULL today** — raw-file storage is deferred (see Deferred). |
| `status` | varchar(20) | Run review status — `open` until every actionable line is cleared, then `resolved`. Indexed. |
| `statement_total` | numeric(18,2) | The statement's claimed open balance (sum of statement-origin lines). |
| `ledger_total` | numeric(18,2) | Our matched ledger total (sum of the invoices we matched). |
| `line_count` / `matched_count` / `amount_mismatch_count` / `missing_our_side_count` / `missing_their_side_count` | integer | Denormalised outcome rollup, so the list view needs no per-line scan. |
| `notes` | varchar(500) | |
| `created_by` | uuid | The actor who created the run (plain UUID, no cross-DB FK). |
| `meta` | jsonb | Free-form bag. No PII / banking data. |
| `entity_id` | uuid FK → entities | From `EntityMixin`; lines inherit it. |
| `created_at` / `updated_at` | timestamptz | From `TimestampMixin`. |

### `VendorStatementReconLine` (the per-line result)

One row per reconciliation outcome the engine produced.

| Field | Type | Notes |
|-------|------|-------|
| `id` | uuid PK | |
| `reconciliation_id` | uuid FK → run (CASCADE) | Lines are deleted with the run. Indexed. |
| `organization_id` | uuid | |
| `statement_invoice_number` / `statement_date` / `statement_amount` / `statement_status` | the supplier's view | All NULL for a `missing_on_their_side` row (no statement line — it's one of *our* invoices the statement omitted). |
| `classification` | varchar(30) | One of the four outcomes below. Indexed. |
| `matched_invoice_id` | uuid FK → invoices | Our matched invoice (or the orphan invoice, for `missing_on_their_side`). NULL for `missing_on_our_side`. |
| `ledger_amount` | numeric(18,2) | Our invoice's amount, for the matched legs. |
| `amount_difference` | numeric(18,2) | Signed `statement_amount − ledger_amount`, for the `amount_mismatch` view. |
| `match_method` | varchar(40) | `invoice_number` \| `amount_date` — how the engine matched this line. |
| `resolution_status` | varchar(20) | `unresolved` (default) \| `resolved` \| `ignored`. |
| `resolution_note` / `resolved_by` / `resolved_at` | the clerk's disposition | |
| `raw` | jsonb | The original parsed statement line, for audit replay. |
| `entity_id` | uuid FK → entities | From `EntityMixin`. |
| `created_at` | timestamptz | |

The classification / status / source string constants live on the model
(`CLASS_*`, `STATUS_*`, `RESOLUTION_*`, `SOURCE_*`).

## The pure engine

All reconciliation math lives in `app/services/vendor_statement_recon.py` and is
**pure**: no DB session, no I/O, no network, no `async`. It operates on
dataclasses (`StatementLine`, `LedgerInvoice` → `ReconLineResult` +
`ReconSummary`) and every money value is `Decimal`. There is **no background
sweep** — reconciliation is entirely user-triggered. The CSV-parsing helpers
(`_find_col`, `_parse_date`, `_parse_amount`) mirror the forgiving idioms in
`bank_reconciliation` but are reimplemented so the engine stays self-contained.

### CSV parsing (`parse_statement_csv`)

`parse_statement_csv(raw_csv: bytes) -> list[StatementLine]`. Forgiving:

- Decodes `utf-8-sig` (BOM-tolerant) with a `latin-1` fallback.
- Sniffs the header row and accepts common column synonyms (case +
  whitespace insensitive):

  | Field | Accepted column names |
  |---|---|
  | Invoice number | `invoice`, `invoice number`, `invoice_number`, `invoice no`, `invoice #`, `number`, `ref`, `reference`, `document`, `document number` |
  | Date | `date`, `invoice date`, `invoice_date`, `document date`, `due date` |
  | Amount | `amount`, `balance`, `open balance`, `outstanding`, `amount due`, `total` |
  | Status | `status`, `state` |

- Amount parser accepts `1234.56`, `1,234.56`, `$1,234.56`, `-1234.56`, and
  `(1,234.56)` (Quickbooks-style parenthesized negative → signed Decimal).
- Date parser tries ISO (`%Y-%m-%d`), then `%m/%d/%Y`, then `%d/%m/%Y`, then
  `%Y/%m/%d`; an unrecognised value is `None` (never raises).

Raises `StatementParseError` (→ a 422 at the route) only on a **structural**
failure: an empty body / fewer than two rows (header + ≥1 data row), or a header
carrying **neither** an invoice-number column **nor** an amount column. A single
data row with no number *and* no usable amount is noise → skipped silently; a
blank line is skipped.

### Matching algorithm (`reconcile`)

`reconcile(statement_lines, ledger_invoices, *, amount_tolerance=Decimal("0.01"),
date_window_days=5)` is deterministic and stable-ordered. Statement lines are
processed in input order; **each ledger invoice is consumed by at most one
statement line**. Invoice numbers are normalised for matching via
`normalize_invoice_number` — strip, upper-case, drop every non-alphanumeric
char, so `"INV-001"`, `"inv 001"` and `"#INV001"` all collapse to `"INV001"`.

For each statement line:

1. **Leg 1 — invoice number first.** Exact normalised invoice-number match
   against an unconsumed ledger invoice (`match_method = invoice_number`).
   First-wins on a number collision.
2. **Leg 2 — amount + date fallback.** If Leg 1 found nothing and the line has
   an amount, the first unconsumed ledger invoice whose amount is **exactly
   equal** and whose date is within `±date_window_days` (a missing date on
   either side passes — we don't have the signal to reject on)
   (`match_method = amount_date`).
3. **No match → `missing_on_our_side`.** The supplier billed it and we have no
   invoice.
4. **Match found → `matched` or `amount_mismatch`.** Compute the signed
   `amount_difference = statement_amount − ledger_amount`; if
   `abs(difference) <= amount_tolerance` it's `matched`, otherwise
   `amount_mismatch`.

After every statement line, each **unconsumed** ledger invoice yields one
`missing_on_their_side` result (we have an open invoice the statement omitted).

`_build_summary` rolls the results into the denormalised counts + totals:
`statement_total` sums every statement-origin line; `ledger_total` sums the
ledger amount of every matched / amount-mismatch invoice.

### The four classifications

| Classification | Meaning | Actionable? |
|---|---|---|
| `matched` | Statement line ↔ our invoice, amounts agree within tolerance | No |
| `amount_mismatch` | Same invoice, amounts differ beyond tolerance | **Yes** |
| `missing_on_our_side` | Supplier billed it, we have no invoice | **Yes** |
| `missing_on_their_side` | We have an open invoice the statement omitted | No |

The two **actionable** classes (`missing_on_our_side` + `amount_mismatch`) are
what a clerk must clear before the run flips to `resolved`, and they're the only
ones that contribute to close-readiness materiality. `missing_on_their_side` is
informational — the supplier simply hasn't listed an invoice we're tracking (a
timing difference, usually); it carries no unreconciled money toward the
threshold.

### Materiality of one line (`line_unreconciled_amount`)

`line_unreconciled_amount(classification, statement_amount, amount_difference)`
is the pure primitive for "how much unresolved money does this line represent":

- `missing_on_our_side` → `abs(statement_amount)` (we owe it, untracked)
- `amount_mismatch` → `abs(amount_difference)` (the gap to resolve)
- everything else → `0`

It never raises on `None` inputs. The close-readiness endpoint sums it over a
run's still-`unresolved` lines.

## Design decision: recon lines, not Exceptions

The actionable differences are surfaced as **reconciliation lines** (the
`vendor_statement_recon_lines` review queue), **not** as `Exception` rows. This
is deliberate, and the most important one is structural:

**`Exception.invoice_id` is NOT NULL.** A `missing_on_our_side` row — *"the
supplier billed invoice X, and we have no invoice for it"* — by definition has
no invoice on our side. It literally cannot be represented as an `Exception`
without fabricating a placeholder invoice first, which would pollute the AP
ledger with a non-invoice and corrupt every downstream aggregate (aging, spend,
the payment queue). The recon line is the right home: it's a durable work item
that *describes a missing invoice* and feeds invoice intake — once the clerk
creates the real invoice, they resolve the line.

Secondary reasons:

- **The run is the unit of work, not the invoice.** Exceptions hang off a single
  invoice; a statement reconciliation is a vendor-and-period-scoped batch whose
  lines must be reviewed and rolled up together (counts, totals,
  close-readiness). A run with its lines models that; scattered per-invoice
  exceptions don't.
- **Different lifecycle.** A recon line resolves to `resolved` / `ignored` with
  a note, and the run auto-flips to `resolved` once no actionable line is open
  — its own small state machine, distinct from the exception queue's.

So the recon line *is* the durable artifact; it doesn't shadow an `Exception`.

## API surface

Mounted at `/api/vendor-statements` (`app/api/vendor_statement_recon.py`). Money
is `Decimal` end-to-end; every mutation is RBAC-gated, writes an audit row, and
is entity-scoped.

| Method + path | Purpose |
|---|---|
| `POST /vendor-statements` | Create a run from a pasted/normalised list of lines (`source_format = manual`) |
| `POST /vendor-statements/upload` | Create a run from an uploaded statement CSV (`multipart/form-data`: `file` + `vendor_id` + `statement_date` + optional `statement_reference` / `currency`); `source_format = csv`; 422 on a structurally-bad CSV |
| `GET /vendor-statements` | List runs (filters: `vendor_id`, `status`; paginated `page` / `page_size`). Omits lines |
| `GET /vendor-statements/close-readiness` | Period-close gate (see below). Declared **before** `/{recon_id}` so the literal path wins |
| `GET /vendor-statements/{recon_id}` | Detail — the run + all its lines (with each matched invoice's number, fetched in one query, no N+1) |
| `POST /vendor-statements/{recon_id}/lines/{line_id}/resolve` | Resolve / ignore / re-open one line (`resolution_status` ∈ `resolved` / `ignored` / `unresolved`, optional note); recomputes the run status |
| `DELETE /vendor-statements/{recon_id}` | Delete the run (cascade removes its lines) |

The candidate ledger for a run is **that vendor's** invoices in the entity scope
whose status is `NOT IN (paid, done)` — a settled invoice can't be on a
supplier's open-items statement. Both intake paths share `_create_run`, which
resolves the vendor (404 if out of entity scope), builds the candidate ledger,
runs `reconcile`, and persists the run + lines (mirrored by the seed helper).

A run flips to `resolved` (`_recompute_run_status`) once no actionable line
(`missing_on_our_side` / `amount_mismatch`) is still `unresolved`; resolving the
last open actionable line on the resolve endpoint closes the run.

### RBAC

- **read** (list / detail / close-readiness) = `admin`, `ap_manager`, `ap_clerk`, `cfo`
- **write** (create / upload / resolve-line / delete) = `admin`, `ap_manager`

Every read/write is **entity-scoped** (the `X-Entity-ID` header narrows to a
subsidiary, via `apply_entity_scope` / `get_entity_id` / `get_write_entity_id`,
like the other business tables).

### Audit actions

Every mutation writes an `audit_log` row via `dispatch_audit` (append-only at
the DB layer). `entity_type` is `vendor_statement_reconciliation`:

| Action | Emitted by |
|--------|-----------|
| `vendor_statement_recon.created` | `POST /vendor-statements` and `POST /vendor-statements/upload`; `details` carries the vendor id + line count |
| `vendor_statement_recon.line_resolved` | resolve-line; `details` carries the line id + new resolution status + resulting run status |
| `vendor_statement_recon.deleted` | `DELETE /vendor-statements/{id}`; `details` carries the vendor id |

Audit `details` never carry PII / banking data.

## Close-readiness (period-close tie-in)

`GET /vendor-statements/close-readiness` answers *"can we close the period, or
does a vendor still have a material unreconciled balance?"* For every **open**
run in scope, newest-first, keeping only the **most recent run per vendor** (a
prior run is superseded by the vendor's latest statement), it sums
`line_unreconciled_amount` over the run's still-`unresolved` lines. A vendor
whose unreconciled total **exceeds** the materiality threshold is a
`blocking_vendor`; the period is `is_close_ready` only when no vendor blocks.

The threshold comes from `settings.statement_recon_materiality_default` (config
key `statement_recon_materiality_default`, default `1000.0`), overridable
per-request via `?materiality=` (≥ 0). It's parsed through `str()` into a
`Decimal` so the comparison stays exact.

## Intake paths

Two ways to get a supplier's statement into a run:

1. **Manual / pasted lines** — `POST /vendor-statements` with a JSON
   `lines: [{invoice_number, invoice_date, amount, status}]` body. The UI's
   line-by-line entry / paste path; `source_format = manual`.
2. **CSV upload** — `POST /vendor-statements/upload` with the supplier's CSV
   file. `parse_statement_csv` does the forgiving header sniff;
   `source_format = csv`; a structurally-unparseable CSV returns 422 with the
   parser's message.

**Honest scope note:** the uploaded raw file is **not** stored today —
`file_key` is always written `NULL` (raw-file storage to S3 is deferred). The
`pdf` source format and a PDF-via-extraction intake path are reserved on the
model but not implemented. The `raw` JSONB on each line preserves the parsed
statement line for audit replay regardless.

## Migration

- **0047_vendor_statement_recon** — creates `vendor_statement_reconciliations`
  and `vendor_statement_recon_lines` plus their indexes. Tenant DB only (gated
  on the `invoices` table → no-ops on the control plane, fans out to every
  tenant via `scripts/migrate_all_tenants.py`). Idempotent (`CREATE TABLE/INDEX
  IF NOT EXISTS`). Mirrors `app.models.vendor_statement_recon` exactly so a
  fresh tenant built by `tenant_provisioning._create_tenant_tables`
  (`create_all`) matches a migrated one. The FKs (vendors, entities, invoices)
  all exist by earlier migrations.

## Local-first

No new external dependency and no new `pnpm` script. There is no background
sweep — reconciliation is entirely user-triggered — so `pnpm dev` runs the whole
feature (create from pasted lines or CSV, review the line queue, resolve lines,
check close-readiness) with no cloud credential and nothing to enable.

## Seed data

`scripts/seed_extras.py::seed_vendor_statement_recon` adds one reconciliation
run per tenant so the page isn't empty on a freshly seeded tenant: it picks an
existing vendor with a couple of invoices, hand-builds a small statement (lines
that match the vendor's invoices plus one phantom line that doesn't), and runs
the real `reconcile` engine — persisting the run + lines exactly like the API's
`_create_run` — so the seeded data produces a genuine `missing_on_our_side`
line. Additive + skip-if-exists (it bails if any run already exists for the
org), like the rest of `seed_extras`.

## Deferred / future work

- **PDF statement extraction.** Many suppliers send statements as a PDF, not a
  CSV. Reuse the existing AI-extraction pipeline (`services/extraction`) to turn
  a PDF statement into `StatementLine`s, then feed the same `reconcile` engine.
  The `pdf` source format is already reserved on the model.
- **Raw-file storage.** Store the uploaded statement (CSV/PDF) to S3 and stamp
  `file_key`, so a run can be audited against the original document. The column
  exists; only the upload-to-S3 wiring is deferred.
- **Auto-create invoice on resolve.** When a clerk resolves a
  `missing_on_our_side` line, optionally kick off invoice intake pre-filled from
  the statement line (number / amount / date), closing the loop from "supplier
  says we owe this" to "invoice in the queue" in one step. The recon line is
  already the durable work item that feeds intake; this is the convenience leg.
</content>
</invoke>
