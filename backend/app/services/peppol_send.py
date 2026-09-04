"""Idempotent PEPPOL outbound send service.

Orchestrates one transmission of an invoice over the PEPPOL network:

    map → assert_valid(check_tax) → generate_ubl → resolve_participant →
    INSERT PeppolTransmission('sending') → adapter.send → update row →
    dispatch_audit → commit

Reuses the shipped ``e_invoice`` package wholesale (no UBL duplication) and the
``peppol_adapters`` family. Idempotency is enforced at the DATA layer by the
partial unique index ``uq_peppol_one_live_per_invoice_direction`` — the row is
inserted in ``sending`` state and flushed BEFORE the (slow, networked) transmit,
so two concurrent sends can never both reach the gateway. A handler-level
short-circuit returns an existing live row without a second adapter call or a
duplicate audit row.

PII invariant: the receiver participant value (a supplier tax/org id) lives on
the row and inside the UBL payload legitimately, but is NEVER logged or placed
in an audit ``details`` payload — only the scheme, message id, doc type, and
provider are recorded. :class:`PeppolSendError` and ``EInvoiceValidationError``
carry PII-free codes only.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice, InvoiceLineItem
from app.models.peppol_transmission import PeppolTransmission
from app.services.audit_dispatch import dispatch_audit
from app.services.e_invoice import (
    BuyerIdentity,
    assert_bis3_conformant,
    assert_valid,
    generate_ubl,
    invoice_to_einvoice_document,
)
from app.services.peppol_adapters import (
    PEPPOL_BIS_BILLING_DOCTYPE,
    PEPPOL_BIS_BILLING_PROCESSID,
    ParticipantId,
    PeppolSendError,
    TransmissionRequest,
    UnknownPeppolProviderError,
    get_peppol_adapter,
)

logger = logging.getLogger(__name__)

_LIVE_STATUSES = ("sending", "sent", "delivered")
_DIRECTION_OUTBOUND = "outbound"


async def _select_live_outbound(
    db: AsyncSession, invoice_id: uuid.UUID
) -> PeppolTransmission | None:
    """Return the existing non-failed outbound transmission for the invoice, if any."""
    return (
        await db.execute(
            select(PeppolTransmission).where(
                PeppolTransmission.invoice_id == invoice_id,
                PeppolTransmission.direction == _DIRECTION_OUTBOUND,
                PeppolTransmission.status.in_(_LIVE_STATUSES),
            )
        )
    ).scalar_one_or_none()


async def send_invoice_over_peppol(
    db: AsyncSession,
    *,
    invoice: Invoice,
    line_items: list[InvoiceLineItem],
    buyer: BuyerIdentity,
    sender_id: ParticipantId,
    receiver_id: ParticipantId,
    organization_id: uuid.UUID,
    entity_id: uuid.UUID | None,
    actor_id: uuid.UUID,
    peppol_config: dict | None,
) -> tuple[PeppolTransmission, bool]:
    """Transmit ``invoice`` over PEPPOL. Idempotent.

    Returns ``(transmission, already_sent)``. ``already_sent`` is True when the
    idempotency short-circuit (or the IntegrityError race path) returned a
    pre-existing live transmission without re-transmitting.

    Raises :class:`app.services.e_invoice.EInvoiceValidationError` (tax-invalid,
    422 at the route) or :class:`PeppolSendError` (e.g.
    ``receiver_not_registered``, 422 at the route) before any row is persisted.
    """
    # 1. Idempotency short-circuit (fast path; the DB index is the guarantee).
    existing = await _select_live_outbound(db, invoice.id)
    if existing is not None:
        return existing, True

    # 2-4. Map → validate (hard tax reject) → serialize. These raise BEFORE we
    # persist anything, so a tax-invalid invoice leaves no row behind.
    doc = invoice_to_einvoice_document(invoice, line_items, buyer)
    assert_valid(doc)  # check_tax=True by default → EInvoiceValidationError on fail
    # The parties' electronic addresses (BT-34 / BT-49) ARE the AS4 participant
    # ids: the receiver is the counterparty this invoice names as seller, the
    # sender is us — the document's buyer. They live only on the transport
    # arguments, so stamp them onto the document before it is serialized;
    # otherwise the UBL we transmit carries no `cbc:EndpointID` on either party
    # and cannot claim the profile the AS4 doc-type id below asserts.
    doc.seller.endpoint_id = receiver_id.value
    doc.seller.endpoint_scheme_id = receiver_id.scheme
    doc.buyer.endpoint_id = sender_id.value
    doc.buyer.endpoint_scheme_id = sender_id.scheme
    # We are about to put this document on the network under
    # PEPPOL_BIS_BILLING_DOCTYPE, which ASSERTS EN 16931 / BIS Billing 3.0
    # conformance. Refuse here rather than transmit a document that provably
    # does not meet it — the Access Point would reject it anyway, and a claim we
    # can disprove ourselves must never leave the building. PII-free
    # `field: code` body, mapped to 422 at the route.
    assert_bis3_conformant(doc)
    payload = generate_ubl(doc)

    # 5-6. Resolve the receiver via SMP/SML (mockable, no DNS). Unknown → refuse
    # before persisting a row.
    #
    # The ADAPTER is resolved here too, above the slot claim in step 7, and a
    # provider we have no adapter for is refused rather than resolved to `mock`
    # (`decisions.md` §29). The mock's `send` reports success with a synthetic
    # message id and no network involved, which step 9 would write onto the row
    # as `sent` + a `message_id` and step 10 would record as `invoice.peppol_sent`
    # — a legally-significant document reported as transmitted to a supplier that
    # never received it, with the live-transmission slot occupied so the honest
    # resend came back `already_sent`. Refusing HERE means no row exists at all.
    # The reason code names the condition, never the admin's raw settings value.
    try:
        adapter = get_peppol_adapter(peppol_config)
    except UnknownPeppolProviderError as exc:
        logger.warning(
            "[peppol] provider %r has no registered adapter — refusing to transmit",
            exc.provider,
        )
        raise PeppolSendError("peppol_provider_not_configured") from None
    capability = await adapter.resolve_participant(receiver_id)
    if not capability.registered:
        raise PeppolSendError(capability.unregistered_reason or "receiver_not_registered")
    # The receiver must actually accept the BIS Billing 3.0 invoice doc type —
    # the SMP step exists to catch this before we commit a live row + emit. Only
    # enforce when the AP reported a doc-type set (an empty tuple means the
    # mock/gateway didn't enumerate, so we don't false-reject).
    if (
        capability.supported_doc_types
        and PEPPOL_BIS_BILLING_DOCTYPE not in capability.supported_doc_types
    ):
        raise PeppolSendError("receiver_doctype_unsupported")

    business_message_id = invoice.correlation_id.hex
    # Capture the id BEFORE the flush: an IntegrityError + rollback expires the
    # ORM `invoice`, so a later `invoice.id` would trigger a lazy reload (IO)
    # outside the async greenlet → MissingGreenlet. The local is rollback-safe.
    invoice_id = invoice.id

    # 7. Claim the idempotency slot: INSERT 'sending' and flush BEFORE the
    # networked transmit. A concurrent send that already claimed it raises
    # IntegrityError on the partial unique index → return the committed live row.
    transmission = PeppolTransmission(
        invoice_id=invoice_id,
        direction=_DIRECTION_OUTBOUND,
        participant_scheme=receiver_id.scheme,
        participant_value=receiver_id.value,
        sender_scheme=sender_id.scheme,
        sender_value=sender_id.value,
        doc_type_id=PEPPOL_BIS_BILLING_DOCTYPE,
        process_id=PEPPOL_BIS_BILLING_PROCESSID,
        business_message_id=business_message_id,
        status="sending",
        provider=adapter.provider_name,
        amount=invoice.amount,
        currency=invoice.currency,
        organization_id=organization_id,
        entity_id=entity_id,
    )
    db.add(transmission)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        racer = await _select_live_outbound(db, invoice_id)
        if racer is not None:
            return racer, True
        raise

    # 8. Transmit (the only networked step; the slot is already claimed).
    result = await adapter.send(
        TransmissionRequest(
            sender=sender_id,
            receiver=receiver_id,
            doc_type_id=PEPPOL_BIS_BILLING_DOCTYPE,
            process_id=PEPPOL_BIS_BILLING_PROCESSID,
            payload=payload,
            business_message_id=business_message_id,
        )
    )

    # 9. Update the row from the adapter outcome. A failed send NEVER persists a
    # message_id (defence in depth alongside the adapter): a non-NULL message_id
    # on a failed row would occupy `uq_peppol_message_id`, and the supported
    # failed→retry path reuses the same business_message_id — so a real AP that
    # echoes the same MessageId on the retry would collide on commit.
    transmission.status = result.status if result.success else "failed"
    transmission.message_id = result.message_id if result.success else None
    transmission.failure_reason = result.failure_reason
    transmission.raw_response = result.raw_response
    transmission.transmitted_at = datetime.now(UTC)

    # 10. Audit — PII-free details only (scheme, message id, doc type, provider).
    action = "invoice.peppol_sent" if result.success else "invoice.peppol_send_failed"
    await dispatch_audit(
        db,
        correlation_id=invoice.correlation_id,
        organization_id=organization_id,
        actor_id=actor_id,
        action=action,
        entity_type="invoice",
        entity_id=invoice.id,
        details={
            "provider": adapter.provider_name,
            "receiver_scheme": receiver_id.scheme,
            "message_id": result.message_id,
            "doc_type": PEPPOL_BIS_BILLING_DOCTYPE,
            "status": transmission.status,
            "failure_reason": result.failure_reason,
        },
    )

    # 11. Commit.
    await db.commit()
    await db.refresh(transmission)
    return transmission, False
