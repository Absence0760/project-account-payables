"""AI extraction adapter package — unified interface for invoice data extraction."""

from app.services.extraction_adapters.base import (
    ExtractionAdapter,
    ExtractionResult,
    ExtractedField,
)
from app.services.extraction_adapters.dispatcher import get_extraction_adapter

__all__ = [
    "ExtractionAdapter",
    "ExtractionResult",
    "ExtractedField",
    "get_extraction_adapter",
]
