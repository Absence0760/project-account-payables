"""Canonical pagination for list endpoints.

Every paginated list endpoint shares ONE contract so the API (and the
frontend's shared Load-More control) behaves identically everywhere:

- query params ``page`` (1-based) and ``page_size``
- ``page_size`` defaults to :data:`DEFAULT_PAGE_SIZE` and is capped at
  :data:`MAX_PAGE_SIZE`
- the response envelope always carries ``items``, ``total``, ``page`` and
  ``page_size``

Use the :func:`pagination_params` dependency for the inputs and either
:class:`PageMeta` (mixed into a typed Pydantic list response) or
:func:`paginated` (for dict-returning handlers) for the envelope. Bounds
live here and nowhere else, so a new endpoint cannot quietly drift onto a
different default or cap.
"""

from dataclasses import dataclass
from typing import Any

from fastapi import Query
from pydantic import BaseModel

# The single source of truth for list-page sizing. The frontend's shared
# Load-More control requests the same DEFAULT_PAGE_SIZE, so a bare
# `GET /api/<list>` and the UI's first page return the same rows.
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


@dataclass(frozen=True)
class PaginationParams:
    """Resolved, validated pagination inputs for a list request."""

    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


def pagination_params(
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
) -> PaginationParams:
    """FastAPI dependency yielding the canonical, bounds-checked params."""
    return PaginationParams(page=page, page_size=page_size)


class PageMeta(BaseModel):
    """Pagination fields every typed list response includes.

    Mix into a Pydantic list response alongside its ``items`` / ``total``
    so the envelope matches the dict-returning handlers byte-for-byte::

        class VendorListResponse(PageMeta):
            items: list[VendorResponse]
            total: int
    """

    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE


def paginated(items: list[Any], total: int, p: PaginationParams) -> dict[str, Any]:
    """Build the canonical envelope for a dict-returning list handler."""
    return {"items": items, "total": total, "page": p.page, "page_size": p.page_size}
