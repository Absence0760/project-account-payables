"""Base punch-out adapter interface + value objects.

Punch-out is the e-procurement pattern where a buyer "punches out" from their
own system into a supplier's hosted catalog site, shops there, and the supplier
returns the cart back into the buyer's system as a requisition. The dominant
wire protocols are **cXML** (Ariba/Coupa lineage — an XML PunchOutSetupRequest
→ supplier start URL → PunchOutOrderMessage cart return) and **OCI** (SAP's
Open Catalog Interface — an HTML-form round-trip with `NEW_ITEM-*` fields).

This module is the protocol-agnostic spine both shapes implement:

    buyer setup  → ``build_setup_request`` → :class:`PunchoutStartResult`
    cart return  → ``parse_order_message`` → :class:`PunchoutCart`

The adapter never touches the DB or commits — it only builds the outbound
setup payload + start URL and parses the inbound cart bytes into normalized,
``Decimal``-money cart items. The ``mock`` adapter synthesises both ends
entirely in-process (no supplier, no network) so the whole flow runs under
``pnpm dev``; the ``cxml`` adapter builds/parses real cXML and fails closed
(``punchout_not_configured``) when the supplier transport/credential is absent,
mirroring the PEPPOL ``as4_gateway`` posture.

PII / secret invariant: a supplier credential (shared secret, identity) lives
in config and inside the cXML header — NEVER in a log line or an HTTP error
body. :class:`PunchoutError` carries a PII-free reason *code* only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


class PunchoutError(Exception):
    """Raised when a punch-out setup/return cannot proceed.

    Carries a PII-free reason *code* only (e.g. ``punchout_not_configured``,
    ``catalog_not_punchout``, ``no_punchout_url``) — never a supplier secret,
    identity, or payload. ``str(exc)`` is therefore always safe in an error body.
    """

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class PunchoutSetupContext:
    """Everything an adapter needs to build a PunchOutSetupRequest.

    ``return_url`` is where the supplier POSTs the cart back (our public return
    endpoint, tenant + buyer-cookie encoded). ``buyer_cookie`` is the opaque
    correlation token the supplier must echo in the returned cart so we can match
    it to exactly one :class:`~app.models.procurement.PunchoutSession`.
    """

    catalog_name: str
    punchout_url: str  # the supplier's stored hosted-catalog endpoint
    buyer_cookie: str  # opaque correlation token (echoed back in the cart)
    return_url: str  # our public cart-return endpoint
    buyer_identity: str | None = None  # our org/network id sent in the cXML header


@dataclass
class PunchoutStartResult:
    """Result of building a setup request — the URL the buyer's browser visits."""

    start_url: str
    # The raw outbound setup payload (cXML string / OCI form fields) — advisory,
    # kept for audit/debug; never logged at INFO (may carry a shared secret).
    raw_request: str | None = None


@dataclass
class PunchoutCartItem:
    """One normalized line from a returned cart. Money is ``Decimal``."""

    description: str
    quantity: Decimal
    unit_price: Decimal
    sku: str | None = None
    uom: str | None = None
    currency: str = "USD"

    @property
    def line_total(self) -> Decimal:
        """Exact ``quantity * unit_price`` (both ``Decimal``)."""
        return Decimal(self.quantity) * Decimal(self.unit_price)


@dataclass
class PunchoutCart:
    """Normalized result of parsing a PunchOutOrderMessage / OCI return.

    ``buyer_cookie`` is the correlation token the supplier echoed — the return
    endpoint matches it to the originating session. ``items`` carry ``Decimal``
    money; ``total`` is the exact sum (recomputed, never trusted from the wire).
    """

    buyer_cookie: str
    items: list[PunchoutCartItem] = field(default_factory=list)
    currency: str = "USD"

    @property
    def total(self) -> Decimal:
        """Exact sum of line totals (``Decimal``), recomputed from the items."""
        return sum((it.line_total for it in self.items), Decimal("0"))


class PunchoutAdapter:
    """Base class for punch-out protocol adapters.

    Adapters are stateless — anything tenant/supplier-specific lives in
    ``self.config`` (per-org ``Organization.settings.punchout``). Two methods:
    build the outbound setup (→ start URL) and parse the inbound cart.
    """

    provider_name: str = "base"
    # Wire protocol shape this adapter speaks — "cxml" | "oci". Lets the OCI
    # shape slot in behind the same interface without a second method set.
    protocol: str = "cxml"

    def __init__(self, config: dict):
        self.config = config

    def build_setup_request(self, ctx: PunchoutSetupContext) -> PunchoutStartResult:
        """Build the PunchOutSetupRequest and return the supplier start URL.

        Raises :class:`PunchoutError` (PII-free code) when the supplier
        transport/credential is not configured (real adapters fail closed)."""
        raise NotImplementedError

    def parse_order_message(self, headers: dict, body: bytes) -> PunchoutCart | None:
        """Parse a returned PunchOutOrderMessage (cXML) / OCI form into a cart.

        The shared secret + buyer-cookie are verified by the route BEFORE this
        runs (the return endpoint is public-by-design). Returns ``None`` when the
        body is unparseable or carries no buyer cookie (so the route refuses a
        cart it could never correlate), mirroring the email-intake / PEPPOL
        parse-None drop."""
        raise NotImplementedError

    async def test_connection(self) -> bool:
        """Cheap credential / reachability check."""
        raise NotImplementedError
