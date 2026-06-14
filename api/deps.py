"""
api/deps.py
FastAPI dependency injection helpers.

Usage in routers:
    from .deps import CurrentUser, AdminUser, DBSession

    @router.get("/something")
    async def endpoint(user: CurrentUser, db: DBSession):
        ...
"""

from __future__ import annotations

from typing import Annotated, AsyncGenerator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import AsyncSessionLocal
from .models import User
from .security import decode_access_token

# ── DB session ────────────────────────────────────────────────────────────────

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async DB session; commit on success, rollback on error."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

DBSession = Annotated[AsyncSession, Depends(get_db)]

# ── Bearer token extractor ────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=True)

# ── Current user dependency ───────────────────────────────────────────────────

async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
    db: DBSession,
) -> User:
    """Validate Bearer JWT and return the active User ORM object.

    Raises 401 for invalid/expired tokens, 403 for inactive accounts.
    """
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise credentials_exc

    user_id: str | None = payload.get("sub")
    if user_id is None:
        raise credentials_exc

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user: User | None = result.scalar_one_or_none()

    if user is None:
        raise credentials_exc

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


# ── Admin guard ───────────────────────────────────────────────────────────────

async def require_admin(user: CurrentUser) -> User:
    """Allow only admin-role users.  Raises 403 for viewers."""
    if not user.is_admin():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


AdminUser = Annotated[User, Depends(require_admin)]
