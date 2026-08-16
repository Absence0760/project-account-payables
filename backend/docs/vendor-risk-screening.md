# Vendor Risk & Sanctions Screening

Screens vendors against sanctions / PEP / adverse-media lists, blocks payments
to sanctioned entities, re-screens on a schedule, and rolls sanctions + fraud +
payment-history signals into a composite vendor risk score. Roadmap:
*Priority — Sanctions & Vendor Risk Screening*.

Local-first: the default `mock` sanctions adapter returns `clear` for everything
that isn't an obvious fixture, so a fresh clone screens vendors with no cloud
account and no key. Deployed envs point
`Organization.settings.compliance.sanctions.provider` at a real provider.

## Pieces

| Concern | Where |
|---|---|
| Screening providers (adapters) | `services/sanctions_adapters/` — `mock`, `complyadvantage`, `dowjones`, `refinitiv` |
| Hit-category taxonomy (incl. adverse media) | `services/sanctions_categories.py` |
| Screen-one-vendor primitive | `services/vendor_screening.py::screen_vendor_record` |
| Screen on create / update + manual re-screen + review queue + block | `api/vendors.py` |
| Pre-payment gate (refuse blocked vendors + per-payment screen) | `services/compliance.py::check_payment_compliance` |
| Periodic re-screen sweep | `services/vendor_rescreen.py` (background loop) |
| Composite risk scoring | `services/vendor_risk_scoring.py` + `api/vendor_risk.py` |
| Screening trail (append-only) | `sanctions_checks` table (`models/sanctions_check.py`) |
| Denormalised current state | `vendors` columns (migration 0042) |

## Data model

`sanctions_checks` (migration 0018) is the append-only trail — one row per
screen, with `check_type` (`initial` | `periodic` | `manual` | `pre_payment`),
`result` (`clear` | `match` | `review_required`), `risk_score`, `matched_list`
(the list NAME), and `raw_response` (JSONB; raw provider match details live
**only** here, never in logs / HTTP bodies — invariant #7).

### Hit categories (and adverse media)

A provider reports not just a verdict but **what kind of hit** it was. The
adapters normalise their own vocabulary into a fixed taxonomy on
`ScreeningResult.categories` — `sanctions` / `pep` / `adverse_media` /
`high_risk_country` (Refinitiv's World-Check `ADVERSE-MEDIA` maps to
`adverse_media`; the `mock` adapter simulates it for the fixture names in
`_DEFAULT_ADVERSE_MEDIA`, overridable via `compliance.sanctions.mock_adverse_media`).

**Adverse media is negative-news screening**: press coverage of fraud or
corruption that has not reached a formal watchlist. It is a different
instruction to a reviewer than a list match — *review the relationship*, not
*stop the payment* — which is exactly why the bare verdict alone was not enough.

`services/sanctions_categories.py` owns the persisted half, and all three
consumers go through it so they cannot drift:

| Consumer | What the taxonomy does there |
|---|---|
| `compliance.check_payment_compliance` | adds its own `ComplianceDecision` reason on top of the verdict — on `review_required`, on `match`, **and on `clear`**, where the extra reason turns the verdict into a `hold`. A provider that reports negative news on a counterparty not yet on any list must not be auto-allowed. |
| `vendor_screening.screen_vendor_record` | folds the labels into the persisted `sanctions_checks` row and names them in the PII-free `vendor.screened` audit row; `ScreenOutcome.categories` / `.adverse_media` carry them to the caller. |
| `vendor_risk_scoring` | reads them back off the persisted row (it is compute-on-read and never calls an adapter), floors an adverse-media hit above a bare `review_required`, and names it in `Vendor.risk_factors.sanctions`. |

**No migration.** The labels ride `sanctions_checks.raw_response` under the
reserved key `_screening_categories` — the column is already JSONB, the
taxonomy is small and additive, and a dedicated column would fan a schema
change out to every tenant DB to store an enum list. The merge never mutates
the provider's own payload (an auditor still replays exactly what was
returned), and a `clear` screen's payload is left byte-identical. Reads are
tolerant: a row written before the taxonomy shipped, or one whose JSONB holds
something unexpected, reads as "no categories" rather than raising — a
screening-trail row must never be able to 500 the risk endpoint.

**The labels are PII-free by construction** — a fixed vocabulary, never
provider free text — which is what makes them safe on an audit row, in an API
response (`SanctionsCheckResponse.categories` / `.adverse_media`,
`ScreeningReviewItem.latest_categories` / `.adverse_media`) and in a UI badge,
while `raw_response`'s provider payload stays confined to the JSONB column and
is never serialized out (invariant #7).

Migration 0042 denormalises the current state onto `vendors`:

| Column | Meaning |
|---|---|
| `screening_status` | `unscreened` \| `clear` \| `review` \| `match` (mirrors the latest check) |
| `last_screened_at` | timestamp of the most recent screen |
| `payments_blocked` | hard payment block (sanctions match or manual) |
| `payments_blocked_reason` / `payments_blocked_at` | why / when (reason carries the list NAME only) |
| `risk_score` | composite 0–100 (`Numeric(5,2)`) |
| `risk_level` | `low` \| `medium` \| `high` \| `critical` \| `unknown` |
| `risk_factors` | per-signal breakdown JSONB (counts / scores / list NAMES only) |
| `risk_scored_at` | when risk was last computed |

## Screening flow

`vendor_screening.screen_vendor_record(db, *, vendor, organization_id,
org_settings, check_type, actor_id=None, correlation_id=None,
sanctions_adapter=None)` is the single primitive. It runs the adapter, appends a
`sanctions_checks` row, denormalises `screening_status` / `last_screened_at`,
sets `payments_blocked` on a `match`, and writes a PII-free `vendor.screened`
audit row. It mutates the session but does **not** commit — the caller owns the
transaction (mirrors `check_payment_compliance`). Returns a `ScreenOutcome`.

Call sites:

- **Vendor create / update** (`api/vendors.py`) — `check_type="initial"`. Update
  re-screens only when an identity field changed (`name`, `tax_id`,
  `bank_details.country`, beneficial owners). Best-effort: a screening failure
  never blocks the vendor write. Gated by `FEOH_VENDOR_SCREENING_ENABLED`.
- **Manual re-screen** — `POST /api/vendors/{id}/screen` (`check_type="manual"`).
- **Approved bank-detail change** (`api/vendors.py::approve_change_request`) —
  `check_type="bank_change"`. The dual-control gate catches the *approval* of a
  staged `bank_details` change (the BEC / bank-redirect fraud tail); once the new
  coordinates are applied, the vendor is re-screened against them (so a redirect
  to a high-risk jurisdiction surfaces as `review`, and a name that has since
  landed on a list hard-blocks) **and** every in-queue payable invoice for the
  vendor gets a de-duped `fraud_flag` payment hold. Best-effort — a screening
  failure never blocks applying the approved change. Gated by
  `FEOH_VENDOR_SCREENING_ENABLED`.
- **Periodic sweep** — `services/vendor_rescreen.py` (`check_type="periodic"`).
- **Pre-payment** — `check_payment_compliance` keeps its own
  `check_type="pre_payment"` screen (different verdict contract).

## Blocking payments

`check_payment_compliance` refuses a payment up front when
`vendor.payments_blocked` is set — before FX lock and before any payment
adapter is touched. A live sanctions `match` (from the pre-payment screen or a
prior screen) keeps the vendor blocked until an admin explicitly unblocks via
`POST /api/vendors/{id}/unblock`. `POST /api/vendors/{id}/block` is the manual
override.

## Review queue

`GET /api/vendors/screening/review-queue` lists vendors whose
`screening_status` is `match` or `review` (backed by the partial index on
`sanctions_checks(result)` + the `vendors(screening_status)` index).
`GET /api/vendors/{id}/screening-history` returns the full trail for one vendor.

**Frontend surface.** The dedicated review-queue page lives at
`frontend/src/routes/vendors/screening/+page.svelte` (sidebar link
**Screening**, gated to admin / ap_manager / cfo). It lists the flagged
vendors with their screening pill (`ui/ScreeningBadge.svelte`), risk score, and
last-screened date over `getScreeningReviewQueue()`, and a detail modal opens
the per-vendor screening-history timeline plus **block / unblock** and
**re-screen** actions. An adverse-media hit renders as an amber **Negative
news** pill on the same `ScreeningBadge` (a `adverseMedia` prop reusing the
existing calibrated tone — no new colour), reads as a *Hit categories* row in
the detail modal, appears on each history entry, and is searchable (the queue's
client-side filter matches the formatted category labels, so typing "negative
news" narrows to them). Block/unblock is gated in the UI on the granular
permission `vendor.block` (`auth.can('vendor.block')`) — not a role check — so
the control is hidden for a non-holder; re-screen is gated on
`auth.isManager`. The backend enforces both regardless. (The vendor LIST page
`/vendors` also surfaces the same screening/risk actions per-vendor via
`VendorModal`.)

## Periodic re-screening

`services/vendor_rescreen.py` is a background loop (same shape as
`contract_renewal`): every `FEOH_VENDOR_RESCREEN_INTERVAL_SECONDS` it sweeps every
tenant, re-screens active vendors whose `last_screened_at` is NULL or older than
`FEOH_VENDOR_RESCREEN_AFTER_DAYS`, and notifies AP managers when a vendor newly
flips to `match` / `review`. Disabled by default (`FEOH_VENDOR_RESCREEN_ENABLED`).

**Each vendor is screened and committed on its own**, and a vendor whose screen
raises is logged (exception CLASS only), counted in `RescreenResult.vendor_failures`,
and skipped — the tenant's remaining vendors carry on.

That is a correctness requirement, not a nicety. The loop previously had no
per-vendor guard and committed once per tenant, and `screen_vendor_record` has
no internal `except`. So one vendor whose screen raised (adapter transport
error, a bad credential, one malformed row) aborted the whole tenant sweep
*before* the commit: not even the vendors already screened on that tick got
their `last_screened_at` advanced. Every one of them stayed due, the same poison
vendor was re-selected on the next tick, and the sweep made **zero progress
forever** — a sanctions-compliance control silently not running, with nothing
surfacing staleness (`GET /api/vendors/screening/review-queue` only *orders* by
`last_screened_at`). `vendor_failures` is counted apart from `failures`
(whole-tenant sweep aborts) so the two can't be confused in the sweep's log line.

## Risk scoring

`services/vendor_risk_scoring.py` blends three PII-free signals into a 0–100
composite + a bucket: **sanctions** (latest check result / score / hit
categories), **fraud** (open `fraud_flag` exceptions on the vendor's invoices),
and **payment history** (trailing volume / count / failed-or-voided runs).
Compute-on-demand, no external calls.

The sanctions sub-score reads the persisted row's hit categories (above). An
**adverse-media** hit floors the sub-score at `_ADVERSE_MEDIA_FLOOR` (65),
above the `_REVIEW_FLOOR` (60) a category-less `review_required` scores — the
`mock` adapter scores negative news 50, so without the floor it would rank
*below* a generic jurisdiction flag, inverting the two signals. The floor
applies outside the `review_required` branch so a `clear` verdict carrying
negative news still moves the score, matching the compliance gate, which holds
that payment. A `match` still dominates at 100. Rows written before the
taxonomy shipped score exactly as they did before.

`GET /api/vendors/{id}/risk` reads the persisted score;
`POST /api/vendors/{id}/risk/recompute` recomputes + persists;
`GET /api/vendors/risk/summary` returns the org-wide distribution by bucket.

## Providers

`mock` (local-first default), `complyadvantage`, `dowjones`, `refinitiv`.
Selected per-org via `Organization.settings.compliance.sanctions.provider`;
real keys live in sops, never committed. Adverse-media and other hit kinds
surface through `ScreeningResult.categories` — see
[Hit categories](#hit-categories-and-adverse-media) for how they reach the
verdict, the trail and the risk score.

## Env vars

| Var | Default | Purpose |
|---|---|---|
| `FEOH_VENDOR_SCREENING_ENABLED` | `true` | Screen on vendor create / update (mock-safe, local-first). |
| `FEOH_VENDOR_RESCREEN_ENABLED` | `false` | Master switch for the periodic re-screen sweep. |
| `FEOH_VENDOR_RESCREEN_INTERVAL_SECONDS` | `86400` | Sweep interval. |
| `FEOH_VENDOR_RESCREEN_AFTER_DAYS` | `7` | Re-screen vendors whose last screen is older than this. |
