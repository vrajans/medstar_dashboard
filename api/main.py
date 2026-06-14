"""
api/main.py
InsightHub FastAPI service -- runs on port 8000.
Dash dashboard continues to run independently on port 8050.

Start:
    uvicorn api.main:app --reload --port 8000

Interactive docs:
    http://127.0.0.1:8000/docs     (Swagger UI)
    http://127.0.0.1:8000/redoc    (ReDoc)
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .database import engine, Base
from .models import User, SalesRecord, PurchaseRecord, RefreshToken  # noqa: F401
from .models import Tenant, TenantModule, SchemaMapping              # noqa: F401 (register all models)
from .routers import auth, sales, purchases
from .routers import tenants, domains
from .schemas import HealthResponse
from .security import hash_password
from .domain_library import DEFAULT_MODULES, MEDSTAR_DEFAULT_MAPPINGS

# ── Lifespan: create tables + seed default users on first run ─────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        # Create all tables (no-op if they already exist)
        await conn.run_sync(Base.metadata.create_all)

        # Seed default admin / viewer if users table is empty
        from sqlalchemy import select, func
        from .models import User as _User, Tenant as _Tenant, TenantModule as _TM, SchemaMapping as _SM
        result = await conn.execute(select(func.count()).select_from(_User))
        if result.scalar_one() == 0:
            await conn.execute(
                _User.__table__.insert(),
                [
                    {
                        "username":      "admin",
                        "display_name":  "Administrator",
                        "password_hash": hash_password("admin123"),
                        "role":          "admin",
                        "is_active":     True,
                    },
                    {
                        "username":      "viewer",
                        "display_name":  "Viewer",
                        "password_hash": hash_password("viewer123"),
                        "role":          "viewer",
                        "is_active":     True,
                    },
                ],
            )

        # Seed MedStar as the default tenant (pharmacy domain)
        t_result = await conn.execute(select(func.count()).select_from(_Tenant))
        if t_result.scalar_one() == 0:
            t_ins = await conn.execute(
                _Tenant.__table__.insert().values(
                    name          = "MedStar Pharmacy",
                    slug          = "medstar",
                    domain_type   = "pharmacy",
                    plan          = "pro",
                    contact_email = "admin@medstar.local",
                    is_active     = True,
                ).returning(_Tenant.__table__.c.id)
            )
            tenant_id = t_ins.scalar_one()

            # Seed all default modules (all enabled)
            await conn.execute(
                _TM.__table__.insert(),
                [{"tenant_id": tenant_id, "module_name": m, "is_enabled": True}
                 for m in DEFAULT_MODULES],
            )

            # Seed identity schema mappings (source == canonical for MedStar)
            await conn.execute(
                _SM.__table__.insert(),
                [{"tenant_id": tenant_id, "domain_type": "pharmacy",
                  "entity": row["entity"],
                  "source_column": row["source_column"],
                  "canonical_column": row["canonical_column"]}
                 for row in MEDSTAR_DEFAULT_MAPPINGS],
            )

    yield  # App runs here

    # Shutdown: dispose connection pool
    await engine.dispose()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title       = "InsightHub API",
    description = "Multi-branch pharmacy analytics REST service for MedStar",
    version     = "1.0.0",
    lifespan    = lifespan,
    docs_url    = "/docs",
    redoc_url   = "/redoc",
)

# ── CORS (allow Dash on :8050 to call this API) ───────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins  = [
        "http://localhost:8050",
        "http://127.0.0.1:8050",
        "http://localhost:3000",   # future React frontend
        "http://127.0.0.1:3000",
    ],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(auth.router)
app.include_router(sales.router)
app.include_router(purchases.router)
app.include_router(tenants.router)
app.include_router(domains.router)

# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health():
    """Quick liveness probe — also verifies DB connectivity."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return HealthResponse()


@app.get("/", tags=["System"])
async def root():
    return {
        "service": "InsightHub API",
        "version": "1.0.0",
        "docs":    "/docs",
        "health":  "/health",
    }
