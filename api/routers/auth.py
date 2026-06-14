"""
api/routers/auth.py
JWT authentication endpoints.

POST  /auth/login    -> TokenResponse (access + refresh)
POST  /auth/refresh  -> AccessTokenResponse (new access token)
POST  /auth/logout   -> revokes the refresh token in DB
GET   /auth/me       -> UserOut (current user info)
POST  /auth/users    -> create a new user  (admin only)
PATCH /auth/users/{user_id} -> update role / active  (admin only)
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ..database import get_db
from ..deps import AdminUser, CurrentUser, DBSession
from ..models import RefreshToken, User
from ..schemas import (
    AccessTokenResponse,
    LoginRequest,
    RefreshRequest,
    TokenResponse,
    UserCreate,
    UserOut,
    UserUpdate,
)
from ..security import (
    ACCESS_TOKEN_EXPIRES_SECONDS,
    create_access_token,
    create_refresh_token,
    hash_jti,
    hash_password,
    verify_password,
    decode_refresh_token,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


# ── Login ─────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse, summary="Obtain access + refresh tokens")
async def login(body: LoginRequest, db: DBSession):
    result = await db.execute(
        select(User).where(User.username == body.username.lower().strip())
    )
    user: User | None = result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated — contact an administrator",
        )

    access  = create_access_token(user.id, user.username, user.role)
    refresh, jti, expires_at = create_refresh_token(user.id)

    # Persist refresh token (hashed) for revocation support
    db.add(RefreshToken(
        user_id    = user.id,
        token_hash = hash_jti(jti),
        expires_at = expires_at,
    ))
    await db.flush()

    return TokenResponse(
        access_token  = access,
        refresh_token = refresh,
        expires_in    = ACCESS_TOKEN_EXPIRES_SECONDS,
    )


# ── Refresh ───────────────────────────────────────────────────────────────────

@router.post("/refresh", response_model=AccessTokenResponse, summary="Exchange refresh token for new access token")
async def refresh_token(body: RefreshRequest, db: DBSession):
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or revoked refresh token",
        headers={"WWW-Authenticate": "Bearer"},
    )

    payload = decode_refresh_token(body.refresh_token)
    if payload is None:
        raise invalid

    jti     = payload.get("jti")
    user_id = payload.get("sub")
    if not jti or not user_id:
        raise invalid

    # Look up in DB — must exist and not be revoked
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_jti(jti),
            RefreshToken.user_id    == int(user_id),
            RefreshToken.revoked    == False,  # noqa: E712
        )
    )
    rt: RefreshToken | None = result.scalar_one_or_none()
    if rt is None:
        raise invalid

    if rt.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise invalid

    # Fetch user
    u_result = await db.execute(select(User).where(User.id == int(user_id)))
    user: User | None = u_result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise invalid

    new_access = create_access_token(user.id, user.username, user.role)
    return AccessTokenResponse(
        access_token = new_access,
        expires_in   = ACCESS_TOKEN_EXPIRES_SECONDS,
    )


# ── Logout ────────────────────────────────────────────────────────────────────

@router.post("/logout", summary="Revoke refresh token (logout)")
async def logout(body: RefreshRequest, db: DBSession):
    payload = decode_refresh_token(body.refresh_token)
    if payload:
        jti = payload.get("jti")
        if jti:
            result = await db.execute(
                select(RefreshToken).where(RefreshToken.token_hash == hash_jti(jti))
            )
            rt = result.scalar_one_or_none()
            if rt:
                rt.revoked = True
    return {"detail": "Logged out"}


# ── Me ────────────────────────────────────────────────────────────────────────

@router.get("/me", response_model=UserOut, summary="Get current user info")
async def me(user: CurrentUser):
    return user


# ── User management (admin only) ──────────────────────────────────────────────

@router.post("/users", response_model=UserOut, status_code=201, summary="Create user (admin)")
async def create_user(body: UserCreate, admin: AdminUser, db: DBSession):
    # Check duplicate username
    existing = await db.execute(
        select(User).where(User.username == body.username.lower().strip())
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username already exists")

    user = User(
        username      = body.username.lower().strip(),
        display_name  = body.display_name or body.username.capitalize(),
        password_hash = hash_password(body.password),
        role          = body.role,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=UserOut, summary="Update user role / status (admin)")
async def update_user(user_id: int, body: UserUpdate, admin: AdminUser, db: DBSession):
    result = await db.execute(select(User).where(User.id == user_id))
    user: User | None = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Protect the built-in admin account
    if user.username == "admin" and body.is_active is False:
        raise HTTPException(status_code=400, detail="Cannot deactivate the built-in admin account")

    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active

    await db.flush()
    await db.refresh(user)
    return user
