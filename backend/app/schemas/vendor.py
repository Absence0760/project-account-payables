import uuid

from pydantic import BaseModel, Field


class VendorBase(BaseModel):
    name: str = Field(..., max_length=255)
    code: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    address: str | None = Field(default=None, max_length=500)
    payment_terms: str | None = Field(default=None, max_length=100)


class VendorCreate(VendorBase):
    pass


class VendorUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    code: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    payment_terms: str | None = None


class VendorResponse(BaseModel):
    id: str
    name: str
    code: str | None
    email: str | None
    phone: str | None
    address: str | None
    payment_terms: str | None
    created_at: str

    model_config = {"from_attributes": True}

    @classmethod
    def from_db(cls, v) -> "VendorResponse":
        return cls(
            id=str(v.id),
            name=v.name,
            code=v.code,
            email=v.email,
            phone=v.phone,
            address=v.address,
            payment_terms=v.payment_terms,
            created_at=v.created_at.isoformat() if v.created_at else "",
        )
