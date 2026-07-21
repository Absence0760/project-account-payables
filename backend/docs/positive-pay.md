# Positive Pay / Payment Fraud File

Positive Pay is a bank-side **fraud control**. We hand our bank a file of the
cheques we actually *issued* (a `check_issue` file, generated per payment run) —
or, for ACH debit-block, the list of accounts authorized to debit us (a
standalone `ach_authorization` file). When an item is later presented for
payment, the bank matches it against our issued file and **refuses to clear**
anything that doesn't line up — an altered amount, a cheque we never wrote, or
an unauthorized ACH originator. Without it, a forged or altered cheque clears
silently; with it, the bank stops it and asks us first.

This is the feature behind roadmap **Positive Pay / Payment Fraud File**. It's a
natural extension of the existing `checkeeper` check-printing + payment-rail
adapters, and a frequent enterprise-AP procurement requirement.

## PII invariant (read this first)

This feature is squarely in PII scope. The rule:

- **The rendered file legitimately contains full account / routing numbers** —
  that *is* its purpose (the bank needs them to match). It lives **only** in
  MinIO under `file_key`, behind the read-role gate, and is never logged.
- **Everything else carries no full account number.** The DB row stores
  `account_last4` only; audit `details`, log lines, and HTTP error bodies carry
  masked or no account data. The pure return classifier
  (`classify_presented_items`) never sees an account number at all — only check
  numbers + amounts.

A `logger.info(...)` or audit `details` containing a full account / routing
number is a `Critical` PII-invariant violation.

## Data model

One tenant-scoped table in `app/models/positive_pay.py`
(`PositivePayFile(Base, EntityMixin, TimestampMixin)`), migration
`0048_positive_pay` (+ `0070_positive_pay_currency` adds `currency`). Money is
exact: `total_amount` is `Numeric(18, 2)` — never float.

### `PositivePayFile` — `positive_pay_files`

| Field | Type | Notes |
|-------|------|-------|
| `id` | uuid PK | |
| `organization_id` | uuid | Indexed. |
| `payment_run_id` | uuid FK → payment_runs (nullable) | Set for `check_issue` (the run the cheques came from); NULL for `ach_authorization` (org-wide). Indexed. |
| `file_type` | varchar(20) | `check_issue` \| `ach_authorization`. Indexed. |
| `bank_format` | varchar(30) | Formatter key, e.g. `csv` \| `fixed_width`. |
| `status` | varchar(20) | `generated` → `returned_processed` (once the bank's return is processed). Indexed. |
| `item_count` | integer | Number of cheques / authorized accounts in the file. |
| `total_amount` | numeric(18,2) | Sum of cheque amounts (always 0 for an ACH-authorization file). |
| `currency` | varchar(3) (nullable) | Currency `total_amount` is denominated in — the org's reporting (home) currency stamped at generation via `resolve_reporting_currency` (migration `0070`). `Payment.amount` is already home-currency, so this is a **stored label, not an FX conversion**. NULL for legacy rows created before the column; the UI falls back to the org default for those. |
| `content_hash` | varchar(64) | sha256 hex of the rendered file content — tamper-evidence / dedupe aid. |
| `file_key` | varchar(512) | MinIO key of the rendered file (the file holding the full account numbers). |
| `account_last4` | varchar(4) | Masked originating / cheque account — **never** the full number. |
| `generated_by` | uuid | The actor who generated the file (plain UUID, no cross-DB FK). |
| `meta` | jsonb (MutableDict) | Free-form bag — holds the `issued_map` (`{normalized_check_number: {"invoice_id": ..., "amount": "..."}}`, the POINT-IN-TIME snapshot of what was actually sent to the bank at generation — return processing classifies against this, never a live re-query of the run's current payment statuses, so an issued-then-voided cheque still classifies correctly instead of `not_on_file`) and, after a return, `return_summary` + `return_history`. **No PII.** |
| `entity_id` | uuid FK → entities | From `EntityMixin` (multi-entity). |
| `created_at` / `updated_at` | timestamptz | From `TimestampMixin`. |

The file-type / status string constants live on the model (`FILE_TYPE_*`,
`STATUS_*`).

### Idempotency

The partial unique index
`uq_positive_pay_run_format ON (payment_run_id, bank_format) WHERE
payment_run_id IS NOT NULL` enforces **one check-issue file per (run, format)**.
Re-generating returns the existing row (HTTP 200) rather than emitting a second
file; a concurrent double-fire loses the race on `flush()` → the loser rolls
back and returns the winner. ACH-authorization files are run-less
(`payment_run_id IS NULL`), so the partial predicate exempts them — you can
generate as many as you like.

## Formatter adapters (`services/positive_pay_adapters/`)

The rendering layer mirrors `payment_adapters/`: a pluggable, per-bank
formatter, selected by the `bank_format` key, with a default and a safe
fallback.

```python
@register_positive_pay_formatter("my_bank")
class MyBankFormatter(PositivePayFormatter):
    format_name = "my_bank"
    file_extension = "csv"
    content_type = "text/csv"

    def format_check_issue(self, items: list[CheckIssueItem], ctx: FormatterContext) -> str: ...
    def format_ach_authorization(self, items: list[AchAuthorizationItem], ctx: FormatterContext) -> str: ...
```

The dataclasses (`base.py`):

- `CheckIssueItem(check_number, payee, amount, issue_date, account_number)`
- `AchAuthorizationItem(vendor_name, routing_number, account_number, status)`
- `FormatterContext(company_name, account_number, file_date, currency)`

`get_positive_pay_formatter(name_or_config)` (`dispatcher.py`) resolves the
formatter; the default is `csv`, and an **unknown name falls back to `csv`**
(never raises — a bad config can't break generation).

Registered formatters:

| Key | Class | Shape |
|---|---|---|
| `csv` (default) | `CsvFormatter` | RFC-4180 CSV. check_issue header `check_number,payee,amount,issue_date,account_number`; ach header `vendor_name,routing_number,account_number,status`. Amounts are plain `str(Decimal)`; dates ISO. |
| `fixed_width` | `FixedWidthFormatter` | Headerless, column-aligned, deterministic. Fixed widths documented as a contract in the module docstring (check_issue 80-char record: check#=10, payee=40, amount=14 zero-padded cents, issue_date=8 `YYYYMMDD`, account=8; ach 80-char record: vendor=40, routing=9, account=17, status=14). |

### Adding a per-bank format

1. Copy `csv_formatter.py`, implement `format_check_issue` /
   `format_ach_authorization` for your bank's exact layout.
2. Decorate the class with `@register_positive_pay_formatter("your_bank")`.
3. Import it in `positive_pay_adapters/__init__.py` so the decorator runs at
   import time.
4. The frontend exposes formats via the `BANK_FORMATS` constant in
   `frontend/src/lib/types/positivePay.ts` — add your key + label there to
   surface it in the generate modal.

Amounts are always derived from the `Decimal` on the item (zero-padded cents in
fixed-width, plain decimal string in CSV) — never a float.

Positive Pay files are **deliberately excluded** from the platform-wide CSV
formula-injection guard (`report_export.csv_safe_cell`, CWE-1236): they are
fixed-format uploads consumed by the bank's machine matching, never opened as a
spreadsheet workflow, and a `'` prefix on the payee/vendor name would break the
bank's exact-match comparison and raise a false fraud flag on every cheque. All
human-facing CSV exports (analytics, audit, report builder, invoice/workflow
exports) do apply the guard — see `backend/CLAUDE.md` § Security utilities.

## The pure return classifier

`classify_presented_items` (`services/positive_pay.py`) is **pure**: no DB, no
I/O, no network, no account numbers. It takes the items the bank reports as
*presented* and the items we *issued*, and labels each presented item:

| Classification | Meaning | Fraud? |
|---|---|---|
| `matched_ok` | Found by normalised check number, `|presented − issued| <= tolerance` | No |
| `amount_mismatch` | Found by check number, amount differs beyond tolerance — an **ALTERED** cheque | **Yes** |
| `not_on_file` | No issued cheque with that number — a cheque we **never wrote** | **Yes** |

Check numbers are normalised before matching (`normalize_check_number`: strip,
upper-case, drop every non-alphanumeric char), so `"1001"`, `"#1001"`, and
`"chk-1001"` collapse to a comparable form — mirroring the
`vendor_statement_recon` invoice normalisation. Default tolerance is one cent
(`Decimal("0.01")`). The classifier returns per-item `ReturnItemResult`s + roll-up
counts (`presented_count`, `matched_ok`, `amount_mismatch`, `not_on_file`).

## File-item builders (DB → formatter dataclasses)

Two async builders in `services/positive_pay.py` project tenant rows into the
formatter dataclasses. Both are entity-scoped and never log an account / routing
number.

- **`build_check_issue_items(db, *, run, entity_id, account_number="")`** —
  selects the run's cheque payments (`method == "check"`, excluding `failed` /
  `cancelled` / `voided` **at the time it's called**), joins `Invoice` for the
  payee name, and projects each into a `CheckIssueItem`. `check_number` is the
  Payment's `reference`; `issue_date` is the run's `executed_at` date (or
  today). Returns `(items, total_amount, mapping)` where `mapping` is
  `[(normalized_check#, invoice_id, amount)]`. Called **only at file
  generation**; the result is persisted onto `meta["issued_map"]` so return
  processing never calls this again (see § Return handling — a second live call
  at return time would reflect a payment's CURRENT status, not what was
  actually on the file already sent to the bank).
- **`build_ach_authorization_items(db, *, org_id, entity_id)`** — selects
  `active` vendors whose `bank_details` carry both a routing and an account
  number, and projects each into an `AchAuthorizationItem`. Vendors without ACH
  bank details are skipped (nothing to authorize).

## API surface

Mounted at `/api/positive-pay` (`app/api/positive_pay.py`). Money is `Decimal`
end-to-end; every mutation is RBAC-gated, writes an audit row, and is
entity-scoped.

| Method + path | Purpose |
|---|---|
| `POST /positive-pay/payment-runs/{run_id}/check-issue` | Generate the check-issue file for a payment run (`bank_format?` body, default `csv`). **Idempotent** on (run, format): returns the existing file 200, else renders + stores + persists 201. |
| `POST /positive-pay/ach-authorization` | Generate a standalone ACH debit-authorization file for the org (`bank_format?`). 201. |
| `GET /positive-pay` | List files (filters: `file_type`, `status`; paginated `page` / `page_size`), entity-scoped. |
| `GET /positive-pay/{file_id}` | Detail. |
| `GET /positive-pay/{file_id}/download` | Stream the rendered file from MinIO. Verifies `file_key`'s first segment equals the caller's org (cross-tenant gate — **same 404** for wrong-org and missing, so it can't enumerate). |
| `POST /positive-pay/{file_id}/process-return` | Process the bank's return against a check-issue file (`presented_items: [{check_number?, amount?}]`). Classifies, raises deduped fraud Exceptions, flips status to `returned_processed`, stores the summary in `meta`. 422 if the file isn't a check-issue file. |
| `DELETE /positive-pay/{file_id}` | Delete the file row. 204. |

The originating company name + cheque account number are read from the
control-plane `Organization.settings` (`settings.company.name`;
`settings.payments.check_account_number` → `payments.account_number`). When no
account is configured the file still renders (account-less) and `account_last4`
is `None`.

### RBAC

Positive pay is a **treasury control** — clerks are excluded, matching the
Payments surface.

- **read** (list / detail / download) = `admin`, `ap_manager`, `cfo`
- **write** (generate / process-return / delete) = `admin`, `ap_manager`

Every read/write is **entity-scoped** (`X-Entity-ID` narrows to a subsidiary via
`apply_entity_scope` / `get_entity_id` / `get_write_entity_id`).

### Audit actions

Every mutation writes an `audit_log` row via `dispatch_audit` (append-only at
the DB layer). `entity_type` is `positive_pay_file`; `details` are **PII-free**
(file type, bank format, item count, total as string, run id — never an account
number):

| Action | Emitted by |
|--------|-----------|
| `positive_pay.check_issue_generated` | `POST .../check-issue` |
| `positive_pay.ach_authorization_generated` | `POST /ach-authorization` |
| `positive_pay.return_processed` | `POST .../process-return` (`details` = the return summary counts) |
| `positive_pay.deleted` | `DELETE /{id}` |

## Return handling — both fraud signals become `fraud_flag` Exceptions

`POST .../process-return` classifies each presented item against the
POINT-IN-TIME `meta["issued_map"]` snapshot persisted on the file at
generation — what was actually sent to the bank — **never** a live re-query of
the run's current payment statuses (issue #178: a cheque issued, then later
voided in the app, must still classify against what the bank was told, or its
presentment is mislabeled `not_on_file` and orphaned from its invoice — the
exact altered/fraud case Positive Pay exists to catch). It raises a deduped
`fraud_flag`
Exception for **every** fraud signal (`exception_type="fraud_flag"`,
`severity="error"`, `status="open"`, description `"Positive Pay return: check
<num> <reason>"` — no account numbers):

- **`amount_mismatch`** (an *altered* cheque whose number we did issue) maps to
  its invoice → an invoice-scoped `fraud_flag`. Dedupe: skip if an open/escalated
  `fraud_flag` already exists for that invoice (mirrors
  `invoice_warnings._ensure_exception`).
- **`not_on_file`** (a cheque the bank cleared that we *never issued* — the
  strongest fraud signal) has, by definition, no invoice on our side. It becomes
  a **standalone `fraud_flag` with `invoice_id = None`**, so it's a first-class,
  queryable item in the exception queue rather than a buried JSON field. Dedupe:
  skip if an open/escalated invoice-less `fraud_flag` with the same description
  (which carries the unique cheque number) already exists.

**Why this needs a nullable `invoice_id`.** Migration `0049` drops the
`exceptions.invoice_id` NOT NULL constraint precisely so a never-issued cheque
can be a real Exception without fabricating a placeholder invoice (which would
pollute the AP ledger + every aggregate). One consequence: an invoice-less
exception can't be auto-resolved by an agent (there's no invoice to act on), so
`POST /api/exceptions/{id}/agent-resolve` returns **422** for it — human triage
only. The exceptions list already `outerjoin`s the invoice, so these rows render
with a null `invoice_id` / `invoice_number`. (Vendor-statement reconciliation
still models its `missing_on_our_side` rows as recon **lines**, not Exceptions —
that's a deliberate choice for its resolve/ignore lifecycle, no longer forced by
the constraint.)

The file then flips to `status = "returned_processed"` with a PII-free summary in
`meta["return_summary"] = {presented_count, matched_ok, amount_mismatches,
not_on_file, exceptions_created}`, and each run is appended to
`meta["return_history"]` so a re-processed bank redelivery never clobbers the
prior outcome.

## storage helper

`storage.upload_positive_pay_file(org_id, file_id, content: bytes, filename,
content_type) -> (file_key, download_url)` (`services/storage.py`) stores the
already-rendered bytes (not an `UploadFile`) at
`{org_id}/positive-pay/{file_id}/{_safe_filename(filename)}` and returns the key
+ the `/api/positive-pay/{file_id}/download` path. The filename is sanitised via
the shared `_safe_filename` (strips path separators / `..` / dotfiles); the body
is capped at `MAX_FILE_SIZE` defensively even though the content is
system-generated. The download route re-checks the key's org segment before
streaming.

## Frontend

Route `/positive-pay` (`frontend/src/routes/positive-pay/+page.svelte`), under
the **Billing** nav group (`$lib/nav.ts`, roles `admin` / `ap_manager` / `cfo` —
clerks excluded, matching the backend). Built from the shared `ui/` components:
`PageHeader` + a KPI row (`KpiCard`: total files, items exported, returns
flagged) + file-type `FilterChips` (All / Check issue / ACH auth) + `SearchBox`
+ a `DataTable` of files with clickable rows (`RowLink`) and an armed-confirm
delete (`RowAction`). Money renders via `<Money>` in the file's stored
`currency` (falling back to the org default for legacy NULL rows); URL-backed
filter state via `$page` + `replaceState` (deep-link `?id=` opens a file's
detail modal).

The modal (`$lib/components/modals/PositivePayModal.svelte`) is dual-mode:

- **Generate** — pick file type (check-issue → choose a payment run;
  ACH-authorization → org-wide) + bank format, gated to managers.
- **Detail** — file metadata, a download button (fetches the bytes with the
  Bearer token via `api.downloadBlob`), the return summary + unmatched-returns
  table, and a "process return" sub-form (paste `check#,amount` per line) for
  check-issue files.

Typed client `$lib/api/positivePay.ts` over the shared `api` object; types in
`$lib/types/positivePay.ts`.

## Migration

- **0048_positive_pay** — creates `positive_pay_files` + its indexes + the
  partial unique index `uq_positive_pay_run_format`. Tenant DB only (gated on the
  `invoices` table → no-ops on the control plane, fans out via
  `scripts/migrate_all_tenants.py`). Idempotent (`CREATE TABLE/INDEX IF NOT
  EXISTS`, the partial unique index via raw SQL `CREATE UNIQUE INDEX IF NOT
  EXISTS ... WHERE payment_run_id IS NOT NULL`). Mirrors
  `app.models.positive_pay` exactly so a fresh tenant built by
  `tenant_provisioning._create_tenant_tables` (`create_all`) matches a migrated
  one. The FKs (payment_runs, entities) exist by earlier migrations.

## Local-first

No new external dependency and no new `pnpm` script — generation, download, and
return processing all run in-process against MinIO. There's no background sweep
(generation is user-triggered), so `pnpm dev` runs the whole feature with no
cloud credential and nothing to enable.

## Deferred / future work

- **More per-bank formats.** Only `csv` + `fixed_width` ship; a real BAI2 layout
  and named-bank templates (Wells Fargo ARP, BofA, Chase) slot in behind the
  same `@register_positive_pay_formatter` interface.
- **Automated return ingestion.** Today the bank's return is pasted / posted to
  `process-return`; a webhook or SFTP poller that ingests the bank's
  "items-presented-not-on-file" report and calls the same classifier is the
  natural next step.
- **Direct bank transmission.** The file is generated + downloaded; pushing it to
  the bank's cash-management portal (SFTP / API) is out of scope for this slice.
