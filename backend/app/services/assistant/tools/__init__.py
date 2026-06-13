"""Fixed assistant toolset — registry, Anthropic specs, dispatch.

Five typed read-only tools over the **current tenant** only. Each ``ToolSpec``
binds a name to its param/return Pydantic models, the async tool fn, and the
Anthropic tool schema (auto-derived from the param model's JSON schema). The
orchestrator runs the fn; the model can only emit one of these five calls with
typed, clamped params — never raw SQL.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.services.assistant.tools import schemas
from app.services.assistant.tools.approvals import list_pending_approvals
from app.services.assistant.tools.forecast import get_payment_forecast
from app.services.assistant.tools.invoices import list_invoices
from app.services.assistant.tools.text_search import find_invoices_by_text
from app.services.assistant.tools.vendor_spend import get_vendor_spend


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    param_model: type[BaseModel]
    return_model: type[BaseModel]
    fn: Callable[..., Awaitable[BaseModel]]

    @property
    def anthropic_spec(self) -> dict[str, Any]:
        """The Anthropic tool schema: ``{name, description, input_schema}``.

        Derived from the param model's JSON schema, trimmed to the
        ``input_schema`` shape the Messages API expects.
        """
        schema = self.param_model.model_json_schema()
        input_schema: dict[str, Any] = {
            "type": "object",
            "properties": schema.get("properties", {}),
        }
        if "required" in schema:
            input_schema["required"] = schema["required"]
        if "$defs" in schema:
            input_schema["$defs"] = schema["$defs"]
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": input_schema,
        }


TOOLS: dict[str, ToolSpec] = {
    "list_invoices": ToolSpec(
        name="list_invoices",
        description=(
            "List invoices in the current tenant, optionally filtered by status, "
            "vendor name, invoice-date range, and amount range. Returns a "
            "paginated summary (id, number, vendor, amount, currency, status, "
            "dates). Use for 'show me invoices …', status/amount/vendor queries."
        ),
        param_model=schemas.ListInvoicesParams,
        return_model=schemas.InvoiceListResult,
        fn=list_invoices,
    ),
    "get_vendor_spend": ToolSpec(
        name="get_vendor_spend",
        description=(
            "Top-N vendors by committed/paid spend over a period (mtd, qtd, ytd, "
            "last_30d, last_90d, last_12m), in the org's reporting currency, with "
            "each vendor's share of total. Use for 'top vendors', 'who do we spend "
            "the most with', vendor-concentration questions."
        ),
        param_model=schemas.VendorSpendParams,
        return_model=schemas.VendorSpendResult,
        fn=get_vendor_spend,
    ),
    "list_pending_approvals": ToolSpec(
        name="list_pending_approvals",
        description=(
            "Invoices awaiting approval. assignee='me' (default) returns the "
            "caller's own approval queue; assignee='anyone' returns the whole "
            "queue. Use for 'what's awaiting my approval', 'my queue', 'pending "
            "approvals'."
        ),
        param_model=schemas.PendingApprovalsParams,
        return_model=schemas.PendingApprovalsResult,
        fn=list_pending_approvals,
    ),
    "get_payment_forecast": ToolSpec(
        name="get_payment_forecast",
        description=(
            "Projected AP cash outflow over a horizon (7d/14d/30d/60d/90d), "
            "bucketed by day/week/month, with per-bucket totals and a grand "
            "total. Use for 'cash due', 'upcoming payments', 'what do we owe', "
            "payment-forecast questions."
        ),
        param_model=schemas.ForecastParams,
        return_model=schemas.ForecastResult,
        fn=get_payment_forecast,
    ),
    "find_invoices_by_text": ToolSpec(
        name="find_invoices_by_text",
        description=(
            "Semantic / similarity search over past invoices by free-text query. "
            "Returns the most similar invoices with a similarity score and a "
            "non-sensitive snippet. Use for 'find invoices like …', 'search for "
            "invoices about …', fuzzy lookups."
        ),
        param_model=schemas.TextSearchParams,
        return_model=schemas.TextSearchResult,
        fn=find_invoices_by_text,
    ),
}

# The JSON tool schemas handed to the claude adapter.
TOOL_SPECS: list[dict[str, Any]] = [t.anthropic_spec for t in TOOLS.values()]
