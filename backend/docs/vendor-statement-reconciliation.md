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
| `source_format` | varchar(20) | `manual` (pasted lines, default) \| `csv` (uploaded file) \| `pdf` (read through the extraction pipeline — see PDF intake). |
| `file_key` | varchar(512) | S3 key of the uploaded statement, kept for audit replay. Stamped on both upload paths (NULL for the pasted-lines path, which has no document). |
| `status` | varchar(20) | Run review status — `open` until every actionable line is cleared, then `resolved`. Indexed. |
| `statement_total` | numeric(18,2) | The statement's claimed open balance (sum of statement-origin lines). |
| `ledger_total` | numeric(18,2) | Our matched ledger total (sum of the invoices we matched). |
| `line_count` / `matched_count` / `amount_mismatch_count` / `missing_our_side_count` / `missing_their_side_count` | integer | Denormalised outcome rollup, so the list view needs no per-line scan. |
| `notes` | varchar(500) | |
| `created_by` | uuid | The actor who created the run (plain UUID, no cross-DB FK). |
| `meta` | jsonb | Free-form bag. No PII / banking data. Carries `extraction` (provider / confidence / line_count) on a PDF run and `raw_file_stored` on both upload paths. |
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
is deliberate.

A `missing_on_our_side` row — *"the supplier billed invoice X, and we have no
invoice for it"* — by definition has no invoice on our side, and representing it
as an `Exception` would historically have meant fabricating a placeholder
invoice (the `Exception.invoice_id` column used to be NOT NULL). Migration `0049`
has since made that column nullable (so the Positive Pay feature can raise
invoice-less fraud exceptions), so the constraint is no longer the blocker — but
the recon line remains the right home regardless, for the reasons below:

- **It describes a missing invoice and feeds intake.** The recon line is a
  durable work item that *points at* an invoice we should create; once the clerk
  creates the real invoice, they resolve the line.

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
| `POST /vendor-statements/upload` | Create a run from an uploaded statement **CSV or PDF** (`multipart/form-data`: `file` + `vendor_id` + `statement_date` + optional `statement_reference` / `currency`). A PDF routes through the extraction pipeline (`source_format = pdf`), anything else through the CSV parser (`source_format = csv`); 422 on a structurally-bad CSV or an unreadable statement, 413 over the size cap |
| `GET /vendor-statements` | List runs (filters: `vendor_id`, `status`; paginated `page` / `page_size`). Omits lines |
| `GET /vendor-statements/close-readiness` | Period-close gate (see below). Declared **before** `/{recon_id}` so the literal path wins |
| `GET /vendor-statements/{recon_id}` | Detail — the run + all its lines (with each matched invoice's number, fetched in one query, no N+1) |
| `GET /vendor-statements/{recon_id}/file` | Download the archived supplier document this run was built from. Read roles; entity-scoped run lookup **and** an org-prefix check on the stored key; the same opaque 404 for an unknown run and a run with no document |
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

Three ways to get a supplier's statement into a run — all of which end at the
same pure `reconcile` engine:

1. **Manual / pasted lines** — `POST /vendor-statements` with a JSON
   `lines: [{invoice_number, invoice_date, amount, status}]` body. The UI's
   line-by-line entry / paste path; `source_format = manual`.
2. **CSV upload** — `POST /vendor-statements/upload` with the supplier's CSV
   file. `parse_statement_csv` does the forgiving header sniff;
   `source_format = csv`; a structurally-unparseable CSV returns 422 with the
   parser's message.
3. **PDF upload** — the same endpoint with a PDF; `source_format = pdf`. See
   below.

## PDF intake

Many suppliers send the statement as a PDF, and until this shipped it had to be
transcribed by hand. A PDF now routes through the **existing AI-extraction
pipeline** — the org's own configured adapter, resolved by the same
platform-vs-BYOK rules invoices use — rather than a second parser with its own
provider config and its own failure modes.

### The adapter capability

`ExtractionAdapter.extract_statement(file_bytes, file_key, mime_type)` is an
**optional** capability sitting beside `extract`, the same shape as
`PaymentAdapter.get_balance` / `fetch_settlement`: the base returns
`available=False, reason="not_supported"`, so an adapter that can't read a
statement says so instead of returning an empty success.

It is a separate method rather than a reuse of `extract` because the documents
are genuinely different shapes. `ExtractionResult` models ONE invoice — a header
plus `ExtractedLineItem`s that carry description / quantity / unit price. A
statement is MANY open items for one supplier, and the two fields reconciliation
matches on — the per-row **invoice number** and **date** — have nowhere to live
on an `ExtractedLineItem`. Forcing the statement through that shape would have
meant smuggling the invoice number into `item_code`, and losing the date.

| Adapter | `extract_statement` |
|---|---|
| `mock` | Deterministic offline reader — no network, no credential. Pulls the document's text layer (PyMuPDF) and scans `number [date] amount` rows (`statement_extraction.scan_statement_text`). One money column per row, or it skips the row — see below. |
| `claude_vision` | Sends the shared statement prompt down the same document channel `extract` uses. |
| `ollama` | Same prompt against a **local** model — text mode when the PDF has a text layer, page images when it doesn't. The no-cloud path for a scanned statement. |
| `openai_vision`, `aws_textract`, `einvoice` | Inherit the honest `not_supported` default. |

The prompt and the JSON→dataclass parser are shared
(`extraction_adapters/statement_extraction.py`), so the hosted and the local
reader can't drift.

#### The offline reader skips rather than guesses

`scan_statement_text` reads a row as `identifier [date] amount`, and takes the
amount only when **all three** hold:

1. the identifier is **not itself money** (see § …and it says how many it
   skipped — a row whose reference is a figure is a summary line, not an item);
2. the row has **exactly one** money column after the identifier — money being a
   token with cents, a thousands separator, or a currency symbol (a lone
   amount-shaped integer counts, for a statement that prints no cents);
3. **nothing amount-shaped sits to the right of it.**

Rule 2 alone isn't enough, and the reason is worth stating because the two
layouts look identical in shape:

```
INV-1  2026-01-15  Net 30    1,200.00     -> read 1,200.00
INV-1  2026-01-15  1200.00   800          -> skipped
```

`30` and `800` are both bare integers. What separates them is **position**: a
payment-terms or aging-days column prints *before* the balance, a second money
column (`invoice-amount` + `balance-due`, or `balance` + aging bucket) prints
*after* it — and only the second makes which figure is open ambiguous. Counting
candidates within the money bucket alone would call that second row unambiguous
and return `1200.00`, the invoice amount rather than the open balance.

Picking a column anyway would produce a plausible figure that may be the wrong
one — a wrong open balance presented as fact. Skipping is loud instead: our
invoice for that row lands as `missing_on_their_side`, a difference the clerk
sees and chases. A multi-column or aging-bucket statement is the case this
reader can't resolve honestly; the answer there is a vision provider.

#### …and it says how many it skipped

`scan_statement_text` returns a `StatementScan` — `lines` plus
`ambiguous_skips` — because "how many rows did you skip?" has no honest single
answer. Every physical line goes through the same loop: blank lines, the vendor
block, the column header, `Page 1 of 2`, the statement total. A count of
everything declined would report a dozen skips on a clean two-page statement,
and a number that is noise on the good case trains a reviewer to ignore it on
the bad one.

So the skip is **classified where it happens**, and only one class is reported:

| Class | Reported? | What it is |
|---|---|---|
| Not a row | no | No identifier-shaped token, or nothing money-shaped after one. Column headers, page furniture, `Total …`, `Balance forward …`. |
| Ambiguous | **yes** | The line *did* look like an open item and the reader refused to pick between two readings — two money columns, or a second reference-shaped column left of the amount. |

One rule makes the split hold on a real aging statement, and closed a bug on
the way: **a row whose chosen reference is itself unambiguous money is not an
open item at all** — it is a summary or total line, skipped silently and not
counted.

```
Total                              1,800.50
Total  1,200.00  850.50  410.00    2,460.50   <- aging footer
Current: 1,200.00   Past due:        850.00   <- summary block
```

The first two only ever reached the skip path by accident — one has nothing
after the money token it took as its reference, the other has too many. The
third has exactly one figure after it and was therefore **accepted**, booking a
fabricated open item keyed on `1,200.00` for `850.00` that no ledger row can
match. That is the invented money the whole reader exists to avoid, and it is
worse than a skip. Testing the reference directly closes all three.

**The cost, named.** `_is_money` needs cents, a thousands separator, or a
currency symbol, so the rule only reaches a **bare, prefix-less, purely numeric**
reference that happens to carry one. In practice that is exactly two shapes:
`2026.01` (a year.sequence reference) and `1,234`. Every other real format
survives — `INV-1001`, `100234`, `1200`, `INV/2026/001`, `2026-001`, `2026.001`
(three decimals, so not money-shaped), `FR-2026-01`, `0012345678`, `#4502`,
`SI-2026.01`, and the European `1.234,56`. A supplier using one of the two
affected shapes loses those rows from a machine-read run, and they surface as
`missing_on_their_side` — visible, chased, recoverable.

That trade is deliberate and follows the reader's own doctrine: a token like
`2026.01` is *genuinely ambiguous* between a reference and a figure, and every
ambiguity here resolves to skipping. The alternative costs more — an accepted
summary line is invented money a clerk chases the supplier for, and nothing
downstream ever flags it. `test_a_money_reference_is_never_an_invoice_number`
and `test_an_all_digit_invoice_number_is_still_a_valid_reference` pin both
directions so neither drifts.

The result: a clean `number date amount` statement reports **0**; a
four-column aging statement reports **one per data row**. The count rides
`StatementExtractionResult.skipped_ambiguous` → the run's
`meta.extraction.skipped_ambiguous` → the detail modal's provenance panel,
which says how many rows were skipped, that the diff below is short by exactly
that many supplier rows, and points at the CSV / vision-provider alternative in
context. A count only, never the skipped rows' text — the figure is what a
reviewer acts on, and the text is supplier data.

A **model-backed** adapter leaves the field at `0`: it is not asked to report
its own skips, so `0` there means "not measured", which is why the panel shows
the standing skip-rule note instead of a "0 rows skipped" line.

### Money crosses the boundary as a string

An adapter returns `StatementLineExtraction` with **raw strings** —
`amount="(250.00)"`, `invoice_date="01/20/2026"`. `vendor_statement_extraction`
normalises them with the engine's own `parse_amount` / `parse_date` (public for
exactly this reason), so a model's output and a CSV cell become `Decimal` /
`date` by identical rules and neither ever passes through a float.

### Fail closed

Every failure path raises `StatementExtractionError` → **422**, and no run is
created:

| Reason | When |
|---|---|
| `not_supported` | The org's provider hasn't implemented the capability |
| `empty_file` | Zero-byte upload |
| `no_text_layer` | A scan the configured provider couldn't read |
| `no_lines_found` | Readable, but no open items on it |
| `provider_error` | Transport / non-200 / an adapter that raised |
| `unreadable_response` | The provider returned something that isn't the agreed shape |

The reason codes are PII-free by construction and map to static user-facing
messages. An adapter's own `error` text — which can echo a provider response
body, key material included — is **logged and never surfaced**.

Two refusals are worth calling out because the tempting alternative is worse:

- The `mock` adapter does **not** fall back to a fixture when a PDF has no text
  layer, unlike its `extract` twin. A fabricated open item on this feature is
  money a clerk then chases a supplier for.
- An adapter that reports success but yields no usable row is a refusal, not an
  empty run. A run with zero statement lines asserts the supplier listed
  nothing — which reads as "we owe them nothing".

### Provenance

A PDF run records `meta.extraction` (`method` / `provider` / `confidence` /
`line_count`), surfaced on the response as `extraction`, and each line's `raw`
JSONB carries `source: "extraction"` plus that line's own confidence. A reviewer
clearing these lines is clearing a machine's reading of a document, and the
response says so; a CSV / pasted-lines run returns `extraction: null`.

`extraction.line_count` is the number of open items the reader **accepted** off
the document — deliberately not the run's `summary.line_count`, which also counts
the `missing_on_their_side` rows built from our own ledger. The reader reports no
*skipped*-row figure; why that is a design question rather than an oversight is
in [followups.md](../../docs/followups.md) § The statement reader skips rows
without saying how many.

## The UI (`/vendor-statements`)

`VendorStatementReconModal` is both the create form and the run detail.

**Intake is an explicit choice**, not inferred from which field the user touched:
a radio pair swaps between the pasted-lines editor and a CSV-or-PDF file picker.
Before, both were on screen at once and a file silently won the tiebreak, so
typed lines could vanish; `notes` (which `POST /upload` does not accept) sat
above both and was dropped on the upload path. Notes now lives in the paste
panel, and submit stays disabled until the chosen intake can actually produce a
statement line — an empty editor used to create a run asserting the supplier
listed nothing, the same claim the PDF path refuses to invent.

**A refusal is rendered, not toasted.** The 422 reason messages above are the
actionable half of a fail-closed refusal ("upload a CSV, or configure a vision
provider"), so they land in a persistent `role="alert"` region on the form
(`[data-testid="statement-intake-error"]`) with the dialog still open. Oversized
files are caught client-side against the same 25 MB `storage.MAX_FILE_SIZE` cap
the route enforces, so the refusal reads as a size problem rather than a failed
upload.

**The run detail carries its provenance**: a source pill (typed / CSV / PDF), and
for a machine-read run a panel naming the adapter, its confidence and the number
of open items taken off the document, plus what the skip-rather-than-guess rule
means for the diff below it — a skipped row is precisely what becomes a
`missing_on_their_side` difference. When the document was archived, a Download
control fetches it through the authenticated client (a bare `<a href>` can't
carry the Bearer + tenant headers).

Confidence is rendered by the pure `formatExtractionConfidence`
(`frontend/src/lib/types/vendorStatementRecon.ts`), which clamps and guards
non-finite input: the figure crosses a network boundary from a provider, and a
reviewer weighing a machine's reading must never be handed `NaN%` or `140%`.
Covered by `vendorStatementRecon.test.ts` (vitest) and
`frontend/tests-e2e/vendor-statements/recon.spec.ts`.

## Raw-file storage

Both upload paths archive the uploaded document to S3/MinIO
(`storage.upload_vendor_statement_file` → `<org_id>/vendor-statements/<run_id>/
<safe-filename>`) and stamp `file_key`; `GET /vendor-statements/{id}/file`
serves it back. The leading `org_id` segment is the cross-tenant gate, the same
scheme as the invoice / contract / positive-pay files.

The per-line `raw` JSONB preserves what we **parsed**, which is enough to replay
the match — but not to answer *"did we read the supplier's document
correctly?"*, which is the question a disputed balance actually raises, and it
matters most on the PDF path where a model did the reading.

Archiving is **best-effort**: a storage hiccup logs PII-free and records
`meta.raw_file_stored = false` rather than failing the request, because it must
not cost a clerk a reconciliation they just ran. Only a document that produced a
run is archived, so a rejected upload never reaches the bucket; deleting a run
drops the object.

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
feature (create from pasted lines, CSV **or PDF**, review the line queue,
resolve lines, check close-readiness) with no cloud credential and nothing to
enable.

PDF intake stays local-first through the `mock` adapter's deterministic
text-layer reader: point the org at `settings.extraction = {program_type:
"byok", provider: "mock"}` and a real supplier PDF (any ERP-generated statement
carries a text layer) reconciles on the laptop with no key anywhere. A *scanned*
statement genuinely needs a model — `pnpm ollama:up` plus `provider: "ollama"`
is the no-cloud answer, same as it is for invoice extraction. The default
platform provider is `claude_vision`, which needs a key like every other
extraction path; without one the upload is refused with an actionable message
rather than mis-read.

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

- **No upload UI.** `/vendor-statements` is a create-from-pasted-lines page; the
  CSV upload endpoint never had a UI either, and the PDF one inherits that gap.
  The whole upload surface (file picker → vendor / statement-date form → the run
  detail, plus a "download the source statement" link) is one page-level change
  and is tracked in `docs/followups.md`.
- **Multi-money-column layouts on the offline reader.** `scan_statement_text`
  reads a row only when it has exactly one money column after the identifier,
  so an `invoice-amount + balance-due` statement or one with current/30/60/90
  aging buckets yields nothing from it (see § The offline reader skips rather
  than guesses). That's deliberate — the alternative is a wrong open balance —
  but it does mean the credential-free local path covers the simple layout
  only. The answer for the rest is a vision provider (`ollama` locally,
  `claude_vision` deployed), which reads the table properly.
- **Auto-create invoice on resolve.** When a clerk resolves a
  `missing_on_our_side` line, optionally kick off invoice intake pre-filled from
  the statement line (number / amount / date), closing the loop from "supplier
  says we owe this" to "invoice in the queue" in one step. The recon line is
  already the durable work item that feeds intake; this is the convenience leg.
</content>
</invoke>
