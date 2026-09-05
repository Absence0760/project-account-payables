"""Tax1099 e-filing adapter — skeleton.

Tax1099 (Zenwork) is the common partner for programmatic 1099 e-filing: you
POST a batch of forms and it transmits to the IRS (FIRE/IRIS), files the
states, and mails/e-delivers recipient copies. Auth is an API key; the API
supports a submission-id / reference so a retried POST does not double-file.

API: https://www.tax1099.com/

This adapter ships as a working skeleton — the request shape and the
response→result mapping match the published API, but the live API key must
be set in ``Organization.settings.tax.filing.api_key`` before
``submit_batch`` will call out. Without a key it raises ``RuntimeError`` so
the orchestrator surfaces "filing provider unconfigured" rather than a false
``accepted`` (same pattern as the OXR FX + ComplyAdvantage skeletons).

Idempotency is delegated to the partner via the ``idempotency_key`` (sent as
the submission reference) AND enforced at our API layer (the filing batch
row keyed on the same idempotency key) — defence in depth against
double-filing. PII discipline: TINs are in the request body but never logged;
the result carries only counts + confirmation.
"""

from __future__ import annotations

import logging

import httpx

from app.services.tax_filing_adapters.base import (
    BATCH_ACCEPTED,
    BATCH_PARTIAL,
    BATCH_REJECTED,
    FilingBatchResult,
    FilingFormPayload,
    FilingFormResult,
)
from app.services.tax_filing_adapters.dispatcher import register_tax_filing_adapter

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.tax1099.com"


@register_tax_filing_adapter("tax1099")
class Tax1099FilingAdapter:
    provider_name = "tax1099"

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.api_key: str = cfg.get("api_key", "")
        self.timeout = float(cfg.get("timeout_seconds", 30.0))

    async def submit_batch(
        self,
        *,
        tax_year: int,
        forms: list[FilingFormPayload],
        idempotency_key: str,
    ) -> FilingBatchResult:
        if not self.api_key:
            raise RuntimeError("tax1099 filing adapter requires `api_key` in tax.filing config")

        body = {
            "tax_year": tax_year,
            # The partner dedupes on this reference — same key, same filing.
            "submission_reference": idempotency_key,
            "forms": [
                {
                    "form_type": f.form_type,
                    "recipient_name": f.recipient_name,
                    "recipient_tin": f.recipient_tin,
                    "amount": str(f.box_amount),  # string-Decimal, never float
                    # The per-box split, when the calculation produced one. A
                    # MISC form with rent AND medical payments is two boxes;
                    # sending only the total files it all in one.
                    **(
                        {"boxes": {b: str(a) for b, a in f.box_amounts.items()}}
                        if f.box_amounts
                        else {}
                    ),
                    "external_id": f.vendor_id,
                }
                for f in forms
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Idempotency-Key": idempotency_key,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{_BASE_URL}/efile/1099/batch", json=body, headers=headers
            )
            response.raise_for_status()
            payload = response.json()

        # Response shape:
        # {"confirmation_number": "...", "results": [
        #     {"external_id": "...", "form_type": "1099-NEC", "status": "accepted"},
        #     {"external_id": "...", "form_type": "1099-MISC", "status": "rejected",
        #      "error_code": "..."}, ...]}
        confirmation = payload.get("confirmation_number")
        raw_results = payload.get("results") or []
        results: list[FilingFormResult] = []
        for r in raw_results:
            accepted = str(r.get("status", "")).lower() == "accepted"
            results.append(
                FilingFormResult(
                    vendor_id=str(r.get("external_id", "")),
                    form_type=str(r.get("form_type", "")),
                    accepted=accepted,
                    reason_code=None if accepted else (r.get("error_code") or "rejected"),
                )
            )

        accepted_count = sum(1 for r in results if r.accepted)
        rejected_count = len(results) - accepted_count
        if not results or accepted_count == 0:
            status = BATCH_REJECTED
        elif rejected_count == 0:
            status = BATCH_ACCEPTED
        else:
            status = BATCH_PARTIAL

        return FilingBatchResult(
            status=status,
            provider=self.provider_name,
            confirmation_number=confirmation if accepted_count else None,
            tax_year=tax_year,
            submitted_count=len(results),
            accepted_count=accepted_count,
            rejected_count=rejected_count,
            forms=results,
            reason_code=None if accepted_count else "no_filable_forms",
        )

    async def test_connection(self) -> bool:
        if not self.api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{_BASE_URL}/account/ping",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                response.raise_for_status()
        except Exception:  # noqa: BLE001
            return False
        return True
