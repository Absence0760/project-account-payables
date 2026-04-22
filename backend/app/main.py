from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    admin,
    auth,
    auth_sso,
    cards,
    dashboard,
    erp_webhook,
    exceptions,
    gl_accounts,
    invoices,
    organization,
    payments,
    portal,
    portal_auth,
    purchase_orders,
    scim,
    signup,
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

    from app.services.extraction_reaper import run_reaper_loop

    reaper_task: asyncio.Task | None = None
    if settings.extraction_reaper_enabled:
        reaper_task = asyncio.create_task(run_reaper_loop(), name="extraction-reaper")

    try:
        yield
    finally:
        if reaper_task is not None:
            reaper_task.cancel()
            try:
                await reaper_task
            except (asyncio.CancelledError, Exception):
                pass

        from app.database import dispose_all_engines

        await dispose_all_engines()


app = FastAPI(
    title="Account Payables API",
    version="0.1.0",
    lifespan=lifespan,
)

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
app.include_router(erp_webhook.router, prefix="/api")
app.include_router(exceptions.router, prefix="/api")
app.include_router(gl_accounts.router, prefix="/api")
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
