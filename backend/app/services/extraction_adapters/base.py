"""Base extraction adapter interface and shared types."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExtractedField:
    """A single extracted field with confidence score."""

    value: str | None
    confidence: float = 0.0  # 0-1


@dataclass
class ExtractedLineItem:
    line_number: int = 0
    item_code: ExtractedField = field(default_factory=lambda: ExtractedField(None))
    description: ExtractedField = field(default_factory=lambda: ExtractedField(None))
    quantity: ExtractedField = field(default_factory=lambda: ExtractedField(None))
    unit_price: ExtractedField = field(default_factory=lambda: ExtractedField(None))
    tax: ExtractedField = field(default_factory=lambda: ExtractedField(None))
    total: ExtractedField = field(default_factory=lambda: ExtractedField(None))
    gl_account: ExtractedField = field(default_factory=lambda: ExtractedField(None))


@dataclass
class ExtractionResult:
    """Normalized result from any extraction adapter."""

    success: bool
    overall_confidence: float = 0.0  # 0-1 average across all fields

    # Header fields
    invoice_number: ExtractedField = field(default_factory=lambda: ExtractedField(None))
    vendor_name: ExtractedField = field(default_factory=lambda: ExtractedField(None))
    vendor_address: ExtractedField = field(default_factory=lambda: ExtractedField(None))
    vendor_tax_id: ExtractedField = field(default_factory=lambda: ExtractedField(None))
    amount: ExtractedField = field(default_factory=lambda: ExtractedField(None))
    currency: ExtractedField = field(default_factory=lambda: ExtractedField(None))
    subtotal: ExtractedField = field(default_factory=lambda: ExtractedField(None))
    tax_amount: ExtractedField = field(default_factory=lambda: ExtractedField(None))
    tax_rate: ExtractedField = field(default_factory=lambda: ExtractedField(None))
    discount_amount: ExtractedField = field(default_factory=lambda: ExtractedField(None))
    shipping_amount: ExtractedField = field(default_factory=lambda: ExtractedField(None))
    invoice_date: ExtractedField = field(default_factory=lambda: ExtractedField(None))
    due_date: ExtractedField = field(default_factory=lambda: ExtractedField(None))
    payment_terms: ExtractedField = field(default_factory=lambda: ExtractedField(None))
    po_number: ExtractedField = field(default_factory=lambda: ExtractedField(None))
    description: ExtractedField = field(default_factory=lambda: ExtractedField(None))
    reference_number: ExtractedField = field(default_factory=lambda: ExtractedField(None))
    payment_method: ExtractedField = field(default_factory=lambda: ExtractedField(None))
    bill_to_address: ExtractedField = field(default_factory=lambda: ExtractedField(None))
    remit_to_address: ExtractedField = field(default_factory=lambda: ExtractedField(None))

    # AI-suggested GL coding
    suggested_gl_account: ExtractedField = field(default_factory=lambda: ExtractedField(None))
    suggested_cost_center: ExtractedField = field(default_factory=lambda: ExtractedField(None))

    # Line items
    line_items: list[ExtractedLineItem] = field(default_factory=list)

    # Raw response for debugging
    raw_response: dict | None = None
    provider: str = ""
    error: str | None = None

    def to_flat_dict(self) -> dict:
        """Convert to a flat dict of field_name → value (for backward compat)."""
        result = {}
        for fname in (
            "invoice_number",
            "vendor_name",
            "vendor_address",
            "vendor_tax_id",
            "amount",
            "currency",
            "subtotal",
            "tax_amount",
            "tax_rate",
            "discount_amount",
            "shipping_amount",
            "invoice_date",
            "due_date",
            "payment_terms",
            "po_number",
            "description",
            "reference_number",
            "payment_method",
            "bill_to_address",
            "remit_to_address",
            "suggested_gl_account",
            "suggested_cost_center",
        ):
            f: ExtractedField = getattr(self, fname)
            if f.value is not None:
                result[fname] = f.value
        return result


class ExtractionAdapter:
    """Base class for AI extraction providers."""

    provider_name: str = "base"

    def __init__(self, config: dict):
        self.config = config

    async def extract(
        self,
        file_bytes: bytes = b"",
        file_key: str = "",
        mime_type: str = "application/pdf",
        file_url: str = "",
    ) -> ExtractionResult:
        """Extract invoice data from file bytes.

        Returns structured result with per-field confidence.
        """
        raise NotImplementedError

    async def test_connection(self) -> bool:
        """Verify the provider connection / API key is working."""
        raise NotImplementedError
