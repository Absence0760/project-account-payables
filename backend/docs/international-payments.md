# International Payments

End-to-end support for paying vendors outside the org's home currency
or country. The feature glues together four small subsystems:

| Layer | File | Purpose |
|---|---|---|
| Banking validators | `app/utils/banking.py` | IBAN mod-97, SWIFT/BIC format, SEPA zone membership |
| FX adapter pattern | `app/services/fx_adapters/` | Pluggable rate providers; mock + Open Exchange Rates ship today |
| Corridor selector | `app/services/payment_corridor.py` | Pure function: (src currency, tgt currency, tgt country) → method + flags |
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
5. Same-currency, foreign, non-SEPA → `international_wire`, SWIFT
   required, no IBAN, no FX.

SEPA membership lives in `SEPA_COUNTRIES` (`app/utils/banking.py`).

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

## Migration

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

## Tests

| File | Coverage |
|---|---|
| `tests/test_banking_validation.py` | IBAN mod-97 (10 valid examples + 11 negatives), SWIFT/BIC format, SEPA membership |
| `tests/test_fx_adapters.py` | Mock adapter (rates + overrides + unknown currency), dispatcher fallback, OpenExchangeRates HTTP wiring, FXRate immutability |
| `tests/test_payment_corridor.py` | 13 cases covering cross-currency, US/SEPA/foreign rails, explicit override, frozen dataclass |
| `tests/test_international_payments.py` | `prepare_international_payment` happy paths + refusals; `compute_fx_gain_loss` directionality; `is_international_payment` predicate; **end-to-end** through `execute_payment_run` with a EUR invoice on a USD-home org → locked rate + corridor + invoice flip |
