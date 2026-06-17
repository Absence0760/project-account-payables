"""Mock punch-out adapter — local dev + test default.

Simulates a supplier's hosted punch-out site entirely in-process: no network,
no real supplier. This is the default provider (``AP_PUNCHOUT_PROVIDER=mock``)
so ``pnpm dev`` runs the whole round-trip — setup → start URL → returned cart →
convert-to-requisition — without an external supplier or credential.

- ``build_setup_request`` synthesises a deterministic start URL off the stored
  ``punchout_url`` + the buyer cookie (what a real supplier would hand back as
  the SP page to redirect the buyer to). A catalog with no ``punchout_url``
  fails closed with the PII-free ``no_punchout_url`` code.
- ``parse_order_message`` accepts a dev JSON cart envelope (the easy local
  shape) AND a real cXML PunchOutOrderMessage (so the local return endpoint can
  be exercised either way). Money is parsed into ``Decimal``.

A returned cart with NO items deterministically synthesises a single demo line
so the local convert-to-requisition flow always has something to convert.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from app.services.punchout_adapters.base import (
    PunchoutAdapter,
    PunchoutCart,
    PunchoutCartItem,
    PunchoutError,
    PunchoutSetupContext,
    PunchoutStartResult,
)
from app.services.punchout_adapters.cxml import parse_cxml_order_message
from app.services.punchout_adapters.dispatcher import register_punchout_adapter


@register_punchout_adapter("mock")
class MockPunchoutAdapter(PunchoutAdapter):
    provider_name = "mock"
    protocol = "cxml"

    def build_setup_request(self, ctx: PunchoutSetupContext) -> PunchoutStartResult:
        if not ctx.punchout_url:
            # Real failure mode surfaced locally: a punch-out catalog with no URL.
            raise PunchoutError("no_punchout_url")
        # A real supplier returns its SP start page; we synthesise one off the
        # stored endpoint so the buyer's browser has somewhere to "visit".
        sep = "&" if "?" in ctx.punchout_url else "?"
        start_url = f"{ctx.punchout_url}{sep}mock_session={ctx.buyer_cookie}"
        return PunchoutStartResult(
            start_url=start_url,
            raw_request=(
                f"<MockPunchOutSetupRequest cookie='{ctx.buyer_cookie}' return='{ctx.return_url}'/>"
            ),
        )

    def parse_order_message(self, headers: dict, body: bytes) -> PunchoutCart | None:
        if not body:
            return None
        text = body.lstrip()
        # Dev JSON envelope: {"buyer_cookie": "...", "currency": "USD",
        #   "items": [{"description","sku","quantity","unit_price","uom"}]}.
        if text[:1] in (b"{", b"["):
            return self._parse_json(body)
        # Otherwise treat the body as a real cXML PunchOutOrderMessage.
        return parse_cxml_order_message(body)

    def _parse_json(self, body: bytes) -> PunchoutCart | None:
        try:
            envelope = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(envelope, dict):
            return None
        buyer_cookie = str(envelope.get("buyer_cookie") or "")
        if not buyer_cookie:
            return None
        currency = str(envelope.get("currency") or "USD")
        items: list[PunchoutCartItem] = []
        for raw in envelope.get("items") or []:
            if not isinstance(raw, dict):
                continue
            try:
                qty = Decimal(str(raw.get("quantity", "1")))
                unit_price = Decimal(str(raw.get("unit_price", "0")))
            except (InvalidOperation, ValueError):
                continue
            items.append(
                PunchoutCartItem(
                    description=str(raw.get("description") or raw.get("sku") or "Item"),
                    quantity=qty,
                    unit_price=unit_price,
                    sku=(str(raw["sku"]) if raw.get("sku") else None),
                    uom=(str(raw["uom"]) if raw.get("uom") else None),
                    currency=str(raw.get("currency") or currency),
                )
            )
        # A returned-but-empty cart still needs a line so the local convert flow
        # has something to convert — synthesise a deterministic demo item.
        if not items:
            items.append(
                PunchoutCartItem(
                    description="Punch-out demo item",
                    quantity=Decimal("1"),
                    unit_price=Decimal("49.99"),
                    sku="PUNCHOUT-DEMO",
                    uom="EA",
                    currency=currency,
                )
            )
        return PunchoutCart(buyer_cookie=buyer_cookie, items=items, currency=currency)

    async def test_connection(self) -> bool:
        return True
