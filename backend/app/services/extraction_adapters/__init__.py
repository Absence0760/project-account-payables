"""AI extraction adapter package — unified interface for invoice data extraction."""

from app.services.extraction_adapters.base import (
    ExtractedField,
    ExtractionAdapter,
    ExtractionResult,
)
from app.services.extraction_adapters.dispatcher import (
    UnknownExtractionProviderError,
    get_extraction_adapter,
    list_available_providers,
)

__all__ = [
    "ExtractionAdapter",
    "ExtractionResult",
    "ExtractedField",
    "UnknownExtractionProviderError",
    "get_extraction_adapter",
    "list_available_providers",
]
