from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.api import auth, dashboard, invoices, vendors


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    yield
    # Shutdown
    from app.database import engine

    await engine.dispose()


app = FastAPI(
    title="Account Payables API",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth.router, prefix="/api")
app.include_router(invoices.router, prefix="/api")
app.include_router(vendors.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")


@app.get("/api/health")
async def health():
    return {"status": "ok"}
