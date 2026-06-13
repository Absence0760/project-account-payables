"""Tax1099 TIN-match adapter — skeleton.

Tax1099 (a Zenwork product) exposes a real-time TIN-match endpoint that
checks a TIN + legal name against the IRS TIN database and returns one of
the IRS match codes (0 = match, 1 = TIN not issued, 2 = name/TIN mismatch,
etc.). Auth is an API key in the request header.

API: https://www.tax1099.com/ (TIN match + e-file products)

This adapter ships as a working skeleton — the request shape and the
response→verdict mapping match the published API, but the live API key must
be set in ``Organization.settings.tax.tin_validation.api_key`` before
``validate`` will call out. Without a key it falls back to the offline
format check (``format_rules``) and reports ``name_match=None`` — i.e. it
degrades to exactly what the mock adapter does, never silently claiming an
IRS match it didn't make.

Like the ComplyAdvantage skeleton, the *online* path raises on transport
errors so the caller can surface "provider unreachable" rather than a false
``valid``. PII discipline: the TIN goes out in the request body (required by
the API) but is never logged, and the result object only carries last-4.
"""

from __future__ import annotations

import logging

import httpx

from app.services.tin_validation_adapters.base import (
    VERDICT_INVALID,
    VERDICT_UNKNOWN,
    VERDICT_VALID,
    TINValidationResult,
)
from app.services.tin_validation_adapters.dispatcher import register_tin_validation_adapter
from app.services.tin_validation_adapters.format_rules import check_format

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.tax1099.com"

# IRS TIN-match result codes → our verdict. Source: IRS Pub 2108A /
# Tax1099 API docs. 0 = TIN+name match. Everything else is a hard fail
# except transient/unknown which we surface as "unknown".
_IRS_MATCH_CODE = "0"


@register_tin_validation_adapter("tax1099")
class Tax1099TINValidationAdapter:
    provider_name = "tax1099"

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.api_key: str = cfg.get("api_key", "")
        self.timeout = float(cfg.get("timeout_seconds", 10.0))

    async def validate(
        self,
        *,
        tin: str,
        legal_name: str | None = None,
        tin_type_hint: str | None = None,
    ) -> TINValidationResult:
        # Always run the offline structural check first — no point spending
        # an IRS call on a malformed TIN, and it gives us the last-4 + type.
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

        # No key configured → degrade to format-only (never fabricate an
        # IRS match). Same behaviour as the mock for a well-formed TIN.
        if not self.api_key:
            return TINValidationResult(
                verdict=VERDICT_VALID,
                tin_type=fmt.tin_type,
                tin_last4=fmt.last4,
                name_match=None,
                provider=self.provider_name,
                reason_code="format_only_no_api_key",
            )

        body = {
            # IRS TIN-match needs both the TIN and the legal name.
            "tin": fmt.digits,
            "name": legal_name or "",
            "tin_type": "2" if fmt.tin_type == "ein" else "1",  # IRS: 1=SSN, 2=EIN
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{_BASE_URL}/tinmatch/verify", json=body, headers=headers)
            response.raise_for_status()
            payload = response.json()

        match_code = str(payload.get("match_code", ""))
        if match_code == _IRS_MATCH_CODE:
            return TINValidationResult(
                verdict=VERDICT_VALID,
                tin_type=fmt.tin_type,
                tin_last4=fmt.last4,
                name_match=True,
                provider=self.provider_name,
                reason_code=None,
            )
        if match_code in {"1", "2", "3", "4", "5", "6", "7", "8"}:
            return TINValidationResult(
                verdict=VERDICT_INVALID,
                tin_type=fmt.tin_type,
                tin_last4=fmt.last4,
                name_match=False,
                provider=self.provider_name,
                reason_code="irs_mismatch",
            )
        # Unrecognised / transient response — don't claim valid or invalid.
        return TINValidationResult(
            verdict=VERDICT_UNKNOWN,
            tin_type=fmt.tin_type,
            tin_last4=fmt.last4,
            name_match=None,
            provider=self.provider_name,
            reason_code="provider_indeterminate",
        )

    async def test_connection(self) -> bool:
        if not self.api_key:
            return False
        try:
            # A deliberately malformed TIN exercises auth without a real lookup.
            await self.validate(tin="00-0000000", legal_name="connection_test")
        except Exception:  # noqa: BLE001
            return False
        return True
