"""TIN-validation adapter interface.

A TIN (Taxpayer Identification Number) is either an EIN (employer ID,
``NN-NNNNNNN``) or an SSN/ITIN (``NNN-NN-NNNN``). Before filing a 1099 the
IRS expects the payer to have validated that the vendor's TIN + legal name
match IRS records — a mismatch triggers a CP2100 notice and, eventually,
backup-withholding obligations.

Two distinct concerns, both behind this interface:

  - **Format validation** is offline, deterministic, and free — check the
    digit grouping, reject the obvious-invalid patterns the IRS publishes
    (all-zero area, ``00`` group, etc.). The ``mock`` adapter does exactly
    this and nothing more, so the whole flow runs locally with no cloud
    account (local-first invariant).
  - **TIN match** is an online IRS / Tax1099 call that confirms TIN + name
    against IRS records. The ``tax1099`` adapter is a skeleton for that
    partner; it requires a live API key and falls back to format-only when
    none is configured.

The result never echoes the TIN itself — only a redacted last-4 and the
verdict — so a TIN never lands in a log line or an error body (PII-out-of-
logs invariant).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# Verdicts. ``valid`` = passed every check the adapter ran. ``invalid`` =
# failed a hard check (format or a confirmed IRS mismatch). ``unknown`` =
# the adapter could not reach a verdict (e.g. partner API unavailable);
# the caller decides whether to retry or treat as a soft failure.
VERDICT_VALID = "valid"
VERDICT_INVALID = "invalid"
VERDICT_UNKNOWN = "unknown"


@dataclass(frozen=True)
class TINValidationResult:
    """Outcome of a TIN validation. Carries no raw TIN — only the redacted
    last-4 and the verdict, so it is safe to persist / serialize / log."""

    verdict: str  # valid | invalid | unknown
    tin_type: str | None  # "ein" | "ssn" | None when unparseable
    tin_last4: str | None  # last 4 digits, for display only
    name_match: bool | None  # IRS TIN-name match result; None = not checked
    provider: str
    # Machine code for the failure, never a free-form string carrying the
    # TIN. e.g. "format_invalid", "ein_invalid_prefix", "irs_mismatch".
    reason_code: str | None = None

    @property
    def is_valid(self) -> bool:
        return self.verdict == VERDICT_VALID

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "tin_type": self.tin_type,
            "tin_last4": self.tin_last4,
            "name_match": self.name_match,
            "provider": self.provider,
            "reason_code": self.reason_code,
        }


class TINValidationAdapter(Protocol):
    """The minimum contract every TIN-validation provider must satisfy."""

    provider_name: str

    async def validate(
        self,
        *,
        tin: str,
        legal_name: str | None = None,
        tin_type_hint: str | None = None,
    ) -> TINValidationResult:
        """Validate a TIN (and optionally TIN-name match).

        ``tin`` may carry separators / whitespace; the adapter normalises.
        ``tin_type_hint`` is ``"ein"`` / ``"ssn"`` when the caller knows
        the vendor's entity type (e.g. from the W-9 classification);
        adapters use it to disambiguate the 9-digit format.
        """
        ...

    async def test_connection(self) -> bool:
        """Cheapest probe the provider supports. Returns True on success."""
        ...
