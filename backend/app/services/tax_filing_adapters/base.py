"""1099 e-filing adapter interface.

E-filing submits a batch of 1099-NEC / 1099-MISC forms for a tax year to a
filing partner, which in turn transmits to the IRS FIRE / IRIS system (and,
where the partner offers it, to the states + recipient copies). The IRS does
not expose a direct public filing API to non-transmitters, so every real
deployment goes through a partner (Tax1099 is the common one).

The adapter contract is deliberately small:

  - ``submit_batch`` takes the already-aggregated per-vendor form payloads
    plus a caller-supplied ``idempotency_key`` and returns a
    ``FilingBatchResult``. The key makes a retried submission safe — the
    adapter (and the partner, via the same key) must not double-file.
  - ``test_connection`` probes auth.

The ``mock`` adapter is the local-first default: it "accepts" every
well-formed batch and returns a deterministic confirmation number derived
from the idempotency key, so the whole flow runs with no cloud account and
re-submitting the same key returns the same confirmation. The ``tax1099``
adapter is a skeleton for the real partner.

PII discipline: form payloads carry TINs (the IRS requires them on the
form), but the adapter must never log them; the result object carries only
counts + confirmation, never a TIN.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

# Batch-level status.
BATCH_ACCEPTED = "accepted"  # partner accepted the batch for transmission
BATCH_REJECTED = "rejected"  # partner rejected the whole batch (auth, schema)
BATCH_PARTIAL = "partial"  # some forms accepted, some rejected


@dataclass(frozen=True)
class FilingFormPayload:
    """One 1099 form to file. Money is Decimal — never float."""

    vendor_id: str
    form_type: str  # "1099-NEC" | "1099-MISC"
    recipient_name: str
    recipient_tin: str  # required by the IRS on the form; never logged
    box_amount: Decimal  # the form's total — sum of `box_amounts` when present
    tax_year: int
    # Per-box detail behind `box_amount`, keyed by box code ("MISC-1", "MISC-6").
    # A 1099-MISC carrying both rent and medical payments is TWO boxes on ONE
    # form; transmitting only the total files the whole figure in whichever box
    # the partner defaults to. Empty means "no split available" (a hand-built
    # row, or a tenant with no mapping configured) — a consumer then files
    # `box_amount` against its own box of record, which is the prior behaviour.
    box_amounts: Mapping[str, Decimal] = field(default_factory=dict)


@dataclass(frozen=True)
class FilingFormResult:
    """Per-form outcome. References the vendor, never echoes the TIN."""

    vendor_id: str
    form_type: str
    accepted: bool
    reason_code: str | None = None


@dataclass(frozen=True)
class FilingBatchResult:
    """Outcome of a batch submission."""

    status: str  # accepted | rejected | partial
    provider: str
    confirmation_number: str | None
    tax_year: int
    submitted_count: int
    accepted_count: int
    rejected_count: int
    forms: list[FilingFormResult] = field(default_factory=list)
    reason_code: str | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "provider": self.provider,
            "confirmation_number": self.confirmation_number,
            "tax_year": self.tax_year,
            "submitted_count": self.submitted_count,
            "accepted_count": self.accepted_count,
            "rejected_count": self.rejected_count,
            "reason_code": self.reason_code,
            "forms": [
                {
                    "vendor_id": f.vendor_id,
                    "form_type": f.form_type,
                    "accepted": f.accepted,
                    "reason_code": f.reason_code,
                }
                for f in self.forms
            ],
        }


class TaxFilingAdapter(Protocol):
    """The minimum contract every 1099 e-filing provider must satisfy."""

    provider_name: str

    async def submit_batch(
        self,
        *,
        tax_year: int,
        forms: list[FilingFormPayload],
        idempotency_key: str,
    ) -> FilingBatchResult:
        """Submit a year's 1099 forms. Must be idempotent on
        ``idempotency_key`` — a retried submission with the same key returns
        the same confirmation and does not double-file."""
        ...

    async def test_connection(self) -> bool:
        """Cheapest probe the provider supports. Returns True on success."""
        ...
