"""PEPPOL AS4 inbound receive service (the receiver corner, C4).

A receiver's Access Point (C3) delivers an inbound BIS Billing 3.0 document to
us (C4) by POSTing the UBL/CII payload + metadata (sender participant id, AS4
MessageId, doc type, process id) to our per-tenant webhook with a provider HMAC
signature. This module turns that delivery into an :class:`Invoice` the AP team
sees in their queue — mirroring :func:`email_intake.process_inbound_email`
(create Invoice → upload payload → ``dispatch_extraction``) and the idempotency
discipline of :mod:`peppol_send`.

Dedupe is enforced at the DATA layer, not by app logic. The AS4 ``MessageId``
is the dedupe key; a redelivery repeats it. Two layers, the DB authoritative:

1. Fast path (advisory): a SELECT on ``message_id`` short-circuits the common
   sequential redelivery without creating an invoice.
2. Authoritative guarantee (the concurrent-redelivery race): both redeliveries
   can pass the SELECT, but the partial unique index ``uq_peppol_message_id``
   lets only one ``PeppolTransmission`` INSERT commit. The loser's
   ``IntegrityError`` rolls back its ENTIRE tenant transaction — including the
   Invoice it created — so no second invoice survives. To avoid even an orphaned
   S3 object on the loser, the transmission row is flushed (claiming the slot)
   BEFORE the payload is uploaded, exactly as :mod:`peppol_send` claims its
   idempotency slot before the networked transmit.

Redis ``is_event_already_processed`` is deliberately NOT used here (unlike the
payment webhook): for a create-an-invoice one-time effect, the durable DB unique
index is the correct guarantee — a 24h Redis TTL would let a later redelivery
slip through and is redundant given the index.

PII invariant: the supplier's participant value (``sender_value``), tax id, and
addresses live legitimately on the row and inside the received UBL payload, but
NEVER enter a log line, an audit ``details`` payload, or an HTTP response.
``reason`` codes and audit details carry only PII-free scheme/message-id/doc-type
fields. Money (``amount``) is ``Decimal`` — never float.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.invoice import Invoice, InvoiceStatus
from app.models.organization import Organization
from app.models.peppol_transmission import PeppolTransmission
from app.services.audit_dispatch import dispatch_audit
from app.services.e_invoice import (
    EInvoiceValidationError,
    parse_e_invoice,
)
from app.services.peppol_adapters import get_peppol_adapter

logger = logging.getLogger(__name__)

# Nil-UUID sentinel — inbound receive has no human actor, so every audit row
# written during receive points at this id. Identical to the value email-intake
# uses; the UI can render it as "system (peppol)".
SYSTEM_ACTOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")

_DIRECTION_INBOUND = "inbound"


@dataclass
class InboundPeppolMessage:
    """Normalised inbound delivery — what every PEPPOL adapter's
    ``parse_inbound`` emits after verifying the gateway envelope."""

    message_id: str  # the AS4/AP MessageId — the DEDUPE key
    sender_scheme: str  # receiver-corner counterparty (the supplier) participant scheme
    sender_value: str  # supplier org/tax id (PII — never logged)
    doc_type_id: str
    process_id: str
    payload: bytes  # raw UBL/CII XML bytes


@dataclass
class ReceiveResult:
    accepted: bool = False
    duplicate: bool = False
    tenant_slug: str | None = None
    invoice_id: uuid.UUID | None = None
    transmission_id: uuid.UUID | None = None
    # PII-free reason CODE only (e.g. "unknown_tenant", "bad_signature",
    # "malformed_document", "duplicate") — for the caller's log line, NEVER
    # returned in an HTTP body.
    reason: str | None = None


def verify_inbound_signature(body: bytes, signature: str | None) -> bool:
    """Verify the HMAC-SHA256 signature of the inbound webhook body.

    Mirrors :func:`email_intake.verify_signature`: fails closed. Returns
    ``bool(settings.debug)`` when the secret is empty (local-dev convenience) —
    the boot guard in :func:`app.main.lifespan` refuses to start a deployed env
    that enables inbound without the secret, so that carve-out can only fire for
    an explicitly debug-mode developer.
    """
    secret = settings.peppol_inbound_signing_secret
    if not secret:
        return bool(settings.debug)
    if not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


async def receive_peppol_message(
    ctrl_db: AsyncSession,
    *,
    tenant_slug: str,
    message: InboundPeppolMessage,
) -> ReceiveResult:
    """Resolve the tenant, dedupe, validate, and create an Invoice from an
    inbound PEPPOL delivery. The HMAC + metadata parse already ran in the route.

    Every soft-reject returns a :class:`ReceiveResult` with a PII-free ``reason``
    code (the caller logs it and returns 204) — never raises for a rejection.
    """
    result = ReceiveResult(tenant_slug=tenant_slug)

    org = (
        await ctrl_db.execute(select(Organization).where(Organization.slug == tenant_slug))
    ).scalar_one_or_none()
    if org is None:
        result.reason = "unknown_tenant"
        return result

    # Short-lived tenant session — identical shape to email-intake. pool_size=1
    # keeps us under PostgreSQL's connection limit; disposed in `finally`.
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.database import _make_tenant_url
    from app.models.entity import Entity
    from app.services.extraction_dispatch import dispatch_extraction
    from app.services.storage import _ensure_bucket, _get_client

    tenant_engine = create_async_engine(_make_tenant_url(org.db_name), pool_size=1, max_overflow=0)
    tenant_factory = async_sessionmaker(tenant_engine, expire_on_commit=False)

    try:
        async with tenant_factory() as tenant_db:
            # 1. Fast-path dedupe (advisory). The DB unique index below is the
            #    authoritative guarantee for the concurrent race.
            existing = (
                await tenant_db.execute(
                    select(PeppolTransmission.id).where(
                        PeppolTransmission.message_id == message.message_id
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                result.duplicate = True
                result.reason = "duplicate"
                return result

            # 2. Validate the payload structurally. A non-structured or malformed
            #    document is soft-rejected (no invoice). EInvoiceValidationError's
            #    str() is already PII-free; ValueError gets a fixed code.
            try:
                doc = parse_e_invoice(
                    message.payload, mime_type="application/xml", filename="inbound.xml"
                )
            except EInvoiceValidationError as exc:
                # field/code pairs only — no PII, no document values.
                logger.warning("PEPPOL inbound rejected: malformed document (%s)", str(exc))
                result.reason = "malformed_document"
                return result
            except ValueError:
                logger.warning("PEPPOL inbound rejected: not a structured e-invoice")
                result.reason = "malformed_document"
                return result

            # 3. Resolve the tenant's default entity (inbound has no selector).
            entity_id = (
                await tenant_db.execute(select(Entity.id).where(Entity.is_default))
            ).scalar_one_or_none()

            # 4. Create the Invoice (status=new — the einvoice adapter will
            #    auto-approve via dispatch_extraction). Money is Decimal. The
            #    description names only the sender SCHEME, never the PII value.
            amount = doc.payable_amount or doc.tax_inclusive_amount or Decimal("0")
            invoice = Invoice(
                invoice_number=doc.invoice_number or "",
                vendor_name=(doc.seller.name or ""),
                description=f"Received via PEPPOL from {message.sender_scheme}",
                amount=amount,
                currency=doc.currency or "USD",
                status=InvoiceStatus.new,
                organization_id=org.id,
                entity_id=entity_id,
                uploaded_by_id=None,  # system — no human uploader
            )
            tenant_db.add(invoice)
            await tenant_db.flush()  # assign invoice.id
            invoice_id = invoice.id
            correlation_id = invoice.correlation_id

            # 5. Persist the inbound transmission row keyed by message_id. Flush
            #    HERE — BEFORE the S3 upload — to claim the dedupe slot first
            #    (peppol_send orders the same way). A concurrent redelivery that
            #    already claimed the slot raises IntegrityError → rollback the
            #    whole transaction (invoice included) → return duplicate, no S3
            #    write performed by the loser.
            adapter = get_peppol_adapter((org.settings or {}).get("peppol"))
            peppol_cfg = (org.settings or {}).get("peppol") or {}
            transmission = PeppolTransmission(
                invoice_id=invoice_id,
                direction=_DIRECTION_INBOUND,
                # The COUNTERPARTY (the supplier) — PII value, never logged.
                participant_scheme=message.sender_scheme,
                participant_value=message.sender_value,
                # Our own (C4) participant id, if configured.
                sender_scheme=peppol_cfg.get("sender_scheme"),
                sender_value=peppol_cfg.get("sender_value"),
                doc_type_id=message.doc_type_id,
                process_id=message.process_id,
                business_message_id=message.message_id,
                message_id=message.message_id,  # THE unique-index dedupe key
                status="delivered",  # inbound is already delivered to us (C4)
                provider=adapter.provider_name,
                amount=amount,
                currency=doc.currency or "USD",
                raw_response={
                    "direction": _DIRECTION_INBOUND,
                    "doc_type": message.doc_type_id,
                    "process_id": message.process_id,
                    "message_id": message.message_id,
                },
                organization_id=org.id,
                entity_id=entity_id,
            )
            tenant_db.add(transmission)
            try:
                await tenant_db.flush()
            except IntegrityError:
                await tenant_db.rollback()
                result.duplicate = True
                result.reason = "duplicate"
                return result
            transmission_id = transmission.id

            # 6. Upload the raw payload now that the slot is ours. Direct
            #    put_object (not upload_invoice_file, which needs an UploadFile).
            s3 = _get_client()
            _ensure_bucket(s3)
            file_key = f"{org.id}/{invoice_id}/peppol-inbound.xml"
            s3.put_object(
                Bucket=settings.s3_bucket,
                Key=file_key,
                Body=message.payload,
                ContentType="application/xml",
            )
            invoice.file_key = file_key
            invoice.file_url = (
                f"{settings.s3_endpoint_url.rstrip('/')}/{settings.s3_bucket}/{file_key}"
            )

            # 7. Audit — PII-free details only (scheme/message-id/doc-type).
            await dispatch_audit(
                tenant_db,
                correlation_id=correlation_id,
                organization_id=org.id,
                actor_id=SYSTEM_ACTOR_ID,
                action="invoice.peppol_received",
                entity_type="invoice",
                entity_id=invoice_id,
                details={
                    "provider": adapter.provider_name,
                    "sender_scheme": message.sender_scheme,
                    "message_id": message.message_id,
                    "doc_type": message.doc_type_id,
                    "direction": _DIRECTION_INBOUND,
                },
            )

            await tenant_db.commit()

        # 8. Dispatch extraction OUTSIDE the tenant transaction so a dispatch
        #    failure can't roll back the committed invoice + transmission rows
        #    (exactly email-intake's ordering). run_extraction auto-routes the
        #    stored UBL/CII to the `einvoice` adapter — no config change.
        await dispatch_extraction(invoice_id, org.id, SYSTEM_ACTOR_ID)

        result.accepted = True
        result.invoice_id = invoice_id
        result.transmission_id = transmission_id
        return result
    finally:
        await tenant_engine.dispose()
