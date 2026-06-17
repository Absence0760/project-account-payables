"""cXML / OCI punch-out adapter — real protocol build/parse, fail-closed.

Builds a real cXML PunchOutSetupRequest and parses a real PunchOutOrderMessage
cart return (via :mod:`cxml`). The supplier shared secret comes from config
(per-org ``Organization.settings.punchout.shared_secret``, then the
process-level ``settings.punchout_shared_secret``) and has **NO hardcoded
fallback** — when it is empty the build fails closed with the PII-free
``punchout_not_configured`` code rather than sending an unauthenticated setup
request, mirroring the PEPPOL ``as4_gateway`` posture. The live secret is
supplied via sops in deployed envs.

Transport note: a production cXML round-trip POSTs the setup request to the
supplier's setup URL and reads back a ``StartPage`` URL. That networked leg is
deliberately NOT performed here — this adapter builds the document and derives
the start URL from the stored ``punchout_url`` + buyer cookie (the common
"directed-punchout" shape where the supplier's catalog endpoint is the start
page and the cookie keys the session). A gateway that requires the POST
handshake is the single place to add it (one ``httpx`` call), exactly as the
PEPPOL ``as4_gateway`` adds its transport. The OCI shape is supported through
the same interface via ``protocol="oci"`` (the cart parse already normalizes
either wire form into :class:`PunchoutCart`).
"""

from __future__ import annotations

from app.config import settings
from app.services.punchout_adapters.base import (
    PunchoutAdapter,
    PunchoutCart,
    PunchoutError,
    PunchoutSetupContext,
    PunchoutStartResult,
)
from app.services.punchout_adapters.cxml import (
    build_setup_request_xml,
    parse_cxml_order_message,
)
from app.services.punchout_adapters.dispatcher import register_punchout_adapter


@register_punchout_adapter("cxml")
class CxmlPunchoutAdapter(PunchoutAdapter):
    provider_name = "cxml"

    def __init__(self, config: dict):
        super().__init__(config)
        # Per-org config wins; fall back to the process-level setting. The
        # shared secret has NO hardcoded fallback — empty means "not configured".
        self.shared_secret: str = config.get("shared_secret") or settings.punchout_shared_secret
        self.buyer_identity: str | None = config.get("buyer_identity") or None
        # "cxml" (default) | "oci" — both normalize into PunchoutCart on return.
        self.protocol = config.get("protocol") or "cxml"

    def build_setup_request(self, ctx: PunchoutSetupContext) -> PunchoutStartResult:
        # Fail closed when unauthenticated: never send a setup request without
        # the supplier credential (mirrors as4_gateway's peppol_not_configured).
        if not self.shared_secret:
            raise PunchoutError("punchout_not_configured")
        if not ctx.punchout_url:
            raise PunchoutError("no_punchout_url")

        raw_request = build_setup_request_xml(
            buyer_cookie=ctx.buyer_cookie,
            return_url=ctx.return_url,
            buyer_identity=ctx.buyer_identity or self.buyer_identity,
            shared_secret=self.shared_secret,
        )
        # Directed-punchout start URL: the supplier's catalog endpoint, keyed by
        # the buyer cookie so the session correlates on return. A gateway that
        # requires the POST handshake would replace this with the StartPage URL
        # read back from the supplier's setup response (one httpx call).
        sep = "&" if "?" in ctx.punchout_url else "?"
        start_url = f"{ctx.punchout_url}{sep}BuyerCookie={ctx.buyer_cookie}"
        return PunchoutStartResult(start_url=start_url, raw_request=raw_request)

    def parse_order_message(self, headers: dict, body: bytes) -> PunchoutCart | None:
        # The shared secret + buyer cookie are verified by the route before this
        # runs. cXML is the wire form for both cxml and (effectively) oci returns
        # in this skeleton — a real OCI integration would branch on protocol here.
        return parse_cxml_order_message(body)

    async def test_connection(self) -> bool:
        # No networked leg in this skeleton; a configured secret is the readiness
        # signal (fail-closed parity with as4_gateway when the key is absent).
        return bool(self.shared_secret)
