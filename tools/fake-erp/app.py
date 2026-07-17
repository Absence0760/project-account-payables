"""fake-erp — a tiny deterministic HTTP fake of the three real ERP providers.

Emulates just enough of each provider's API surface to satisfy the backend's
real ERP adapters (backend/app/services/erp_adapters/):

    /merge/api/accounting/v1/...            Merge.dev unified accounting API
    /netsuite/services/rest/record/v1/...   NetSuite SuiteTalk REST (TBA)
    /d365/...                               Dynamics 365 Business Central OData

State is in-memory only (module-level dicts), fully deterministic, and
resettable via POST /__reset. No external deps beyond fastapi + uvicorn.
"""

from __future__ import annotations

import copy
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse

app = FastAPI(title="fake-erp", docs_url=None, redoc_url=None)

D365_TOKEN = "fake-d365-token"

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------


def _fresh_state() -> dict[str, Any]:
    return {
        "merge_invoices": {},  # id -> record dict (top-level shape, incl. "status")
        "netsuite_bills": {},  # id -> record dict ({"status": {"refName": ...}, ...})
        "d365_invoices": {},  # id -> record dict ({"status": "Draft"/"Open", ...})
        "counters": {"merge": 0, "netsuite": 1000, "d365": 0},
    }


STATE: dict[str, Any] = _fresh_state()


class ProviderError(Exception):
    """Raise anywhere to return a provider-shaped JSON error body."""

    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self.body = body


@app.exception_handler(ProviderError)
async def _provider_error_handler(_request: Request, exc: ProviderError) -> JSONResponse:
    return JSONResponse(exc.body, status_code=exc.status_code)


# ---------------------------------------------------------------------------
# Ops endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/__reset")
async def reset() -> dict:
    global STATE
    STATE = _fresh_state()
    return {"status": "reset"}


@app.post("/__set-status")
async def set_status(body: dict) -> dict:
    """Test hook: force a stored invoice into a given provider-native status.

    Body: {"provider": "merge"|"netsuite"|"d365", "id": "...", "status": "..."}
    e.g. merge "PAID", netsuite "paidInFull", d365 "Paid" — so an e2e test can
    drive get_invoice_status() transitions without a real ERP.
    """
    provider = body.get("provider")
    doc_id = str(body.get("id", ""))
    status = body.get("status")
    if not provider or not doc_id or not status:
        raise ProviderError(400, {"detail": "provider, id and status are required"})
    stores = {
        "merge": STATE["merge_invoices"],
        "netsuite": STATE["netsuite_bills"],
        "d365": STATE["d365_invoices"],
    }
    store = stores.get(provider)
    if store is None or doc_id not in store:
        raise ProviderError(404, {"detail": "unknown provider or id"})
    if provider == "netsuite":
        store[doc_id]["status"] = {"id": status.lower(), "refName": status}
    else:
        store[doc_id]["status"] = status
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Merge.dev unified accounting API  (/merge/api/accounting/v1)
# ---------------------------------------------------------------------------

merge = APIRouter(prefix="/merge/api/accounting/v1")

# FIXED fixtures — e2e tests assert these literals. Do not change them.
MERGE_PO_FIXTURES: list[dict] = [
    {
        "id": "merge-po-301",
        "remote_id": "fake-remote-po-301",
        "number": "PO-FAKE-301",
        "status": "OPEN",
        "vendor": {"id": "merge-vendor-a", "name": "Fake ERP Vendor A"},
        "total_amount": 1250.00,
        "currency": "USD",
        "issue_date": "2026-02-10",
        "delivery_date": "2026-02-24",
        "line_items": [
            {
                "description": "Fake widgets",
                "quantity": 10,
                "unit_price": 100.00,
                "total_line_amount": 1000.00,
                "account": "6100",
            },
            {
                "description": "Fake widget installation",
                "quantity": 1,
                "unit_price": 250.00,
                "total_line_amount": 250.00,
                "account": "6300",
            },
        ],
    },
    {
        "id": "merge-po-302",
        "remote_id": "fake-remote-po-302",
        "number": "PO-FAKE-302",
        "status": "OPEN",
        "vendor": {"id": "merge-vendor-b", "name": "Fake ERP Vendor B"},
        "total_amount": 980.50,
        "currency": "USD",
        "issue_date": "2026-03-05",
        "delivery_date": "2026-03-19",
        "line_items": [
            {
                "description": "Fake software licences",
                "quantity": 2,
                "unit_price": 400.00,
                "total_line_amount": 800.00,
                "account": "6200",
            },
            {
                "description": "Fake support hours",
                "quantity": 1,
                "unit_price": 180.50,
                "total_line_amount": 180.50,
                "account": "6300",
            },
        ],
    },
    {
        "id": "merge-po-303",
        "remote_id": "fake-remote-po-303",
        "number": "PO-FAKE-303",
        "status": "OPEN",
        "vendor": {"id": "merge-vendor-a", "name": "Fake ERP Vendor A"},
        "total_amount": 4400.00,
        "currency": "USD",
        "issue_date": "2026-04-01",
        "delivery_date": "2026-04-15",
        "line_items": [
            {
                "description": "Fake consulting retainer",
                "quantity": 4,
                "unit_price": 1000.00,
                "total_line_amount": 4000.00,
                "account": "6300",
            },
            {
                "description": "Fake consultant travel",
                "quantity": 1,
                "unit_price": 400.00,
                "total_line_amount": 400.00,
                "account": "6300",
            },
        ],
    },
]

MERGE_ACCOUNT_FIXTURES: list[dict] = [
    {
        "id": "merge-acct-6100",
        "remote_id": "fake-remote-acct-6100",
        "account_number": "6100",
        "name": "Fake Office Supplies",
        "classification": "EXPENSE",
        "type": "Expense",
        "status": "ACTIVE",
        "currency": "USD",
        "parent_account": None,
    },
    {
        "id": "merge-acct-6200",
        "remote_id": "fake-remote-acct-6200",
        "account_number": "6200",
        "name": "Fake Software",
        "classification": "EXPENSE",
        "type": "Expense",
        "status": "ACTIVE",
        "currency": "USD",
        "parent_account": None,
    },
    {
        "id": "merge-acct-6300",
        "remote_id": "fake-remote-acct-6300",
        "account_number": "6300",
        "name": "Fake Consulting",
        "classification": "EXPENSE",
        "type": "Expense",
        "status": "ACTIVE",
        "currency": "USD",
        "parent_account": None,
    },
]

MERGE_401 = {"detail": "Authentication credentials were not provided."}


def _require_merge_auth(request: Request) -> None:
    auth = request.headers.get("authorization", "")
    account_token = request.headers.get("x-account-token", "")
    if not auth.startswith("Bearer ") or not auth[len("Bearer ") :].strip():
        raise ProviderError(401, MERGE_401)
    if not account_token.strip():
        raise ProviderError(401, MERGE_401)


def _merge_paginate(items: list[dict], cursor: str | None, marker: str) -> dict:
    """Cursor pagination: page 1 = first 2 results + next cursor; page 2 = the rest."""
    if cursor is None:
        page = items[:2]
        next_cursor = marker if len(items) > 2 else None
    elif cursor == marker:
        page = items[2:]
        next_cursor = None
    else:
        page = []
        next_cursor = None
    return {"next": next_cursor, "previous": None, "results": copy.deepcopy(page)}


@merge.get("/account-details")
async def merge_account_details(request: Request) -> dict:
    _require_merge_auth(request)
    return {
        "id": "fake-merge-account",
        "integration": "Fake ERP",
        "integration_slug": "fake-erp",
        "category": "accounting",
        "end_user_organization_name": "Fake ERP Local Dev",
        "status": "COMPLETE",
    }


@merge.post("/invoices")
async def merge_create_invoice(request: Request) -> JSONResponse:
    _require_merge_auth(request)
    body = await request.json()
    model = body.get("model")
    if not isinstance(model, dict):
        raise ProviderError(400, {"model": ["This field is required."]})
    STATE["counters"]["merge"] += 1
    n = STATE["counters"]["merge"]
    record = {
        "id": f"merge-inv-{n}",
        "remote_id": f"fake-remote-inv-{n}",
        "type": model.get("type", "ACCOUNTS_PAYABLE"),
        "number": model.get("number"),
        "status": "OPEN",
        "issue_date": model.get("issue_date"),
        "due_date": model.get("due_date"),
        "currency": model.get("currency"),
        "total_amount": model.get("total_amount"),
        "sub_total": model.get("sub_total"),
        "total_tax_amount": model.get("total_tax_amount"),
        "total_discount": model.get("total_discount"),
        "memo": model.get("memo"),
        "purchase_order_number": model.get("purchase_order_number"),
        "line_items": model.get("line_items", []),
    }
    STATE["merge_invoices"][record["id"]] = record
    return JSONResponse({"model": copy.deepcopy(record)}, status_code=201)


@merge.get("/invoices/{invoice_id}")
async def merge_get_invoice(request: Request, invoice_id: str) -> dict:
    _require_merge_auth(request)
    record = STATE["merge_invoices"].get(invoice_id)
    if record is None:
        raise ProviderError(404, {"detail": "Not found."})
    return copy.deepcopy(record)


@merge.get("/purchase-orders")
async def merge_list_pos(request: Request, cursor: str | None = None) -> dict:
    _require_merge_auth(request)
    return _merge_paginate(MERGE_PO_FIXTURES, cursor, "po-cursor-page-2")


@merge.get("/accounts")
async def merge_list_accounts(request: Request, cursor: str | None = None) -> dict:
    _require_merge_auth(request)
    return _merge_paginate(MERGE_ACCOUNT_FIXTURES, cursor, "account-cursor-page-2")


app.include_router(merge)

# ---------------------------------------------------------------------------
# NetSuite SuiteTalk REST  (/netsuite/services/rest/record/v1)
# ---------------------------------------------------------------------------

netsuite = APIRouter(prefix="/netsuite/services/rest/record/v1")


def _netsuite_error(status: int, code: str, detail: str) -> ProviderError:
    return ProviderError(
        status,
        {
            "type": "https://www.rfc-editor.org/rfc/rfc9110.html#section-15.5.2",
            "title": "Unauthorized" if status == 401 else "Error",
            "status": status,
            "o:errorDetails": [{"detail": detail, "o:errorCode": code}],
        },
    )


def _require_netsuite_auth(request: Request) -> None:
    """Loose OAuth 1.0 TBA check: header shape + required params only.

    Does NOT verify the HMAC signature — presence of oauth_consumer_key,
    oauth_token and oauth_signature in an `Authorization: OAuth ...` header
    is enough for the fake.
    """
    auth = request.headers.get("authorization", "")
    if not auth.startswith("OAuth"):
        raise _netsuite_error(401, "INVALID_LOGIN", "Invalid login attempt.")
    for param in ("oauth_consumer_key", "oauth_token", "oauth_signature"):
        if f"{param}=" not in auth:
            raise _netsuite_error(401, "INVALID_LOGIN", "Invalid login attempt.")


@netsuite.get("/vendor")
async def netsuite_list_vendors(request: Request, limit: int = 1000) -> dict:
    _require_netsuite_auth(request)
    items = [
        {"links": [], "id": "25", "entityId": "Fake ERP Vendor A"},
        {"links": [], "id": "26", "entityId": "Fake ERP Vendor B"},
    ][: max(limit, 0)]
    return {
        "links": [],
        "count": len(items),
        "hasMore": False,
        "items": items,
        "offset": 0,
        "totalResults": 2,
    }


@netsuite.post("/vendorBill")
async def netsuite_create_vendor_bill(request: Request) -> Response:
    _require_netsuite_auth(request)
    body = await request.json()
    STATE["counters"]["netsuite"] += 1
    doc_id = str(STATE["counters"]["netsuite"])  # numeric-string ids, "1001", "1002", ...
    STATE["netsuite_bills"][doc_id] = {
        "id": doc_id,
        "tranId": body.get("tranId"),
        "tranDate": body.get("tranDate"),
        "dueDate": body.get("dueDate"),
        "memo": body.get("memo"),
        "externalId": body.get("externalId"),
        "currency": body.get("currency"),
        "item": body.get("item"),
        "status": {"id": "open", "refName": "Open"},
    }
    # Real NetSuite responds 204 No Content with the new record URL in Location.
    return Response(
        status_code=204,
        headers={"Location": f"{request.url}/{doc_id}"},
    )


@netsuite.get("/vendorBill/{doc_id}")
async def netsuite_get_vendor_bill(request: Request, doc_id: str) -> dict:
    _require_netsuite_auth(request)
    record = STATE["netsuite_bills"].get(doc_id)
    if record is None:
        raise _netsuite_error(404, "NONEXISTENT_ID", f"That record does not exist. id: {doc_id}")
    return copy.deepcopy(record)


app.include_router(netsuite)

# ---------------------------------------------------------------------------
# Dynamics 365 Business Central OData  (/d365)
# ---------------------------------------------------------------------------

d365 = APIRouter(prefix="/d365")


def _d365_error(status: int, code: str, message: str) -> ProviderError:
    return ProviderError(status, {"error": {"code": code, "message": message}})


def _require_d365_auth(request: Request) -> None:
    if request.headers.get("authorization", "") != f"Bearer {D365_TOKEN}":
        raise _d365_error(
            401, "Authentication_InvalidCredentials", "The server has rejected the client credentials."
        )


async def _d365_token(request: Request) -> dict:
    raw = (await request.body()).decode("utf-8", errors="replace")
    form = {k: v[0] for k, v in parse_qs(raw).items()}
    if (
        form.get("grant_type") != "client_credentials"
        or not form.get("client_id", "").strip()
        or not form.get("client_secret", "").strip()
    ):
        raise ProviderError(
            400,
            {
                "error": "invalid_client",
                "error_description": (
                    "AADSTS7000215: client_credentials grant with a non-empty "
                    "client_id and client_secret is required."
                ),
            },
        )
    return {"access_token": D365_TOKEN, "token_type": "Bearer", "expires_in": 3600}


# The adapter's token URL is env-overridable; accept the documented endpoint
# plus the AAD-shaped variants so any reasonable override value works.
@d365.post("/oauth2/token")
async def d365_token(request: Request) -> dict:
    return await _d365_token(request)


@d365.post("/oauth2/v2.0/token")
async def d365_token_v2(request: Request) -> dict:
    return await _d365_token(request)


@d365.post("/{tenant_id}/oauth2/v2.0/token")
async def d365_token_tenant(request: Request, tenant_id: str) -> dict:
    return await _d365_token(request)


@d365.get("/{environment}/api/v2.0/companies({company_id})/vendors")
async def d365_list_vendors(request: Request, environment: str, company_id: str) -> dict:
    _require_d365_auth(request)
    return {
        "value": [
            {"id": "d365-vendor-1", "number": "V0001", "displayName": "Fake ERP Vendor A"},
        ]
    }


@d365.post("/{environment}/api/v2.0/companies({company_id})/purchaseInvoices")
async def d365_create_purchase_invoice(
    request: Request, environment: str, company_id: str
) -> JSONResponse:
    _require_d365_auth(request)
    body = await request.json()
    STATE["counters"]["d365"] += 1
    n = STATE["counters"]["d365"]
    record = {
        "id": f"d365-inv-{n}",
        "number": f"PI-{100000 + n}",
        "status": "Draft",
        "vendorNumber": body.get("vendorNumber"),
        "vendorInvoiceNumber": body.get("vendorInvoiceNumber"),
        "externalDocumentNumber": body.get("externalDocumentNumber"),
        "invoiceDate": body.get("invoiceDate"),
        "dueDate": body.get("dueDate"),
        "currencyCode": body.get("currencyCode"),
        "purchaseInvoiceLines": body.get("purchaseInvoiceLines", []),
    }
    STATE["d365_invoices"][record["id"]] = record
    return JSONResponse(copy.deepcopy(record), status_code=201)


@d365.post("/{environment}/api/v2.0/companies({company_id})/purchaseInvoices({doc_id})/Microsoft.NAV.post")
async def d365_post_purchase_invoice(
    request: Request, environment: str, company_id: str, doc_id: str
) -> Response:
    _require_d365_auth(request)
    record = STATE["d365_invoices"].get(doc_id)
    if record is None:
        raise _d365_error(404, "BadRequest_NotFound", f"No purchaseInvoice with id {doc_id}.")
    record["status"] = "Open"  # posted/finalized → Open (unpaid)
    return Response(status_code=204)


@d365.get("/{environment}/api/v2.0/companies({company_id})/purchaseInvoices({doc_id})")
async def d365_get_purchase_invoice(
    request: Request, environment: str, company_id: str, doc_id: str
) -> dict:
    _require_d365_auth(request)
    record = STATE["d365_invoices"].get(doc_id)
    if record is None:
        raise _d365_error(404, "BadRequest_NotFound", f"No purchaseInvoice with id {doc_id}.")
    return copy.deepcopy(record)


app.include_router(d365)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
