"""Every PDF render is offloaded off the event loop, and stays that way.

The four branded PDF renderers (`analytics_report_pdf`, `audit_report_pdf`,
`remittance_pdf`, `tax_1099_forms`) are ordinary **sync** functions, and each
one is reached only from an `async def` route handler. Two things inside them
block for a long time:

* `branding.build_logo_flowable` → `fetch_logo_bytes` does a *blocking* DNS
  lookup (the SSRF guard) plus a *blocking* `httpx.Client` GET of the tenant's
  logo, bounded at `LOGO_FETCH_TIMEOUT_SECONDS`; and
* ReportLab lays the document out on the CPU, which for a multi-page landscape
  table is not free.

Called straight from the coroutine, that whole cost is charged to the event
loop and every other in-flight request on the worker waits behind it. So each
call site wraps the renderer in `asyncio.to_thread`. This module is the drift
guard on that: a new export route (or a refactor) that calls a renderer
directly fails here instead of quietly reintroducing the stall.
"""

from __future__ import annotations

import ast
import pathlib
from unittest.mock import patch

import pytest

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"

# The sync PDF renderers. Anything here must be reached via asyncio.to_thread
# from a coroutine.
RENDERERS = {
    "render_analytics_report_pdf",
    "render_audit_report_pdf",
    "render_remittance_pdf",
    "render_1099_pdf",
}


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _offloaded_renderers(tree: ast.AST) -> set[int]:
    """ids() of the renderer Name nodes passed to `asyncio.to_thread`."""
    ok: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node) != "to_thread" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Name) and first.id in RENDERERS:
            ok.add(id(first))
    return ok


def test_no_pdf_renderer_is_called_directly_from_a_coroutine():
    offenders: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        source = path.read_text()
        if not any(name in source for name in RENDERERS):
            continue
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            if name in RENDERERS:
                offenders.append(f"{path.relative_to(APP_DIR.parent)}:{node.lineno} {name}(...)")

    assert offenders == [], (
        "PDF renderers must be offloaded with `await asyncio.to_thread(render_x, ctx)` — "
        "calling one directly blocks the event loop for the logo fetch + layout: "
        + ", ".join(offenders)
    )


def test_every_renderer_reference_in_the_api_layer_is_a_to_thread_argument():
    """The positive half: each renderer name that appears as a bare reference in
    a route module is there as `asyncio.to_thread`'s first argument."""
    seen = 0
    for path in sorted((APP_DIR / "api").rglob("*.py")):
        source = path.read_text()
        if not any(name in source for name in RENDERERS):
            continue
        tree = ast.parse(source)
        offloaded = _offloaded_renderers(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Name) or node.id not in RENDERERS:
                continue
            if not isinstance(node.ctx, ast.Load):
                continue
            # Import aliases are ast.alias, not ast.Name, so anything reaching
            # here is a real use of the callable.
            assert id(node) in offloaded, (
                f"{path.relative_to(APP_DIR.parent)}:{node.lineno} uses {node.id} "
                "outside `asyncio.to_thread`"
            )
            seen += 1

    # Guard the guard: if the renderers move or get renamed, this test must not
    # silently pass by scanning nothing.
    assert seen >= 6, f"expected every PDF export route to be covered, found {seen}"


def test_logo_embed_really_is_blocking_network_io():
    """Why the offload exists: the "pure, DB-free" renderer fetches over HTTP.

    `fetch_logo_bytes` opens a synchronous `httpx.Client` against the tenant's
    admin-set logo URL. On the event loop that is a stall of up to
    `LOGO_FETCH_TIMEOUT_SECONDS` per export, plus the DNS lookup in front of it.
    """
    from app.services import branding

    with patch("httpx.Client", side_effect=RuntimeError("blocking HTTP")) as client:
        # A public host so the SSRF guard passes and the fetch is attempted.
        assert branding.fetch_logo_bytes("https://8.8.8.8/logo.png") is None
    assert client.called, "the logo embed no longer performs a blocking fetch"


@pytest.mark.asyncio
async def test_remittance_render_runs_off_the_loop_thread():
    """End-to-end on the primitive: the renderer body executes on a worker."""
    import asyncio
    import threading
    from datetime import UTC, datetime
    from decimal import Decimal

    from app.services.remittance_pdf import (
        RemittanceContext,
        RemittanceLine,
        render_remittance_pdf,
    )

    ctx = RemittanceContext(
        payer_name="Acme",
        payer_address=None,
        vendor_name="Supplier",
        vendor_address=None,
        payment_date=datetime.now(UTC),
        payment_method="ach",
        payment_reference="REF-1",
        payment_amount=Decimal("100.00"),
        currency="USD",
        lines=[RemittanceLine(invoice_number="INV-1", description=None, amount=Decimal("100.00"))],
    )
    loop_thread = threading.current_thread().ident
    seen: list[int | None] = []
    real = render_remittance_pdf

    def _recording(c):
        seen.append(threading.current_thread().ident)
        return real(c)

    pdf = await asyncio.to_thread(_recording, ctx)
    assert pdf.startswith(b"%PDF")
    assert seen and all(tid != loop_thread for tid in seen)
