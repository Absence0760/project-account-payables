"""UNCL4461 payment-means codes ⇄ our canonical ``Invoice.payment_method`` token.

`cbc:PaymentMeansCode` (UBL) and `ram:TypeCode` (CII) are **code-list** elements:
their content has to be a value from UN/EDIFACT code list 4461, not free text.
`Invoice.payment_method`, by contrast, holds our own lowercase dropdown token
(`ach` / `wire` / `check` / `credit_card` / `other`) — the vocabulary
`extraction._normalize_payment_method` normalizes onto.

Both directions live here, in one table, so they cannot drift:

* **inbound** — the `einvoice` extraction adapter reads a document's code and
  stores our token;
* **outbound** — `mapper.invoice_to_einvoice_document` has to do the inverse
  before the generators serialize it. It used to pass the token straight
  through, so every exported UBL/CII carried `<cbc:PaymentMeansCode>ach</…>` —
  not a UNCL4461 value, and not something any receiver (including our own
  inbound adapter) can read back.

Round-trip property, pinned by ``tests/test_e_invoice_payment_means.py``::

    payment_means_to_method(method_to_payment_means(m)) == m   # for every m

An unmappable token yields ``None`` and the caller **omits** the optional
element — a document with no payment means is valid; one with an out-of-list
code is not.
"""

from __future__ import annotations

# UNCL4461 code → our canonical payment_method token.
#
# 30 Credit transfer · 58 SEPA credit transfer · 31 Debit transfer ·
# 42 Payment to bank account · 20 Cheque · 48 Bank card · 54 Credit card.
PAYMENT_MEANS_TO_METHOD: dict[str, str] = {
    "30": "ach",
    "58": "ach",
    "31": "wire",
    "42": "wire",
    "20": "check",
    "48": "credit_card",
    "54": "credit_card",
}

# Our canonical token → the UNCL4461 code we EMIT for it. Several codes map onto
# one token inbound (30/58 → ach); outbound we pick the one generic enough to be
# correct for any tenant — a US ACH is a credit transfer (30), not specifically a
# SEPA one (58); a wire is a payment to a bank account (42).
#
# `other` is deliberately absent: UNCL4461's "1 — instrument not defined" would
# be admissible XML but carries no information and does not read back as
# anything, so omitting the optional element is the honest encoding.
METHOD_TO_PAYMENT_MEANS: dict[str, str] = {
    "ach": "30",
    "wire": "42",
    "check": "20",
    "credit_card": "48",
}


def payment_means_to_method(code: str | None) -> str | None:
    """UNCL4461 code → our canonical token, or ``None`` when unrecognised."""
    if not code:
        return None
    return PAYMENT_MEANS_TO_METHOD.get(code.strip())


def method_to_payment_means(method: str | None) -> str | None:
    """Our canonical token → the UNCL4461 code to emit, or ``None`` to omit.

    A value that is *already* a UNCL4461 code we recognise passes through
    unchanged — `Invoice.payment_method` is a free-form ``String(50)`` an API
    client can write directly, and re-encoding a code we can already read would
    lose information for no gain.
    """
    if not method:
        return None
    raw = method.strip()
    if raw in PAYMENT_MEANS_TO_METHOD:
        return raw
    return METHOD_TO_PAYMENT_MEANS.get(raw.lower())
