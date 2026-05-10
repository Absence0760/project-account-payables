from pydantic import BaseModel, Field


class VendorBankDetails(BaseModel):
    """Subset of `Vendor.bank_details` JSONB the UI is allowed to set.

    The processor's counterparty / external ID is the bridge to live
    payment rails — Modern Treasury and friends key payment-orders off
    that. Last4s of routing + account number are stored alongside for
    display, never the full account numbers (those live with the
    processor)."""

    counterparty_id: str | None = Field(default=None, max_length=255)
    account_last4: str | None = Field(default=None, max_length=4)
    routing_last4: str | None = Field(default=None, max_length=4)
    bank_name: str | None = Field(default=None, max_length=255)


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


class VendorResponse(BaseModel):
    id: str
    name: str
    code: str | None
    email: str | None
    phone: str | None
    address: str | None
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

    model_config = {"from_attributes": True}

    @classmethod
    def from_db(cls, v, invoice_count: int = 0) -> "VendorResponse":
        bank_details = None
        if v.bank_details:
            # Pydantic strips unknown keys via VendorBankDetails — keeps
            # the legacy JSONB shape (which historically held arbitrary
            # processor metadata) from leaking out to the UI.
            bank_details = VendorBankDetails(
                counterparty_id=v.bank_details.get("counterparty_id"),
                account_last4=v.bank_details.get("account_last4"),
                routing_last4=v.bank_details.get("routing_last4"),
                bank_name=v.bank_details.get("bank_name"),
            )
        return cls(
            id=str(v.id),
            name=v.name,
            code=v.code,
            email=v.email,
            phone=v.phone,
            address=v.address,
            tax_id=v.tax_id,
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
        )
