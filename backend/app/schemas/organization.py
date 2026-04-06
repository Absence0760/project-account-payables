"""Schemas for organization settings."""

from pydantic import BaseModel, Field


class CompanyProfile(BaseModel):
    address: str = ""
    phone: str = ""
    website: str = ""
    tax_id: str = ""
    logo_url: str = ""


class InvoiceDefaults(BaseModel):
    currency: str = "USD"
    payment_terms: str = "Net 30"
    number_prefix: str = "INV-"
    default_gl_account: str = ""
    default_cost_center: str = ""


class OrganizationSettings(BaseModel):
    company: CompanyProfile = Field(default_factory=CompanyProfile)
    invoice_defaults: InvoiceDefaults = Field(default_factory=InvoiceDefaults)


class OrganizationResponse(BaseModel):
    id: str
    name: str
    slug: str
    plan: str
    settings: OrganizationSettings
    created_at: str


class UpdateOrganizationRequest(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    settings: OrganizationSettings | None = None
