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
  never blocks the vendor write. Gated by `AP_VENDOR_SCREENING_ENABLED`.
- **Manual re-screen** — `POST /api/vendors/{id}/screen` (`check_type="manual"`).
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

## Periodic re-screening

`services/vendor_rescreen.py` is a background loop (same shape as
`contract_renewal`): every `AP_VENDOR_RESCREEN_INTERVAL_SECONDS` it sweeps every
tenant, re-screens active vendors whose `last_screened_at` is NULL or older than
`AP_VENDOR_RESCREEN_AFTER_DAYS`, and notifies AP managers when a vendor newly
flips to `match` / `review`. Disabled by default (`AP_VENDOR_RESCREEN_ENABLED`).

## Risk scoring

`services/vendor_risk_scoring.py` blends three PII-free signals into a 0–100
composite + a bucket: **sanctions** (latest check result / score), **fraud**
(open `fraud_flag` exceptions on the vendor's invoices), and **payment history**
(trailing volume / count / failed-or-voided runs). Compute-on-demand, no
external calls. `GET /api/vendors/{id}/risk` reads the persisted score;
`POST /api/vendors/{id}/risk/recompute` recomputes + persists;
`GET /api/vendors/risk/summary` returns the org-wide distribution by bucket.

## Providers

`mock` (local-first default), `complyadvantage`, `dowjones`, `refinitiv`.
Selected per-org via `Organization.settings.compliance.sanctions.provider`;
real keys live in sops, never committed. Adverse-media hits surface through the
same `ScreeningResult` (the list NAME identifies the category, e.g.
`ADVERSE_MEDIA`).

## Env vars

| Var | Default | Purpose |
|---|---|---|
| `AP_VENDOR_SCREENING_ENABLED` | `true` | Screen on vendor create / update (mock-safe, local-first). |
| `AP_VENDOR_RESCREEN_ENABLED` | `false` | Master switch for the periodic re-screen sweep. |
| `AP_VENDOR_RESCREEN_INTERVAL_SECONDS` | `86400` | Sweep interval. |
| `AP_VENDOR_RESCREEN_AFTER_DAYS` | `7` | Re-screen vendors whose last screen is older than this. |
