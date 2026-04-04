from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, dashboard, invoices, vendors


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
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
app.include_router(auth.router, prefix="/api")
app.include_router(invoices.router, prefix="/api")
app.include_router(vendors.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")


@app.get("/api/health")
async def health():
    return {"status": "ok"}
