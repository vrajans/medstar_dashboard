"""
password_reset.py  —  InsightHub Email-OTP Password Reset (US-217)
==================================================================
Flow
----
  1. GET  /forgot-password  → render email form
  2. POST /forgot-password  → generate 6-digit OTP, store in DB, email via SendGrid
  3. GET  /reset-password   → render OTP + new-password form (from ?token= link in email)
  4. POST /reset-password   → verify OTP, call auth.reset_password(), clear OTP

Security
--------
  • OTP is 6 digits, expires in 15 minutes
  • Token in email is a random 32-byte hex string mapped to user_id in a table
  • After 3 failed attempts, token is invalidated

Usage
-----
  from password_reset import register_password_reset_routes, init_reset_tables
  init_reset_tables(engine)
  register_password_reset_routes(app.server, engine)
"""

import os
import json
import logging
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

SENDGRID_API_KEY  = os.getenv("SENDGRID_API_KEY", "")
SENDGRID_FROM     = os.getenv("SENDGRID_FROM_EMAIL", "noreply@insighthub.ai")
APP_NAME          = os.getenv("APP_NAME", "InsightHub")
APP_URL           = os.getenv("APP_URL", "http://localhost:8050")
OTP_EXPIRY_MINS   = int(os.getenv("OTP_EXPIRY_MINS", "15"))


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def init_reset_tables(engine: Any) -> None:
    """Create password_reset_tokens table if not present."""
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS password_reset_tokens (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    INTEGER NOT NULL,
                    token      TEXT    NOT NULL UNIQUE,
                    otp        TEXT    NOT NULL,
                    attempts   INTEGER DEFAULT 0,
                    expires_at TEXT    NOT NULL,
                    used       INTEGER DEFAULT 0,
                    created_at TEXT    DEFAULT (datetime('now'))
                )
            """))
        logger.info("[password_reset] reset_tokens table ensured.")
    except Exception as exc:
        logger.error("[password_reset] init_reset_tables: %s", exc)


def _create_reset_token(engine: Any, user_id: int) -> tuple[str, str]:
    """Generate OTP + URL token, store in DB, return (token, otp)."""
    from sqlalchemy import text
    token   = secrets.token_hex(32)
    otp     = "".join(secrets.choice(string.digits) for _ in range(6))
    expires = (datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRY_MINS)).isoformat()
    with engine.begin() as conn:
        # Invalidate existing tokens for this user
        conn.execute(text("UPDATE password_reset_tokens SET used=1 WHERE user_id=:uid"),
                     {"uid": user_id})
        conn.execute(text("""
            INSERT INTO password_reset_tokens (user_id, token, otp, expires_at)
            VALUES (:uid, :tok, :otp, :exp)
        """), {"uid": user_id, "tok": token, "otp": otp, "exp": expires})
    return token, otp


def _validate_otp(engine: Any, token: str, otp: str) -> tuple[bool, int | None, str]:
    """
    Validate a reset token + OTP.
    Returns (success, user_id, error_message).
    """
    from sqlalchemy import text
    try:
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT user_id, otp, expires_at, attempts, used
                FROM password_reset_tokens WHERE token = :tok
            """), {"tok": token}).fetchone()
        if not row:
            return False, None, "Invalid or expired reset link."
        user_id, db_otp, expires_at, attempts, used = row
        if used:
            return False, None, "This reset link has already been used."
        exp = datetime.fromisoformat(expires_at)
        if datetime.now(timezone.utc) > exp:
            return False, None, f"Reset link expired. Please request a new one."
        if attempts >= 3:
            return False, None, "Too many failed attempts. Please request a new reset link."
        if otp.strip() != db_otp:
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE password_reset_tokens SET attempts = attempts + 1 WHERE token = :tok
                """), {"tok": token})
            return False, None, "Incorrect code. Please try again."
        return True, user_id, ""
    except Exception as exc:
        logger.error("[password_reset] _validate_otp: %s", exc)
        return False, None, "An error occurred. Please try again."


def _mark_token_used(engine: Any, token: str) -> None:
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text("UPDATE password_reset_tokens SET used=1 WHERE token=:tok"),
                     {"tok": token})


def _get_user_by_email_or_username(engine: Any, identifier: str):
    """Return (id, email, username) row or None."""
    from sqlalchemy import text
    try:
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT id, email, username FROM users
                WHERE (email = :q OR username = :q) AND active = 1
                LIMIT 1
            """), {"q": identifier.strip()}).fetchone()
        return row
    except Exception as exc:
        logger.error("[password_reset] _get_user_by_email_or_username: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Email sender
# ─────────────────────────────────────────────────────────────────────────────

def _send_reset_email(to_email: str, username: str, otp: str, token: str) -> bool:
    """Send password-reset email via SendGrid. Returns True on success."""
    reset_url = f"{APP_URL}/reset-password?token={token}"
    subject   = f"{APP_NAME} — Password Reset Code"
    body_text = (
        f"Hi {username},\n\n"
        f"Your {APP_NAME} password reset code is:\n\n"
        f"  {otp}\n\n"
        f"This code expires in {OTP_EXPIRY_MINS} minutes.\n\n"
        f"Or click this link: {reset_url}\n\n"
        f"If you didn't request a reset, please ignore this email.\n\n"
        f"— The {APP_NAME} Team"
    )
    body_html = f"""
    <div style="font-family:Inter,Arial,sans-serif;max-width:520px;margin:0 auto;padding:32px 24px">
      <div style="text-align:center;margin-bottom:24px">
        <div style="display:inline-block;background:linear-gradient(135deg,#2563EB,#0EA5E9);
             border-radius:14px;padding:14px 18px;font-size:22px;margin-bottom:12px">📊</div>
        <div style="font-size:1.3rem;font-weight:800;color:#1E293B">{APP_NAME}</div>
      </div>
      <h2 style="color:#1E293B;font-size:1.1rem;font-weight:700;margin-bottom:8px">
        Password Reset Request</h2>
      <p style="color:#64748B;font-size:0.9rem;line-height:1.6;margin-bottom:24px">
        Hi <strong>{username}</strong>, here is your one-time reset code:</p>
      <div style="background:#EFF6FF;border:2px dashed #BFDBFE;border-radius:12px;
           text-align:center;padding:20px;margin-bottom:24px">
        <div style="font-size:2.2rem;font-weight:800;letter-spacing:0.25em;color:#1E40AF">
          {otp}</div>
        <div style="color:#64748B;font-size:0.78rem;margin-top:6px">
          Expires in {OTP_EXPIRY_MINS} minutes</div>
      </div>
      <p style="color:#64748B;font-size:0.85rem;margin-bottom:16px">
        Or click the button below to open the reset page directly:</p>
      <div style="text-align:center;margin-bottom:24px">
        <a href="{reset_url}"
           style="background:linear-gradient(135deg,#2563EB,#1D4ED8);color:#fff;
                  text-decoration:none;padding:12px 28px;border-radius:9px;
                  font-size:0.95rem;font-weight:700">Reset My Password →</a>
      </div>
      <p style="color:#94A3B8;font-size:0.75rem;text-align:center">
        If you didn't request this, you can safely ignore this email.</p>
    </div>"""

    if not SENDGRID_API_KEY:
        # Fallback: log OTP so dev can use it without email setup
        logger.info("[password_reset] SENDGRID_API_KEY not set. OTP for %s: %s (token=%s)",
                    to_email, otp, token)
        return True  # return True so UI doesn't show an error

    try:
        import urllib.request
        payload = {
            "personalizations": [{"to": [{"email": to_email}]}],
            "from": {"email": SENDGRID_FROM, "name": APP_NAME},
            "subject": subject,
            "content": [
                {"type": "text/plain", "value": body_text},
                {"type": "text/html",  "value": body_html},
            ],
        }
        data = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(
            "https://api.sendgrid.com/v3/mail/send",
            data    = data,
            headers = {
                "Authorization": f"Bearer {SENDGRID_API_KEY}",
                "Content-Type":  "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 202)
    except Exception as exc:
        logger.error("[password_reset] _send_reset_email: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# HTML Templates
# ─────────────────────────────────────────────────────────────────────────────

_BASE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>{title} — {app_name}</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0F172A;min-height:100vh;display:flex;align-items:center;
     justify-content:center;font-family:Inter,-apple-system,sans-serif;-webkit-font-smoothing:antialiased}}
body::before{{content:'';position:fixed;inset:0;
  background-image:linear-gradient(rgba(37,99,235,0.05) 1px,transparent 1px),
    linear-gradient(90deg,rgba(37,99,235,0.05) 1px,transparent 1px);
  background-size:48px 48px;pointer-events:none}}
.wrap{{position:relative;z-index:1;width:100%;max-width:420px;padding:24px}}
.logo-box{{text-align:center;margin-bottom:28px}}
.logo-icon{{display:inline-flex;align-items:center;justify-content:center;
  width:48px;height:48px;background:linear-gradient(135deg,#2563EB,#0EA5E9);
  border-radius:14px;font-size:22px;margin-bottom:12px;
  box-shadow:0 8px 24px rgba(37,99,235,0.35)}}
.logo-name{{font-size:1.55rem;font-weight:800;color:#FFF;letter-spacing:-0.5px}}
.logo-tag{{font-size:0.78rem;color:#64748B;margin-top:4px}}
.card{{background:#1E293B;border:1px solid rgba(255,255,255,0.08);border-radius:16px;
  padding:32px 32px 28px;box-shadow:0 24px 64px rgba(0,0,0,0.4)}}
.card-title{{font-size:1.05rem;font-weight:700;color:#F1F5F9;margin-bottom:4px}}
.card-sub{{font-size:0.78rem;color:#64748B;margin-bottom:28px}}
label{{display:block;font-size:0.69rem;font-weight:600;text-transform:uppercase;
  letter-spacing:0.07em;color:#94A3B8;margin-bottom:5px;margin-top:18px}}
input[type=text],input[type=email],input[type=password]{{width:100%;padding:11px 14px;
  font-size:0.9rem;background:#0F172A;border:1px solid rgba(255,255,255,0.10);
  border-radius:8px;outline:none;color:#F1F5F9;transition:border-color 0.15s;font-family:inherit}}
input::placeholder{{color:#334155}}
input:focus{{border-color:#2563EB;box-shadow:0 0 0 3px rgba(37,99,235,0.20)}}
.btn{{display:block;width:100%;margin-top:26px;padding:12px;font-size:0.95rem;
  font-weight:700;background:linear-gradient(135deg,#2563EB,#1D4ED8);color:#fff;
  border:none;border-radius:9px;cursor:pointer;font-family:inherit;
  box-shadow:0 4px 16px rgba(37,99,235,0.30)}}
.btn:hover{{opacity:0.93}}
.err{{background:rgba(220,38,38,0.12);border:1px solid rgba(220,38,38,0.35);
  border-radius:8px;color:#FCA5A5;font-size:0.8rem;padding:10px 14px;
  margin-bottom:16px;display:flex;align-items:center;gap:8px}}
.ok{{background:rgba(5,150,105,0.12);border:1px solid rgba(5,150,105,0.35);
  border-radius:8px;color:#6EE7B7;font-size:0.8rem;padding:10px 14px;
  margin-bottom:16px}}
.divider{{border:none;border-top:1px solid rgba(255,255,255,0.06);margin:24px 0 18px}}
.back{{text-align:center;font-size:0.75rem;color:#64748B;margin-top:16px}}
.back a{{color:#60A5FA;text-decoration:none}}
.otp-input{{text-align:center;font-size:1.8rem;letter-spacing:0.35em;font-weight:700}}
</style>
</head>
<body>
<div class="wrap">
  <div class="logo-box">
    <div class="logo-icon">&#x1F4CA;</div>
    <div class="logo-name">{app_name}</div>
    <div class="logo-tag">Business Analytics Platform</div>
  </div>
  {body}
</div>
</body>
</html>"""


def _page(title, body, app_name=APP_NAME):
    return _BASE.format(title=title, app_name=app_name, body=body)


FORGOT_FORM = """
<div class="card">
  <div class="card-title">Forgot your password?</div>
  <div class="card-sub">Enter your email or username and we'll send a reset code.</div>
  {error}
  {success}
  <form method="POST" action="/forgot-password">
    <label for="identifier">Email or Username</label>
    <input type="text" id="identifier" name="identifier"
           placeholder="you@company.com" autofocus required>
    <button type="submit" class="btn">Send Reset Code &rarr;</button>
  </form>
  <hr class="divider">
  <div class="back"><a href="/login">&larr; Back to Sign In</a></div>
</div>"""

RESET_FORM = """
<div class="card">
  <div class="card-title">Enter Reset Code</div>
  <div class="card-sub">Check your email for the 6-digit code we just sent.</div>
  {error}
  <form method="POST" action="/reset-password">
    <input type="hidden" name="token" value="{token}">
    <label for="otp">6-Digit Code</label>
    <input type="text" id="otp" name="otp" class="otp-input"
           maxlength="6" placeholder="000000" autocomplete="one-time-code" autofocus>
    <label for="new_password">New Password</label>
    <input type="password" id="new_password" name="new_password"
           placeholder="At least 8 characters" required>
    <label for="confirm_password">Confirm New Password</label>
    <input type="password" id="confirm_password" name="confirm_password"
           placeholder="Repeat password" required>
    <button type="submit" class="btn">Reset Password &rarr;</button>
  </form>
  <hr class="divider">
  <div class="back"><a href="/forgot-password">Resend code</a> &middot; <a href="/login">Sign In</a></div>
</div>"""

RESET_DONE = """
<div class="card" style="text-align:center">
  <div style="font-size:3rem;margin-bottom:0.75rem">✅</div>
  <div class="card-title">Password Updated!</div>
  <div class="card-sub" style="margin-bottom:24px">
    Your password has been changed successfully. You can now sign in.</div>
  <a href="/login" class="btn" style="text-decoration:none;display:inline-block;width:auto;padding:12px 28px">
    Sign In &rarr;</a>
</div>"""


# ─────────────────────────────────────────────────────────────────────────────
# Flask route registration
# ─────────────────────────────────────────────────────────────────────────────

def register_password_reset_routes(flask_app: Any, engine: Any) -> None:
    """Register /forgot-password and /reset-password Flask routes."""
    from flask import request, redirect

    # ── Forgot password — step 1 ──────────────────────────────────────────
    @flask_app.route("/forgot-password", methods=["GET", "POST"])
    def forgot_password():
        error   = ""
        success = ""
        if request.method == "POST":
            identifier = request.form.get("identifier", "").strip()
            user = _get_user_by_email_or_username(engine, identifier)
            # Always show success message to prevent user enumeration
            if user:
                uid, email, username = user
                if email:
                    token, otp = _create_reset_token(engine, uid)
                    _send_reset_email(email, username, otp, token)
            success = (
                '<div class="ok">✅ If an account exists for that email/username, '
                'a reset code has been sent. Check your inbox (and spam folder).</div>'
            )
        body = FORGOT_FORM.format(
            error=f'<div class="err"><span>⚠</span><span>{error}</span></div>' if error else "",
            success=success,
        )
        return _page("Forgot Password", body)

    # ── Reset password — step 2 ───────────────────────────────────────────
    @flask_app.route("/reset-password", methods=["GET", "POST"])
    def reset_password_page():
        token = request.args.get("token") or request.form.get("token", "")
        error = ""

        if not token:
            return redirect("/forgot-password")

        if request.method == "POST":
            otp          = request.form.get("otp", "").strip()
            new_pass     = request.form.get("new_password", "")
            confirm_pass = request.form.get("confirm_password", "")

            if len(new_pass) < 8:
                error = "Password must be at least 8 characters."
            elif new_pass != confirm_pass:
                error = "Passwords do not match."
            else:
                ok, user_id, msg = _validate_otp(engine, token, otp)
                if ok:
                    from auth import reset_password as do_reset
                    do_reset(user_id, new_pass)
                    _mark_token_used(engine, token)
                    return _page("Password Updated", RESET_DONE)
                else:
                    error = msg

        body = RESET_FORM.format(
            token=token,
            error=f'<div class="err"><span>⚠</span><span>{error}</span></div>' if error else "",
        )
        return _page("Reset Password", body)

    logger.info("[password_reset] Routes registered: /forgot-password, /reset-password")
