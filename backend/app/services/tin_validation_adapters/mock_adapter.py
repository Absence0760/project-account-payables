"""Mock TIN-validation adapter — offline format + structural validation.

This is the local-first default. It runs the deterministic IRS structural
rules in ``format_rules`` and nothing else: it never reaches the IRS, so it
can flag a *malformed* TIN with certainty but can only ever report
``name_match=None`` (unchecked) for a well-formed one.

For tests that need to simulate an IRS-confirmed name mismatch without a
live partner, ``config["mock_name_mismatch_last4"]`` lists last-4 values
that should come back ``name_match=False`` even though the format is fine —
lets a test exercise the "valid format, wrong name" branch deterministically.
"""

from __future__ import annotations

from app.services.tin_validation_adapters.base import (
    VERDICT_INVALID,
    VERDICT_VALID,
    TINValidationResult,
)
from app.services.tin_validation_adapters.dispatcher import register_tin_validation_adapter
from app.services.tin_validation_adapters.format_rules import check_format


@register_tin_validation_adapter("mock")
class MockTINValidationAdapter:
    provider_name = "mock"

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self._name_mismatch_last4 = {
            str(v) for v in (self.config.get("mock_name_mismatch_last4") or [])
        }

    async def validate(
        self,
        *,
        tin: str,
        legal_name: str | None = None,
        tin_type_hint: str | None = None,
    ) -> TINValidationResult:
        fmt = check_format(tin, tin_type_hint)
        if not fmt.ok:
            return TINValidationResult(
                verdict=VERDICT_INVALID,
                tin_type=fmt.tin_type,
                tin_last4=fmt.last4,
                name_match=None,
                provider=self.provider_name,
                reason_code=fmt.reason_code,
            )

        # Optional deterministic name-mismatch simulation (tests only).
        if legal_name and fmt.last4 in self._name_mismatch_last4:
            return TINValidationResult(
                verdict=VERDICT_INVALID,
                tin_type=fmt.tin_type,
                tin_last4=fmt.last4,
                name_match=False,
                provider=self.provider_name,
                reason_code="irs_mismatch",
            )

        # Well-formed. The mock can't reach the IRS, so name_match stays
        # None (unchecked) — the verdict reflects format only.
        return TINValidationResult(
            verdict=VERDICT_VALID,
            tin_type=fmt.tin_type,
            tin_last4=fmt.last4,
            name_match=None,
            provider=self.provider_name,
            reason_code=None,
        )

    async def test_connection(self) -> bool:
        return True
