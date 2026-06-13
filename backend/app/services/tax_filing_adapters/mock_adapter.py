"""Mock 1099 e-filing adapter — deterministic, offline, idempotent.

The local-first default. It never transmits anything: it validates that
every form is well-formed (a recipient TIN + a positive box amount) and
returns an ``accepted`` (or ``partial``) batch with a confirmation number
derived deterministically from the idempotency key.

Determinism on the key is what makes the *endpoint* idempotent without a
real partner: re-submitting the same ``idempotency_key`` returns the same
confirmation number, so the API layer can persist "this batch was filed,
here's the confirmation" and a retry is a no-op rather than a double-file.

It carries no TIN into logs or the result — only counts + confirmation.
"""

from __future__ import annotations

import hashlib

from app.services.tax_filing_adapters.base import (
    BATCH_ACCEPTED,
    BATCH_PARTIAL,
    BATCH_REJECTED,
    FilingBatchResult,
    FilingFormPayload,
    FilingFormResult,
)
from app.services.tax_filing_adapters.dispatcher import register_tax_filing_adapter
from app.services.tin_validation_adapters.format_rules import check_format


def _confirmation_number(idempotency_key: str, tax_year: int) -> str:
    digest = hashlib.sha256(f"{tax_year}:{idempotency_key}".encode()).hexdigest()
    # Human-readable-ish confirmation: MOCK-<year>-<12 hex chars>.
    return f"MOCK-{tax_year}-{digest[:12].upper()}"


@register_tax_filing_adapter("mock")
class MockTaxFilingAdapter:
    provider_name = "mock"

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    async def submit_batch(
        self,
        *,
        tax_year: int,
        forms: list[FilingFormPayload],
        idempotency_key: str,
    ) -> FilingBatchResult:
        confirmation = _confirmation_number(idempotency_key, tax_year)

        results: list[FilingFormResult] = []
        for f in forms:
            reason = self._reject_reason(f)
            results.append(
                FilingFormResult(
                    vendor_id=f.vendor_id,
                    form_type=f.form_type,
                    accepted=reason is None,
                    reason_code=reason,
                )
            )

        accepted = sum(1 for r in results if r.accepted)
        rejected = len(results) - accepted
        if not results or accepted == 0:
            status = BATCH_REJECTED
        elif rejected == 0:
            status = BATCH_ACCEPTED
        else:
            status = BATCH_PARTIAL

        return FilingBatchResult(
            status=status,
            provider=self.provider_name,
            # Confirmation only when at least one form went through.
            confirmation_number=confirmation if accepted else None,
            tax_year=tax_year,
            submitted_count=len(results),
            accepted_count=accepted,
            rejected_count=rejected,
            forms=results,
            reason_code=None if accepted else "no_filable_forms",
        )

    @staticmethod
    def _reject_reason(form: FilingFormPayload) -> str | None:
        if not check_format(form.recipient_tin).ok:
            return "tin_invalid"
        if form.box_amount <= 0:
            return "non_positive_amount"
        if not form.recipient_name.strip():
            return "missing_recipient_name"
        return None

    async def test_connection(self) -> bool:
        return True
