"""
google_sso.py — US-407
Google OAuth 2.0 SSO for InsightHub (no flask-dance dependency).

Flow:
  GET /google-login      → redirect to Google's OAuth consent page
  GET /google-callback   → exchange code → fetch userinfo → find/create user → login

Required env vars:
  GOOGLE_CLIENT_ID       — from Google Cloud Console (OAuth 2.0 client)
  GOOGLE_CLIENT_SECRET   — from Google Cloud Console
  GOOGLE_REDIRECT_URI    — defaults to {APP_URL}/google-callback
  APP_URL                — e.g. https://app.insighthub.ai  (defaults to http://localhost:8050)

Optional env vars:
  GOOGLE_SSO_DEFAULT_ROLE  — role assigned on first login (default: "viewer")
  GOOGLE_SSO_DOMAIN_HINT   — if set, only allow emails from this domain
                             e.g. "company.com"  → rejects other domains
"""

import os
import json
import secrets
import urllib.parse
import urllib.request
import logging

from flask import redirect, request, session, flash
from flask_login import login_user

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
APP_URL              = os.getenv("APP_URL", "http://localhost:8050").rstrip("/")
GOOGLE_REDIRECT_URI  = os.getenv("GOOGLE_REDIRECT_URI", f"{APP_URL}/google-callback")
GOOGLE_DEFAULT_ROLE  = os.getenv("GOOGLE_SSO_DEFAULT_ROLE", "viewer")
GOOGLE_DOMAIN_HINT   = os.getenv("GOOGLE_SSO_DOMAIN_HINT", "")   # e.g. "company.com"

_GOOGLE_AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO  = "https://www.googleapis.com/oauth2/v3/userinfo"

_SCOPES = "openid email profile"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _google_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def _build_auth_url(state: str) -> str:
    params = {
        "client_id":     GOOGLE_CLIENT_ID,
        "redirect_uri":  GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope":         _SCOPES,
        "state":         state,
        "access_type":   "online",
        "prompt":        "select_account",
    }
    if GOOGLE_DOMAIN_HINT:
        params["hd"] = GOOGLE_DOMAIN_HINT
    return f"{_GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


def _exchange_code(code: str) -> dict:
    """Exchange auth code for token dict."""
    payload = urllib.parse.urlencode({
        "code":          code,
        "client_id":     GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri":  GOOGLE_REDIRECT_URI,
        "grant_type":    "authorization_code",
    }).encode()
    req = urllib.request.Request(
        _GOOGLE_TOKEN_URL, data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _fetch_userinfo(access_token: str) -> dict:
    req = urllib.request.Request(
        _GOOGLE_USERINFO,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


# ── DB helpers ────────────────────────────────────────────────────────────────

def _ensure_sso_columns(engine) -> None:
    """Add sso_provider and email columns to users table if missing."""
    from sqlalchemy import text
    for col, defn in [("email", "TEXT"), ("sso_provider", "TEXT")]:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {defn}"))
        except Exception:
            pass   # already exists


def _find_user_by_email(engine, email: str):
    """Return row or None.  Prefers SSO users, falls back to matching email."""
    from sqlalchemy import text
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id, username, role, display_name, tenant_id, tenant_name, "
            "tenant_domain FROM users WHERE email=:email AND active=1 LIMIT 1"
        ), {"email": email}).fetchone()
    return row


def _create_sso_user(engine, email: str, display_name: str, given_name: str) -> int:
    """Create a new user from Google SSO.  Returns the new user id."""
    from sqlalchemy import text
    # derive username from email local part; de-duplicate
    base = email.split("@")[0].lower().replace(".", "_")
    username = base
    with engine.begin() as conn:
        # uniqueness loop
        suffix = 1
        while conn.execute(text(
            "SELECT 1 FROM users WHERE username=:u AND COALESCE(tenant_id,0)=0"
        ), {"u": username}).fetchone():
            username = f"{base}{suffix}"
            suffix  += 1

        result = conn.execute(text("""
            INSERT INTO users
                (username, password_hash, role, display_name, email, sso_provider, active)
            VALUES (:u, '', :role, :dn, :email, 'google', 1)
        """), {
            "u":     username,
            "role":  GOOGLE_DEFAULT_ROLE,
            "dn":    display_name or given_name or email,
            "email": email,
        })
        return result.lastrowid


def _load_user_obj(engine, row) -> "User":
    """Hydrate a User object from a DB row tuple."""
    from auth import User
    return User(
        user_id      = row[0],
        username     = row[1],
        role         = row[2],
        display_name = row[3],
        tenant_id    = row[4],
        tenant_name  = row[5],
        tenant_domain= row[6] or "pharmacy",
    )


# ── Flask routes ──────────────────────────────────────────────────────────────

def register_google_sso_routes(flask_app, auth_engine) -> None:
    """Register /google-login and /google-callback on the Flask app."""

    _ensure_sso_columns(auth_engine)

    @flask_app.route("/google-login")
    def google_login():
        if not _google_configured():
            flash("Google SSO is not configured.  Contact the administrator.", "warning")
            return redirect("/login")
        state = secrets.token_urlsafe(16)
        session["google_oauth_state"] = state
        next_url = request.args.get("next", "/")
        session["google_next_url"] = next_url
        return redirect(_build_auth_url(state))

    @flask_app.route("/google-callback")
    def google_callback():
        if not _google_configured():
            return redirect("/login")

        # ── CSRF / state check ───────────────────────────────────────────────
        returned_state = request.args.get("state", "")
        expected_state = session.pop("google_oauth_state", "")
        if not returned_state or returned_state != expected_state:
            logger.warning("[google_sso] CSRF state mismatch — login rejected.")
            flash("OAuth state mismatch.  Please try again.", "danger")
            return redirect("/login")

        # ── Error from Google ─────────────────────────────────────────────────
        error = request.args.get("error")
        if error:
            flash(f"Google sign-in cancelled: {error}", "warning")
            return redirect("/login")

        code = request.args.get("code")
        if not code:
            flash("Missing authorization code from Google.", "danger")
            return redirect("/login")

        try:
            tokens    = _exchange_code(code)
        except Exception as exc:
            logger.error("[google_sso] Token exchange failed: %s", exc)
            flash("Failed to exchange Google authorization code.  Please try again.", "danger")
            return redirect("/login")

        access_token = tokens.get("access_token")
        if not access_token:
            flash("No access token returned from Google.", "danger")
            return redirect("/login")

        try:
            userinfo = _fetch_userinfo(access_token)
        except Exception as exc:
            logger.error("[google_sso] Userinfo fetch failed: %s", exc)
            flash("Failed to retrieve your Google profile.", "danger")
            return redirect("/login")

        email = (userinfo.get("email") or "").lower().strip()
        if not email:
            flash("Could not retrieve email from Google account.", "danger")
            return redirect("/login")

        # ── Domain restriction ────────────────────────────────────────────────
        if GOOGLE_DOMAIN_HINT and not email.endswith(f"@{GOOGLE_DOMAIN_HINT}"):
            flash(f"Only @{GOOGLE_DOMAIN_HINT} accounts are allowed.", "danger")
            return redirect("/login")

        # ── Find or create user ───────────────────────────────────────────────
        try:
            row = _find_user_by_email(auth_engine, email)
            if row:
                user_obj = _load_user_obj(auth_engine, row)
            else:
                display_name = userinfo.get("name", "")
                given_name   = userinfo.get("given_name", "")
                new_id = _create_sso_user(auth_engine, email, display_name, given_name)
                row = _find_user_by_email(auth_engine, email)
                if not row:
                    raise RuntimeError("User creation failed after insert.")
                user_obj = _load_user_obj(auth_engine, row)
        except Exception as exc:
            logger.error("[google_sso] User find/create failed: %s", exc)
            flash("Account lookup failed.  Please contact support.", "danger")
            return redirect("/login")

        login_user(user_obj, remember=True)
        logger.info("[google_sso] User %s (%s) signed in via Google.", user_obj.username, email)
        next_url = session.pop("google_next_url", "/")
        return redirect(next_url or "/")
