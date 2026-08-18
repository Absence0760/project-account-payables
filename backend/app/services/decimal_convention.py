"""Which separator is the decimal point — the pure, shared primitive.

``850,00`` is 850.00 in most of Europe and 85000 if the comma groups thousands.
Nothing about the token alone settles it; the **document** does, because a
document is written in one convention throughout. That call was made for
supplier statements (`decisions.md` §27) and lives here so the *other* reader of
model-produced money strings — AI invoice extraction — reads them by the same
rules instead of re-deriving them (or, as it did, stripping every comma
unconditionally and booking a hundredfold overstatement).

Three functions, no IO, no state:

* :func:`convention_proved_by` — what ONE token proves on its own, or ``None``.
* :func:`detect_convention` — the document-level answer over many tokens.
* :func:`apply_convention` — rewrite a token into a plain ``-?ddd.dd`` string.

They all take a **core**: the token reduced to digits, separators and an
optional leading ``-``. Producing that core is the caller's job, because each
caller strips a different set of decorations (a statement cell carries currency
symbols and French non-breaking spaces; a vision model's field can also carry a
``%`` or a Unicode minus).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

AMOUNT_CONVENTION_US = "us"  # 1,234.56 — comma groups, period is the decimal
AMOUNT_CONVENTION_EU = "eu"  # 1.234,56 — period groups, comma is the decimal

AmountConvention = Literal["us", "eu"]


def _is_group(part: str) -> bool:
    """A well-formed thousands group: exactly three digits."""
    return len(part) == 3 and part.isdigit()


def convention_proved_by(core: str) -> AmountConvention | None:
    """Which convention this ONE token proves, or ``None`` if it proves nothing.

    Three shapes are self-describing:

    * **Both separators** (``1,234.56`` / ``1.234,56``) — the rightmost one is
      the decimal point, because grouping separators never follow it.
    * **The same separator repeated** (``1,234,567``) — only grouping repeats,
      *and only when every run after the first is a real three-digit group*.
      ``1.2.3`` is not a grouped number, it is malformed, and calling it EU
      would silently read it as ``123``.
    * **One separator with a one- or two-digit tail** (``850,00`` / ``850.5``) —
      it must be the decimal point: money carries at most two decimal places,
      and no grouping run is shorter than three digits. This is the shape an
      unconditional ``replace(",", "")`` gets wrong, reading ``850,00`` as
      ``85000``.

    One shape is genuinely ambiguous and deliberately proves nothing: a single
    separator with a **three-digit tail** (``1,234`` / ``1.234``) is a thousands
    group under one convention and a three-decimal-place value under the other.
    Resolving it is what :func:`detect_convention` exists for; letting it vote
    would be circular.
    """
    has_comma = "," in core
    has_period = "." in core
    if has_comma and has_period:
        if core.rfind(".") > core.rfind(","):
            return AMOUNT_CONVENTION_US
        return AMOUNT_CONVENTION_EU
    if not has_comma and not has_period:
        return None
    sep = "," if has_comma else "."
    parts = core.split(sep)
    if len(parts) > 2:
        if not all(_is_group(p) for p in parts[1:]):
            return None  # malformed, not grouped — prove nothing, parse nothing
        return AMOUNT_CONVENTION_US if sep == "," else AMOUNT_CONVENTION_EU
    if len(parts[1]) in (1, 2):
        return AMOUNT_CONVENTION_EU if sep == "," else AMOUNT_CONVENTION_US
    return None


def detect_convention(cores: Iterable[str]) -> AmountConvention | None:
    """Resolve the convention a whole document is written in.

    One unambiguous ``1.234,56`` anywhere settles every bare ``1.200`` beside
    it. Returns ``None`` when the document proves nothing (no separators, or
    only ambiguous three-digit tails) **and** when its tokens contradict each
    other — both mean "no document-level answer", and per-token readings still
    apply, so a contradictory document still reads its self-describing tokens
    correctly rather than being dragged onto one convention wholesale.
    """
    votes: set[AmountConvention] = set()
    for core in cores:
        proved = convention_proved_by(core)
        if proved is not None:
            votes.add(proved)
            if len(votes) > 1:
                return None  # contradictory — no document-level answer
    if len(votes) == 1:
        return votes.pop()
    return None


def apply_convention(core: str, convention: AmountConvention | None) -> str:
    """Rewrite ``core`` into a plain decimal string under the right reading.

    ``convention`` is the document-level answer and is consulted **only** for
    the one genuinely ambiguous shape (a single separator with a three-digit
    tail); every self-describing token is read on its own terms, so a token
    cannot be mis-read just because the rest of the document voted otherwise.
    Passing ``None`` keeps the historical US reading for that ambiguous shape.

    The result is not guaranteed parseable — a malformed token comes back
    unchanged-ish and the caller's ``Decimal(...)`` is what refuses it.
    """
    reading = convention_proved_by(core)
    if reading is None and ("," in core or "." in core):
        reading = convention or AMOUNT_CONVENTION_US
    if reading == AMOUNT_CONVENTION_EU:
        return core.replace(".", "").replace(",", ".")
    return core.replace(",", "")
