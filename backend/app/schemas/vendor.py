from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.vendor_consolidation import mask_tax_id
from app.utils.banking import validate_aba_routing


def _is_masked_tax_id(value) -> bool:
    """True when a caller echoed back the ``***<last4>`` masked value we return
    in responses. Real tax ids are digits/separators and never start ``***``."""
    return isinstance(value, str) and value.startswith("***")


class VendorBankDetails(BaseModel):
    """Subset of `Vendor.bank_details` JSONB the UI is allowed to set.

    The processor's counterparty / external ID is the bridge to live
    payment rails — Modern Treasury and friends key payment-orders off
    that. Last4s of routing + account number are stored alongside for
    display, never the full account numbers (those live with the
    processor).

    International fields:
      - `iban_last4`: the last 4 chars of the IBAN, displayed in the UI.
        The FULL IBAN lives in `Vendor.bank_details` JSONB and is read
        only by the payment orchestrator — it is never returned to the
        browser. A full IBAN is effectively an account identifier in
        SEPA-zone countries.
      - `swift_bic`: the bank's SWIFT/BIC code. Public bank routing
        information (same trust level as a US ABA routing number),
        safe to surface in the UI.
      - `country`: ISO 3166-1 alpha-2 country code for the destination
        bank; used by the corridor selector."""

    counterparty_id: str | None = Field(default=None, max_length=255)
    account_last4: str | None = Field(default=None, max_length=4)
    routing_last4: str | None = Field(default=None, max_length=4)
    bank_name: str | None = Field(default=None, max_length=255)
    iban_last4: str | None = Field(default=None, max_length=4)
    swift_bic: str | None = Field(default=None, max_length=11)
    country: str | None = Field(default=None, max_length=2)


class VendorBase(BaseModel):
    name: str = Field(..., max_length=255)
    code: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    address: str | None = Field(default=None, max_length=500)
    tax_id: str | None = Field(default=None, max_length=50)
    payment_terms: str | None = Field(default=None, max_length=100)
    accepts_virtual_cards: bool = False
    bank_details: VendorBankDetails | None = None

    @model_validator(mode="after")
    def _guard_masked_tax_id(self):
        # `tax_id` is returned masked (`***<last4>`) in VendorResponse. A UI that
        # round-trips the vendor on save echoes that masked value back — never
        # persist the mask over the stored raw tax id. Null it and drop it from
        # the write set so `model_dump(exclude_unset=True)` skips it on update
        # (leaving the stored value unchanged) and a create stores nothing.
        if _is_masked_tax_id(self.tax_id):
            self.tax_id = None
            self.__pydantic_fields_set__.discard("tax_id")
        return self


class VendorCreate(VendorBase):
    pass


class VendorUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    code: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    tax_id: str | None = None
    payment_terms: str | None = None
    accepts_virtual_cards: bool | None = None
    status: str | None = None
    bank_details: VendorBankDetails | None = None

    @model_validator(mode="after")
    def _guard_masked_tax_id(self):
        # See VendorBase._guard_masked_tax_id — same round-trip protection on edit.
        if _is_masked_tax_id(self.tax_id):
            self.tax_id = None
            self.__pydantic_fields_set__.discard("tax_id")
        return self


class VendorResponse(BaseModel):
    id: str
    name: str
    code: str | None
    email: str | None
    phone: str | None
    address: str | None
    website: str | None = None
    tax_id: str | None
    payment_terms: str | None
    accepts_virtual_cards: bool
    status: str
    source: str
    verified_by: str | None
    verified_at: str | None
    erp_vendor_id: str | None
    erp_synced_at: str | None
    invoice_count: int = 0
    created_at: str
    bank_details: VendorBankDetails | None = None

    # Sanctions & risk screening (migration 0042). Denormalised current
    # state; the full trail is GET /api/vendors/{id}/screening-history.
    screening_status: str = "unscreened"
    last_screened_at: str | None = None
    payments_blocked: bool = False
    payments_blocked_reason: str | None = None
    risk_score: str | None = None
    risk_level: str = "unknown"

    model_config = {"from_attributes": True}

    @classmethod
    def from_db(cls, v, invoice_count: int = 0) -> "VendorResponse":
        bank_details = None
        if v.bank_details:
            # Pydantic strips unknown keys via VendorBankDetails — keeps
            # the legacy JSONB shape (which historically held arbitrary
            # processor metadata) from leaking out to the UI.
            full_iban = (v.bank_details.get("iban") or "").strip()
            iban_last4 = full_iban[-4:] if len(full_iban) >= 4 else None
            bank_details = VendorBankDetails(
                counterparty_id=v.bank_details.get("counterparty_id"),
                account_last4=v.bank_details.get("account_last4"),
                routing_last4=v.bank_details.get("routing_last4"),
                bank_name=v.bank_details.get("bank_name"),
                iban_last4=iban_last4,
                swift_bic=v.bank_details.get("swift_bic"),
                country=v.bank_details.get("country"),
            )
        return cls(
            id=str(v.id),
            name=v.name,
            code=v.code,
            email=v.email,
            phone=v.phone,
            address=v.address,
            website=getattr(v, "website", None),
            # PII: never return the raw tax id — mask to `***<last4>`.
            tax_id=mask_tax_id(v.tax_id),
            payment_terms=v.payment_terms,
            accepts_virtual_cards=v.accepts_virtual_cards,
            status=v.status,
            source=v.source,
            verified_by=v.verified_by,
            verified_at=v.verified_at.isoformat() if v.verified_at else None,
            erp_vendor_id=v.erp_vendor_id,
            erp_synced_at=v.erp_synced_at.isoformat() if v.erp_synced_at else None,
            invoice_count=invoice_count,
            created_at=v.created_at.isoformat() if v.created_at else "",
            bank_details=bank_details,
            screening_status=getattr(v, "screening_status", "unscreened"),
            last_screened_at=(
                v.last_screened_at.isoformat() if getattr(v, "last_screened_at", None) else None
            ),
            payments_blocked=bool(getattr(v, "payments_blocked", False)),
            payments_blocked_reason=getattr(v, "payments_blocked_reason", None),
            risk_score=(str(v.risk_score) if getattr(v, "risk_score", None) is not None else None),
            risk_level=getattr(v, "risk_level", "unknown"),
        )


def _mask_change_value(change_type: str, proposed_value: dict) -> dict:
    """Mask the sensitive parts of a staged change for list/summary views.

    Bank account numbers and the full tax ID never appear in a list payload
    — only enough to identify the change (a last-4). The full value is
    revealed only on the dedicated detail/approve path so AP can verify it.
    """
    if change_type == "tax_id":
        tax_id = str(proposed_value.get("tax_id") or "")
        return {"tax_id_last4": tax_id[-4:] if len(tax_id) >= 4 else None}
    if change_type == "bank_details":
        bank = proposed_value.get("bank_details") or {}
        account = str(bank.get("account_number") or "")
        return {
            "bank_name": bank.get("bank_name"),
            "account_last4": account[-4:] if len(account) >= 4 else bank.get("account_last4"),
        }
    return {}


class VendorChangeRequestResponse(BaseModel):
    """Admin-side view of a staged vendor change request.

    `proposed_value` is masked by default (`reveal=False`); the full
    banking / tax value is included only when an admin explicitly opens
    the detail or is about to apply the change. The route is RBAC-gated.
    """

    id: str
    vendor_id: str
    vendor_name: str | None = None
    change_type: str
    status: str
    proposed_value: dict
    # Exactly one requester is set: the portal VendorUser, or the AP User.
    requested_by_vendor_user_id: str | None = None
    requested_by_user_id: str | None = None
    reviewed_by_user_id: str | None = None
    reviewed_at: str | None = None
    review_note: str | None = None
    created_at: str

    @classmethod
    def from_db(
        cls, r, *, vendor_name: str | None = None, reveal: bool = False
    ) -> "VendorChangeRequestResponse":
        proposed = (
            r.proposed_value if reveal else _mask_change_value(r.change_type, r.proposed_value)
        )
        return cls(
            id=str(r.id),
            vendor_id=str(r.vendor_id),
            vendor_name=vendor_name,
            change_type=r.change_type,
            status=r.status,
            proposed_value=proposed,
            requested_by_vendor_user_id=(
                str(r.requested_by_vendor_user_id) if r.requested_by_vendor_user_id else None
            ),
            requested_by_user_id=(
                str(r.requested_by_user_id) if getattr(r, "requested_by_user_id", None) else None
            ),
            reviewed_by_user_id=str(r.reviewed_by_user_id) if r.reviewed_by_user_id else None,
            reviewed_at=r.reviewed_at.isoformat() if r.reviewed_at else None,
            review_note=r.review_note,
            created_at=r.created_at.isoformat() if r.created_at else "",
        )


class VendorChangeReviewRequest(BaseModel):
    review_note: str | None = Field(default=None, max_length=1000)


class VendorBankChangeRequest(BaseModel):
    """AP-initiated bank-details change. Staged for dual-control approval rather
    than applied (the BEC / bank-redirect gate). `bank_details` mirrors the
    partial accepted by the vendor PATCH (counterparty_id, *_last4, bank_name,
    and optionally a full account/routing/iban)."""

    bank_details: dict

    @field_validator("bank_details")
    @classmethod
    def _validate_routing_number(cls, v: dict) -> dict:
        # Structural check only, and only when a US routing number is present
        # at all — an international vendor's staged change carries `iban`/
        # `swift_bic` instead. Unlike IBAN/SWIFT (checked at payment time in
        # `services/international_payments.py`), nothing validated a routing
        # number anywhere, so a fat-fingered digit surfaced only as a returned
        # ACH days later. Reject it at the point it's first written instead.
        routing = v.get("routing_number")
        if routing and not validate_aba_routing(routing):
            raise ValueError("routing_number is not a valid 9-digit ABA routing number")
        return v
