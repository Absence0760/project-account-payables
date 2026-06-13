"""International tax — VAT / GST / withholding computation + rules engine.

A small, data-driven tax layer for cross-border AP:

- ``country_rules`` — the country-specific rules engine (data, not code per
  country): regime, standard rate, reverse-charge support, withholding
  brackets, registration-id label.
- ``vat`` — VAT computation incl. the EU B2B reverse-charge mechanism.
- ``gst`` — GST computation for AU (single), IN (CGST/SGST/IGST split), CA
  (federal + provincial), and other GST jurisdictions.
- ``withholding`` — withholding-tax computation by jurisdiction + payment
  category, with optional double-tax-treaty rate.
- ``report`` — per-period tax report aggregating persisted ``IntlTaxRecord``
  rows (VAT / GST / withholding collected vs owed).

The consumption-tax *rate* is resolved by the pluggable
``services/tax_rate_adapters`` (mock default, Avalara/TaxJar skeletons); the
*rules* (which regime, reverse-charge, WHT shape) come from this package's
rules engine. Money is ``Decimal`` throughout — the *money is exact*
project invariant covers tax math.
"""
