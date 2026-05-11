# International Payments

End-to-end support for paying vendors outside the org's home currency
or country. The feature glues together four small subsystems:

| Layer | File | Purpose |
|---|---|---|
| Banking validators | `app/utils/banking.py` | IBAN mod-97, SWIFT/BIC format, SEPA zone membership |
| FX adapter pattern | `app/services/fx_adapters/` | Pluggable rate providers; mock + Open Exchange Rates ship today |
| Corridor selector | `app/services/payment_corridor.py` | Pure function: (src currency, tgt currency, tgt country) → method + flags |
| Quote optimizer | `app/services/corridor_quotes.py` | Multi-provider price ranking: ask N processors, pick cheapest or fastest |
| Sanctions / KYC | `app/services/sanctions_adapters/` + `app/services/compliance.py` | OFAC / EU / PEP screening, KYC gating per corridor, AML trailing-spend signal |
| Orchestrator | `app/services/international_payments.py` | `prepare_international_payment` builds the Payment row; `compute_fx_gain_loss` for reporting |

The executor (`app/api/payments.py::execute_payment_run`) routes every
payment through the orchestrator when any of these is true:

- The invoice's currency differs from the org's home currency
- The payment's method is `sepa` or `international_wire`
- The vendor's `bank_details.iban` or `bank_details.swift_bic` is set

Domestic same-currency payments skip the orchestrator entirely — no
FX call, no extra validation.

## Corridor selection

`pick_corridor(source_currency, target_currency, target_country, requested_method=None)`
returns a frozen `CorridorChoice`. Resolution order:

1. Explicit `requested_method` from the caller (UI override / vendor
   preference). Requirement flags (`requires_swift`, `requires_iban`,
   `requires_fx`) are still derived from the corridor shape so
   validation isn't skipped.
2. Cross-currency → `international_wire`, FX leg required, SWIFT
   required, IBAN required iff destination is in the SEPA zone.
3. Same-currency USD to the US → `ach`.
4. Same-currency EUR to a SEPA country → `sepa`, IBAN required, no
   SWIFT, no FX. `processor_hint = "wise"`.
5. Same-currency USD to a NACHA Global ACH destination (CA, MX, GB,
   BR, and a handful of LATAM corridors — see
   `_GLOBAL_ACH_DESTINATIONS` in `payment_corridor.py`) →
   `international_ach`. Cheaper than SWIFT for low-value recurring
   payments. No FX leg, no SWIFT, no IBAN — IAT uses local account
   formats.
6. Same-currency, foreign, non-SEPA, non-Global-ACH → `international_wire`,
   SWIFT required, no IBAN, no FX.

SEPA membership lives in `SEPA_COUNTRIES` (`app/utils/banking.py`).
Global-ACH destinations live in `_GLOBAL_ACH_DESTINATIONS`
(`payment_corridor.py`).

## Multi-route quote optimization

`services/corridor_quotes.compare_quotes(payload, org_settings, mode=...)`
asks every configured processor for a `CorridorQuote`, ranks them,
and returns the winner plus the runners-up. Mode is `cheapest`
(default) or `fastest`. Adapter exceptions become `available=False`
quotes (with sanitized reason strings — never the raw provider
message) so a flaky provider can't poison the auction.

Opt-in via `Organization.settings.payments.providers` (a list of
per-provider config dicts). When the field is absent, the legacy
`payments.provider` single-config is lifted into a one-element list
and the optimizer is a no-op. `NoEligibleCorridorError` raises when
zero providers can quote the requested corridor; the caller fails
the payment with `failure_reason="no_eligible_corridor"`.

Per-payment adapters implement `quote_payment(payload) -> CorridorQuote`.
The default base-class implementation reports `available=True` iff
the payload's method is in `supported_methods`, with zero fees;
concrete adapters override with their real fee schedule.

## KYC / AML compliance

`services/compliance.check_payment_compliance` is the gate between
`prepare_international_payment` and `adapter.create_payment`. Three
sub-checks run in order; the most-severe verdict wins:

1. **Sanctions / PEP screening** via the configured
   `sanctions_adapter`. A `match` returns `refuse`; a
   `review_required` returns `hold`. Every screening call writes an
   append-only `sanctions_checks` audit row (raw provider response in
   JSONB; never echoed to logs or HTTP responses — invariant #7).

2. **KYC status gating**. Corridors with high-risk methods (`sepa`,
   `international_wire`, `international_ach` by default, configurable
   via `compliance.high_risk_corridor_methods`) refuse the payment if
   `vendor.kyc_status != "verified"` AND the amount exceeds
   `compliance.kyc_required_above` (default $1,000). KYC gap is a
   refuse, not a hold — regulatory intent is that the AP team
   cannot override.

3. **AML trailing-12m spend signal**. Sum of completed payments to
   this vendor in the last 365 days plus the new payment; if it
   reaches `compliance.aml_spend_alert_threshold` (default $100k),
   returns `hold` with a review reason. Setting the threshold to
   `0` disables this check entirely.

Verdict resolution:

| Verdict | Effect on Payment row | Adapter call? |
|---|---|---|
| `allow` | proceed normally | yes |
| `hold` | `status=pending_compliance`, reasons in `failure_reason` | no |
| `refuse` | `status=failed`, `failure_reason="compliance_refusal: ..."` | NO — the executor never calls `create_payment` |

Sanctions adapter contract (`services/sanctions_adapters/`):

```python
@register_sanctions_adapter("my_provider")
class MyAdapter:
    provider_name = "my_provider"
    async def screen_vendor(
        self, *, vendor_name: str, vendor_country: str | None,
        vendor_tax_id: str | None = None,
        beneficial_owners: list[dict] | None = None,
    ) -> ScreeningResult: ...
    async def test_connection(self) -> bool: ...
```

Registered today: `mock`, `complyadvantage` (skeleton — wire shape
matches CA's `/searches` API; live API key required).

Org settings:

```json
"compliance": {
  "sanctions": { "provider": "complyadvantage", "api_key": "..." },
  "kyc_required_above": "1000",
  "aml_spend_alert_threshold": "100000",
  "high_risk_corridor_methods": ["sepa", "international_wire", "international_ach"]
}
```

`vendors` carries `kyc_status` (`pending` | `verified` | `rejected` |
`not_required`), `kyc_verified_at`, `kyc_verified_by`, and
`beneficial_owner_data` (JSONB list of `{name, country, dob, ...}`
dicts the screening adapter passes through). Migration 0018.

## FX rate locking

`prepare_international_payment` calls `fx_adapter.get_rate(src, tgt)`
exactly once and persists the result on the Payment row:

- `source_currency` — org's home currency
- `source_amount` — `target_amount / fx_rate` (rounded to 2dp)
- `fx_rate` — the rate at submission time (1 src = N tgt)
- `fx_locked_at` — provider-supplied timestamp; UTC now() if absent
- `corridor` — chosen method name
- `target_country` — ISO 3166-1 alpha-2

The locked rate is what an auditor replays. A later market move
between submission and settlement does NOT rewrite `fx_rate`.

## FX gain/loss

`compute_fx_gain_loss(invoice_amount, invoice_currency,
paid_source_amount, paid_source_currency, fx_rate_at_invoice,
fx_rate_at_payment)` returns a `Decimal`:

- Positive → realized **gain** (paid less in source currency than
  accrued at booking)
- Negative → realized **loss**
- `Decimal("0.00")` when invoice and payment currencies match

Sign convention matches GAAP / IFRS. A zero `fx_rate_at_invoice`
raises `ValueError` — defensive against a caller passing a stale
record.

## Vendor bank fields

Stored in `Vendor.bank_details` (JSONB):

| Key | Set by | Read by | Surfaced to UI |
|---|---|---|---|
| `counterparty_id` | UI (admin) | adapter | yes |
| `account_last4`, `routing_last4` | UI | display only | yes |
| `bank_name` | UI | display | yes |
| `iban` | admin endpoint | orchestrator | **no** — `iban_last4` only |
| `swift_bic` | UI / admin | orchestrator + adapter | yes (public bank code) |
| `country` | UI / IBAN-derived | corridor | yes |

The full IBAN never appears in the `VendorBankDetails` Pydantic
schema. Pinned by `tests/test_pii_protection.py`.

## Adding an FX provider

```python
@register_fx_adapter("my_provider")
class MyAdapter:
    provider_name = "my_provider"

    def __init__(self, config: dict | None = None): ...

    async def get_rate(self, source: str, target: str) -> FXRate: ...
    async def test_connection(self) -> bool: ...
```

Same registry pattern as `card_adapters`, `extraction_adapters`,
`erp_adapters`. The dispatcher auto-imports built-ins
(`fx_adapters/mock_adapter.py`, `fx_adapters/openexchangerates.py`)
and falls back to `mock` on unknown / empty config.

## Migrations

`alembic/versions/0017_international_payments.py` adds six nullable
columns to `payments` (tenant DB only):

- `source_currency` VARCHAR(3)
- `source_amount` NUMERIC(15, 2)
- `fx_rate` NUMERIC(18, 8)
- `fx_locked_at` TIMESTAMPTZ
- `corridor` VARCHAR(40)
- `target_country` VARCHAR(2)

Plus a partial index on `corridor` for the reporting query that
breaks spend down by corridor.

`alembic/versions/0018_kyc_compliance.py` extends `vendors` with four
nullable / defaulted KYC columns and creates the append-only
`sanctions_checks` audit table:

- `vendors.kyc_status` VARCHAR(20) DEFAULT 'not_required'
- `vendors.kyc_verified_at` TIMESTAMPTZ
- `vendors.kyc_verified_by` UUID
- `vendors.beneficial_owner_data` JSONB
- `sanctions_checks` table with `(vendor_id, checked_at DESC)` and
  a partial `(result)` index for the AP review queue.

## Tests

| File | Coverage |
|---|---|
| `tests/test_banking_validation.py` | IBAN mod-97 (10 valid examples + 11 negatives), SWIFT/BIC format, SEPA membership |
| `tests/test_fx_adapters.py` | Mock adapter (rates + overrides + unknown currency), dispatcher fallback, OpenExchangeRates HTTP wiring, FXRate immutability |
| `tests/test_payment_corridor.py` | 13 cases covering cross-currency, US/SEPA/foreign rails, explicit override, frozen dataclass |
| `tests/test_cross_border_ach.py` | NACHA Global ACH (IAT) routing: CA/MX/GB/BR pick `international_ach`; JP falls through to SWIFT; explicit override; `is_international_payment` recognizes the new rail |
| `tests/test_corridor_quotes.py` | Cheapest + fastest ranking, unavailable provider can't win, adapter exception sanitised (no PII in `unavailable_reason`), `NoEligibleCorridorError` when zero providers quote, legacy single-provider shape, dedupe |
| `tests/test_compliance.py` | Mock sanctions adapter (clear / match / review_required / beneficial-owner hit), `check_payment_compliance` verdict resolution (refuse on match + KYC gap; hold on review + AML), audit-row persistence, dispatcher fallback, **end-to-end** sanctions refusal through `execute_payment_run` (adapter NEVER called) |
| `tests/test_international_payments.py` | `prepare_international_payment` happy paths + refusals; `compute_fx_gain_loss` directionality; `is_international_payment` predicate; **end-to-end** through `execute_payment_run` with a EUR invoice on a USD-home org → locked rate + corridor + invoice flip |
