"""
api/database.py
SQLAlchemy async engine targeting local PostgreSQL.
DSN is read from the PG_DSN environment variable (or .env via python-dotenv).

Default DSN:  postgresql+asyncpg://postgres:postgres@localhost:5432/medstar
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

# Load .env from project root (one level up from api/)
load_dotenv(Path(__file__).parent.parent / ".env")

# ── DSN ──────────────────────────────────────────────────────────────────────
PG_DSN: str = os.getenv(
    "PG_DSN",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/medstar",
)

# ── Engine ────────────────────────────────────────────────────────────────────
engine = create_async_engine(
    PG_DSN,
    echo=False,          # set True to see SQL in console during dev
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,  # heartbeat: drop stale connections automatically
)

# ── Session factory ───────────────────────────────────────────────────────────
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

# ── Declarative base (shared by all ORM models) ───────────────────────────────
class Base(DeclarativeBase):
    pass


# ── Dependency (used in FastAPI routers via Depends) ─────────────────────────
async def get_db() -> AsyncSession:
    """Yield an async DB session; roll back on error, always close."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
