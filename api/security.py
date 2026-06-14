"""
api/security.py
JWT token creation / verification + password hashing for InsightHub.

Tokens
------
  access_token  : HS256 JWT, 15 min TTL, carries sub (user_id) + role
  refresh_token : HS256 JWT, 7 day TTL, carries sub + jti (unique ID)
                  jti is stored (hashed) in the DB so we can revoke on logout.

Config (via environment / .env)
------
  JWT_SECRET         : signing secret  (required in production)
  JWT_ALGORITHM      : default HS256
  ACCESS_TOKEN_MINS  : default 15
  REFRESH_TOKEN_DAYS : default 7
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt as _bcrypt
from jose import JWTError, jwt

# ── Config ────────────────────────────────────────────────────────────────────
JWT_SECRET          = os.getenv("JWT_SECRET", "insighthub-jwt-secret-change-in-prod-2026")
JWT_ALGORITHM       = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_MINS   = int(os.getenv("ACCESS_TOKEN_MINS",  "15"))
REFRESH_TOKEN_DAYS  = int(os.getenv("REFRESH_TOKEN_DAYS", "7"))

ACCESS_TOKEN_TTL  = timedelta(minutes=ACCESS_TOKEN_MINS)
REFRESH_TOKEN_TTL = timedelta(days=REFRESH_TOKEN_DAYS)

# ── Password hashing (direct bcrypt — no passlib) ─────────────────────────────
# passlib has a known incompatibility with bcrypt >= 4.0 (missing __about__).
# Using bcrypt directly works with all versions >= 3.x.

def hash_password(plain: str) -> str:
    return _bcrypt.hashpw(plain.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ── Token creation ────────────────────────────────────────────────────────────

def create_access_token(user_id: int, username: str, role: str) -> str:
    """Create a short-lived JWT access token."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub":      str(user_id),
        "username": username,
        "role":     role,
        "type":     "access",
        "iat":      now,
        "exp":      now + ACCESS_TOKEN_TTL,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: int) -> tuple[str, str, datetime]:
    """Create a long-lived JWT refresh token.

    Returns:
        (token_str, jti, expires_at)
        jti is the unique token ID stored (hashed) in the DB.
    """
    now     = datetime.now(timezone.utc)
    expires = now + REFRESH_TOKEN_TTL
    jti     = str(uuid.uuid4())
    payload = {
        "sub":  str(user_id),
        "jti":  jti,
        "type": "refresh",
        "iat":  now,
        "exp":  expires,
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return token, jti, expires


def hash_jti(jti: str) -> str:
    """SHA-256 hash of a jti for safe DB storage (never store raw tokens)."""
    return hashlib.sha256(jti.encode()).hexdigest()


# ── Token verification ────────────────────────────────────────────────────────

def decode_access_token(token: str) -> Optional[dict]:
    """Decode and validate an access token.  Returns payload or None."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            return None
        return payload
    except JWTError:
        return None


def decode_refresh_token(token: str) -> Optional[dict]:
    """Decode and validate a refresh token.  Returns payload or None."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "refresh":
            return None
        return payload
    except JWTError:
        return None


# ── Convenience ───────────────────────────────────────────────────────────────

ACCESS_TOKEN_EXPIRES_SECONDS = int(ACCESS_TOKEN_TTL.total_seconds())
