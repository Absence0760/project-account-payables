from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.api import (
    admin,
    auth,
    auth_sso,
    cards,
    credit_memos,
    dashboard,
    email_intake,
    erp_webhook,
    exceptions,
    gl_accounts,
    goods_receipts,
    invoices,
    organization,
    payments,
    portal,
    portal_auth,
    purchase_orders,
    scim,
    signup,
    tax,
    vendors,
    workflow,
    workflow_definitions,
)
from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Background reaper for invoices stuck in `pending` extraction. Started
    # on app boot, cancelled cleanly on shutdown. Toggleable via
    # AP_EXTRACTION_REAPER_ENABLED so tests / one-shot CLI runs can disable it.
    import asyncio

    from app.services.approval_escalation import run_escalation_loop
    from app.services.audit_log_shipper import run_shipper_loop
    from app.services.extraction_reaper import run_reaper_loop
    from app.services.payment_reconciler import run_reconciler_loop

    reaper_task: asyncio.Task | None = None
    shipper_task: asyncio.Task | None = None
    escalation_task: asyncio.Task | None = None
    reconciler_task: asyncio.Task | None = None
    if settings.extraction_reaper_enabled:
        reaper_task = asyncio.create_task(run_reaper_loop(), name="extraction-reaper")
    # Centralized audit-log shipper (SOC 2). Disabled by default so local
    # dev doesn't spin up AWS clients; flip AP_AUDIT_SHIPPING_ENABLED on in
    # deployed envs.
    if settings.audit_shipping_enabled:
        shipper_task = asyncio.create_task(run_shipper_loop(), name="audit-log-shipper")
    if settings.approval_escalation_enabled:
        escalation_task = asyncio.create_task(run_escalation_loop(), name="approval-escalation")
    if settings.payment_reconcile_enabled:
        reconciler_task = asyncio.create_task(run_reconciler_loop(), name="payment-reconciler")

    try:
        yield
    finally:
        for task in (reaper_task, shipper_task, escalation_task, reconciler_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        from app.database import dispose_all_engines

        await dispose_all_engines()


app = FastAPI(
    title="Account Payables API",
    version="0.1.0",
    lifespan=lifespan,
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach SOC 2 tablestakes security headers to every response.

    - `Strict-Transport-Security` (HSTS) is gated on `AP_HSTS_ENABLED` so the
      local HTTP dev server doesn't accidentally pin `localhost` to HTTPS in
      developer browsers. Flip the flag on in deployed envs.
    - The other three headers have no HTTP/HTTPS dependency and are always
      set — auditors look for them alongside HSTS and they cost nothing.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        if settings.hsts_enabled:
            parts = [f"max-age={settings.hsts_max_age}"]
            if settings.hsts_include_subdomains:
                parts.append("includeSubDomains")
            if settings.hsts_preload:
                parts.append("preload")
            response.headers["Strict-Transport-Security"] = "; ".join(parts)

        # Always-on tablestakes headers.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response


app.add_middleware(SecurityHeadersMiddleware)

# CORS — allow any subdomain of localhost or the production domain
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://([\w-]+\.)?(localhost(:\d+)?|app\.com)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(admin.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(cards.router, prefix="/api")
app.include_router(credit_memos.router, prefix="/api")
app.include_router(erp_webhook.router, prefix="/api")
app.include_router(exceptions.router, prefix="/api")
app.include_router(gl_accounts.router, prefix="/api")
app.include_router(goods_receipts.router, prefix="/api")
app.include_router(invoices.router, prefix="/api")
app.include_router(purchase_orders.router, prefix="/api")
app.include_router(vendors.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(organization.router, prefix="/api")
app.include_router(payments.router, prefix="/api")
app.include_router(signup.router, prefix="/api")
app.include_router(auth_sso.router, prefix="/api")
app.include_router(scim.router, prefix="/api")
app.include_router(workflow.router, prefix="/api")
app.include_router(workflow_definitions.router, prefix="/api")
app.include_router(portal_auth.router, prefix="/api")
app.include_router(portal.router, prefix="/api")
app.include_router(email_intake.public_router, prefix="/api")
app.include_router(email_intake.admin_router, prefix="/api")
app.include_router(tax.router, prefix="/api")


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/public-config")
async def public_config():
    """Non-secret config exposed to the frontend (e.g., captcha sitekey,
    tenant URL shape used by the signup form and other non-tenant pages)."""
    return {
        "hcaptcha_sitekey": settings.hcaptcha_sitekey,
        "tenant_url_template": settings.tenant_url_template,
    }
