"""Payment-rail classification — one source of truth for what a
``Payment.method`` value *means* to downstream regulatory reporting.

The rail matters most for **IRS 1099 reporting**. Payments settled over a
payment card are reported by the card settlement entity on Form **1099-K**,
and the payer must therefore leave them OFF Form 1099-NEC / 1099-MISC:

    "Payments made with a credit card or payment card and certain other
    types of payments, including third-party network transactions, must be
    reported on Form 1099-K by the payment settlement entity ... and are not
    subject to reporting on Form 1099-NEC or Form 1099-MISC."
    — IRS, General Instructions for Certain Information Returns
      (see also the Form 1099-NEC/MISC instructions, "Exceptions").

Including a card payment in the payer's 1099 box amount therefore
**over-reports** the vendor's income and double-counts the same dollar
against the processor's 1099-K. Under-reporting is equally wrong, so the
classification is an explicit, exhaustive registry rather than a guess.

Rail-by-rail:

===================== ============ ===============================================
``Payment.method``    1099 treated Why
===================== ============ ===============================================
``ach``               reportable   Bank ACH credit — a direct payment by the payer.
``wire``              reportable   Domestic bank wire.
``rtp``               reportable   RTP / instant bank rail (The Clearing House).
``check``             reportable   Paper cheque drawn on the payer's account.
``sepa``              reportable   SEPA Credit Transfer — a bank rail.
``international_ach`` reportable   NACHA Global ACH / IAT — a bank rail.
``international_wire``reportable   SWIFT wire.
``virtual_card``      EXCLUDED     Payment card. The card network / issuer is the
                                   settlement entity and files the 1099-K.
===================== ============ ===============================================

An unknown or ``NULL`` rail counts as **reportable**. That is deliberate:
NULL is what the manual / legacy payment paths write (``POST /api/payments``
leaves ``method`` unset when the caller omits it), those are overwhelmingly
bank payments, and the safe default for an unclassified rail is to keep it on
the 1099 rather than silently drop money out of a filed figure. The drift
guard below is what stops "unknown" from becoming a hiding place.

**Drift guard:** ``tests/test_payment_methods.py`` fails if a rail appears in
the ``PaymentMethod`` schema enum, in any registered payment adapter's
``supported_methods``, or in the corridor selector's method table without
being classified here. Add the rail to exactly one of the two frozensets
below and the guard goes green again — there is no way to add a rail and
leave its tax treatment undecided.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, func

# Card rails: excluded from 1099-NEC/1099-MISC (the settlement entity files
# a 1099-K for these).
CARD_PAYMENT_METHODS: frozenset[str] = frozenset({"virtual_card"})

# Non-card rails: the payer reports these on the 1099.
IRS_1099_REPORTABLE_METHODS: frozenset[str] = frozenset(
    {
        "ach",
        "check",
        "wire",
        "rtp",
        "sepa",
        "international_ach",
        "international_wire",
    }
)

KNOWN_PAYMENT_METHODS: frozenset[str] = CARD_PAYMENT_METHODS | IRS_1099_REPORTABLE_METHODS


def normalize_payment_method(value: str | None) -> str:
    """Canonical form of a stored rail: trimmed + lower-cased. ``None`` → ``""``."""
    return (value or "").strip().lower()


def is_card_payment_method(value: str | None) -> bool:
    """True when the rail is a payment card (1099-K territory, not ours)."""
    return normalize_payment_method(value) in CARD_PAYMENT_METHODS


def is_1099_reportable_method(value: str | None) -> bool:
    """True when the payer reports this rail on a 1099-NEC / 1099-MISC.

    Everything that is not a known card rail is reportable — including an
    unknown or ``NULL`` method. See the module docstring for why.
    """
    return not is_card_payment_method(value)


def card_payment_method_clause(column: ColumnElement[str | None]) -> ColumnElement[bool]:
    """SQL predicate: the rail in ``column`` is a card rail.

    ``COALESCE`` keeps this two-valued — a ``NULL`` method evaluates to
    ``FALSE`` (not card ⇒ reportable) instead of ``NULL``, so the negation
    ``~card_payment_method_clause(...)`` is safe to use as a filter without
    re-handling NULLs at every call site.
    """
    return func.lower(func.btrim(func.coalesce(column, ""))).in_(sorted(CARD_PAYMENT_METHODS))
