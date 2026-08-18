"""cXML build/parse helpers for punch-out — shared by the cxml + mock adapters.

cXML (Commerce eXtensible Markup Language) is the Ariba/Coupa-lineage punch-out
protocol. Two messages matter here:

- **PunchOutSetupRequest** — the buyer's outbound "start me a session" document.
  We build it (``build_setup_request_xml``) and POST it to the supplier; the
  supplier replies with a ``StartPage`` URL the buyer's browser visits.
- **PunchOutOrderMessage** — the supplier's inbound cart return. We parse it
  (``parse_cxml_order_message``) into a normalized :class:`PunchoutCart` with
  ``Decimal`` money, matching the ``BuyerCookie`` so the return endpoint can
  correlate the cart to its originating session.

XML parsing reuses the e_invoice package's XXE-hardened parser
(``resolve_entities=False`` + ``no_network=True`` + ``load_dtd=False``) — the
cart return is POSTed by an external supplier, so it is untrusted input.

PII / secret invariant: a ``SharedSecret`` lives in the cXML ``Credential`` and
in config — it is embedded when BUILDING the outbound request but is NEVER
logged. Parsing never echoes payload values into logs.
"""

from __future__ import annotations

from decimal import Decimal
from xml.sax.saxutils import escape

from lxml import etree

from app.services.e_invoice._xml import (
    find_all_local,
    find_path,
    parse_secure,
    to_decimal,
)
from app.services.punchout_adapters.base import PunchoutCart, PunchoutCartItem


def build_setup_request_xml(
    *,
    buyer_cookie: str,
    return_url: str,
    buyer_identity: str | None,
    shared_secret: str | None,
) -> str:
    """Build a minimal-but-valid cXML PunchOutSetupRequest.

    ``operation="create"`` opens a new session; ``BuyerCookie`` is the
    correlation token the supplier must echo in the returned cart;
    ``BrowserFormPost/URL`` is where the supplier POSTs the cart back (our public
    return endpoint). The ``SharedSecret`` (if configured) authenticates us to
    the supplier — included here but never logged.
    """
    identity = escape(buyer_identity or "unknown")
    cookie = escape(buyer_cookie)
    secret_block = f"<SharedSecret>{escape(shared_secret)}</SharedSecret>" if shared_secret else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<cXML>"
        "<Header>"
        f"<From><Credential><Identity>{identity}</Identity></Credential></From>"
        "<Sender>"
        f"<Credential><Identity>{identity}</Identity>{secret_block}</Credential>"
        "<UserAgent>FeohLedger</UserAgent>"
        "</Sender>"
        "</Header>"
        "<Request>"
        '<PunchOutSetupRequest operation="create">'
        f"<BuyerCookie>{cookie}</BuyerCookie>"
        "<BrowserFormPost>"
        f"<URL>{escape(return_url)}</URL>"
        "</BrowserFormPost>"
        "</PunchOutSetupRequest>"
        "</Request>"
        "</cXML>"
    )


def parse_cxml_order_message(body: bytes) -> PunchoutCart | None:
    """Parse a cXML PunchOutOrderMessage into a normalized cart.

    Returns ``None`` on malformed XML or a missing ``BuyerCookie`` (so the route
    refuses a cart it could never correlate). Money is parsed into ``Decimal``;
    the cart total is recomputed from the lines, never trusted from the wire.
    """
    try:
        root = parse_secure(body)
    except etree.XMLSyntaxError:
        return None

    buyer_cookie = _first_text(root, "BuyerCookie")
    if not buyer_cookie:
        return None

    currency = "USD"
    items: list[PunchoutCartItem] = []
    for item_in in find_all_local(root, "ItemIn"):
        parsed = _parse_item_in(item_in)
        if parsed is None:
            continue
        items.append(parsed)
        currency = parsed.currency

    return PunchoutCart(buyer_cookie=buyer_cookie, items=items, currency=currency)


def _first_text_within(scope: etree._Element | None, name: str) -> str | None:
    """Text of the first descendant of ``scope`` with the given local name."""
    if scope is None:
        return None
    for el in find_all_local(scope, name):
        if el.text and el.text.strip():
            return el.text.strip()
    return None


def _parse_item_in(item_in: etree._Element) -> PunchoutCartItem | None:
    """Parse one ``<ItemIn quantity="N">`` element into a cart item.

    cXML shape: ``ItemIn[@quantity] > ItemDetail > (UnitPrice/Money[@currency],
    Description, UnitOfMeasure)`` and ``ItemIn > ItemID > SupplierPartID``.

    **Every lookup is scoped to the sub-element that owns that field**, and this
    is load-bearing rather than tidiness. ``ItemIn`` legally carries ``Shipping``,
    ``Tax``, ``SpendDetail`` and ``Distribution > Charge`` as SIBLINGS of
    ``ItemDetail``, and each of those contains its own ``<Money>`` and
    ``<Description>``. A scan over every descendant let the LAST one win, so a
    cart line quoting 250.00 with 200.00 of tax was booked at 200.00 and
    described as "Sales tax" — a plausible-looking price that then flowed into a
    requisition, a PO, and the budget's committed spend.

    A cart with no ``ItemDetail`` at all yields no price (``0``) rather than
    borrowing a number from a sibling block: a zero line is visibly wrong to the
    buyer approving the requisition, a tax-priced one is not. Same
    skip-rather-than-guess call the offline statement reader makes.
    """
    qty = to_decimal(item_in.get("quantity")) or Decimal("1")

    unit_price = Decimal("0")
    currency = "USD"

    detail = find_path(item_in, "ItemDetail")
    # Prefer the exact ``ItemDetail > UnitPrice > Money`` path; fall back to the
    # first Money anywhere INSIDE ItemDetail (a supplier nesting it one level
    # deeper is still unambiguously quoting this line's price) — never outside.
    money = find_path(item_in, "ItemDetail", "UnitPrice", "Money")
    if money is None and detail is not None:
        monies = find_all_local(detail, "Money")
        money = monies[0] if monies else None
    if money is not None:
        parsed = to_decimal(money.text)
        if parsed is not None:
            unit_price = parsed
        currency = money.get("currency") or currency

    description = _first_text_within(detail, "Description")
    uom = _first_text_within(detail, "UnitOfMeasure")
    sku = _first_text_within(find_path(item_in, "ItemID"), "SupplierPartID")

    return PunchoutCartItem(
        description=description or sku or "Item",
        quantity=qty,
        unit_price=unit_price,
        sku=sku,
        uom=uom,
        currency=currency,
    )


def _first_text(root: etree._Element, name: str) -> str | None:
    """Text of the first descendant element with the given local name."""
    for el in find_all_local(root, name):
        if el.text and el.text.strip():
            return el.text.strip()
    return None
