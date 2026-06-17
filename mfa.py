"""
mfa.py  —  InsightHub TOTP-based Multi-Factor Authentication
=============================================================
Uses pyotp for TOTP (RFC 6238) and qrcode for QR code generation.

Flow
----
  1. User lands on /mfa/setup
       → Generate TOTP secret, save encrypted in users table
       → Show QR code for Google Authenticator / Authy
  2. User scans code, enters 6-digit token
       → POST /mfa/verify-setup confirms token
       → Sets users.mfa_enabled = 1
  3. On subsequent logins (if mfa_enabled):
       → Flask-Login authenticates password OK
       → Before completing login, redirect to /mfa/verify
       → User enters current 6-digit token → login completes

Dash integration
----------------
  from mfa import render_mfa_setup_tab, init_mfa_tables, register_mfa_routes
  init_mfa_tables(engine)
  register_mfa_routes(app.server, engine)
"""

import os
import io
import base64
import logging
from typing import Any, Optional
from dash import html
import dash_bootstrap_components as dbc

logger = logging.getLogger(__name__)

# ── Optional imports ──────────────────────────────────────────────────────────
try:
    import pyotp
    HAS_PYOTP = True
except ImportError:
    HAS_PYOTP = False
    logger.warning("[mfa] pyotp not installed — MFA disabled. Run: pip install pyotp qrcode[pil]")

try:
    import qrcode
    from qrcode.image.pil import PilImage
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False

APP_NAME   = os.getenv("APP_NAME",    "InsightHub")
ISSUER     = os.getenv("MFA_ISSUER",  "InsightHub Analytics")
MFA_DIGITS = int(os.getenv("MFA_DIGITS", "6"))
MFA_PERIOD = int(os.getenv("MFA_PERIOD", "30"))


# ═════════════════════════════════════════════════════════════════════════════
# DB helpers
# ═════════════════════════════════════════════════════════════════════════════

def init_mfa_tables(engine: Any) -> None:
    """Add MFA columns to users table if not present."""
    alters = [
        "ALTER TABLE users ADD COLUMN mfa_enabled INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN mfa_secret  TEXT",
    ]
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            for sql in alters:
                try:
                    conn.execute(text(sql))
                except Exception:
                    pass  # column already exists
        logger.info("[mfa] MFA columns ensured on users table.")
    except Exception as exc:
        logger.error("[mfa] init_mfa_tables: %s", exc)


def _get_mfa_record(engine: Any, user_id: int) -> dict:
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT mfa_enabled, mfa_secret FROM users WHERE id = :uid
            """), {"uid": user_id}).fetchone()
        if row:
            return {"mfa_enabled": bool(row[0]), "mfa_secret": row[1]}
    except Exception as exc:
        logger.error("[mfa] _get_mfa_record: %s", exc)
    return {"mfa_enabled": False, "mfa_secret": None}


def _save_mfa_secret(engine: Any, user_id: int, secret: str) -> None:
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE users SET mfa_secret = :secret WHERE id = :uid
            """), {"secret": secret, "uid": user_id})
    except Exception as exc:
        logger.error("[mfa] _save_mfa_secret: %s", exc)


def _set_mfa_enabled(engine: Any, user_id: int, enabled: bool) -> None:
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE users SET mfa_enabled = :val WHERE id = :uid
            """), {"val": int(enabled), "uid": user_id})
        logger.info("[mfa] MFA %s for user %s", "enabled" if enabled else "disabled", user_id)
    except Exception as exc:
        logger.error("[mfa] _set_mfa_enabled: %s", exc)


# ═════════════════════════════════════════════════════════════════════════════
# TOTP helpers
# ═════════════════════════════════════════════════════════════════════════════

def generate_secret() -> str:
    """Generate a new Base32 TOTP secret."""
    if not HAS_PYOTP:
        raise ImportError("pyotp not installed")
    return pyotp.random_base32()


def get_totp_uri(secret: str, username: str) -> str:
    """Return the otpauth:// URI for QR code generation."""
    totp = pyotp.TOTP(secret, digits=MFA_DIGITS, interval=MFA_PERIOD)
    return totp.provisioning_uri(name=username, issuer_name=ISSUER)


def verify_token(secret: str, token: str, valid_window: int = 1) -> bool:
    """Verify a TOTP token. valid_window=1 allows ±30s clock drift."""
    if not HAS_PYOTP or not secret:
        return False
    try:
        totp = pyotp.TOTP(secret, digits=MFA_DIGITS, interval=MFA_PERIOD)
        return totp.verify(token.strip(), valid_window=valid_window)
    except Exception as exc:
        logger.error("[mfa] verify_token: %s", exc)
        return False


def generate_qr_data_url(uri: str) -> Optional[str]:
    """
    Generate QR code as a base64 data URL for embedding in HTML.
    Returns None if qrcode is not installed.
    """
    if not HAS_QRCODE:
        return None
    try:
        img = qrcode.make(uri)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f"data:image/png;base64,{b64}"
    except Exception as exc:
        logger.error("[mfa] generate_qr_data_url: %s", exc)
        return None


# ═════════════════════════════════════════════════════════════════════════════
# Flask routes
# ═════════════════════════════════════════════════════════════════════════════

def register_mfa_routes(flask_app: Any, engine: Any) -> None:
    """
    Register /mfa/* Flask routes.
    Call after flask_app (app.server) is ready.
    """
    from flask import request, redirect, session, jsonify, render_template_string
    from flask_login import current_user, login_required

    # ── MFA Setup page ────────────────────────────────────────────────────
    @flask_app.route("/mfa/setup", methods=["GET", "POST"])
    @login_required
    def mfa_setup():
        if not HAS_PYOTP:
            return "<h3>pyotp not installed. Run: pip install pyotp qrcode[pil]</h3>", 503

        user_id  = current_user.id
        username = current_user.username

        if request.method == "POST":
            token  = request.form.get("token", "").strip()
            secret = session.get("mfa_pending_secret")
            if not secret:
                return redirect("/mfa/setup")
            if verify_token(secret, token):
                _save_mfa_secret(engine, user_id, secret)
                _set_mfa_enabled(engine, user_id, True)
                session.pop("mfa_pending_secret", None)
                session["mfa_verified"] = True
                return redirect("/mfa/setup-complete")
            else:
                error = "Invalid code. Please try again."
                # Re-render with error
                secret = session.get("mfa_pending_secret", generate_secret())
                session["mfa_pending_secret"] = secret
        else:
            error  = None
            record = _get_mfa_record(engine, user_id)
            if record["mfa_enabled"]:
                return redirect("/mfa/already-enabled")
            secret = session.get("mfa_pending_secret") or generate_secret()
            session["mfa_pending_secret"] = secret

        uri    = get_totp_uri(secret, username)
        qr_url = generate_qr_data_url(uri) or ""

        return render_template_string(MFA_SETUP_HTML,
                                      secret=secret, qr_url=qr_url,
                                      error=error, uri=uri,
                                      app_name=APP_NAME)

    # ── MFA Verify (during login) ─────────────────────────────────────────
    @flask_app.route("/mfa/verify", methods=["GET", "POST"])
    def mfa_verify():
        pending_user_id = session.get("mfa_pending_user_id")
        if not pending_user_id:
            return redirect("/login")

        error = None
        if request.method == "POST":
            token  = request.form.get("token", "").strip()
            # Load user and verify
            try:
                from sqlalchemy import text
                with engine.connect() as conn:
                    row = conn.execute(text("""
                        SELECT mfa_secret FROM users WHERE id = :uid
                    """), {"uid": pending_user_id}).fetchone()
                secret = row[0] if row else None
            except Exception:
                secret = None

            if secret and verify_token(secret, token):
                session.pop("mfa_pending_user_id", None)
                session["mfa_verified"] = True
                next_url = session.pop("mfa_next_url", "/")
                # Complete flask-login
                from flask_login import login_user
                from auth import get_user_by_id
                user = get_user_by_id(pending_user_id)
                if user:
                    login_user(user, remember=True)
                return redirect(next_url)
            else:
                error = "Invalid or expired code. Please try again."

        return render_template_string(MFA_VERIFY_HTML, error=error, app_name=APP_NAME)

    # ── Setup complete ────────────────────────────────────────────────────
    @flask_app.route("/mfa/setup-complete")
    @login_required
    def mfa_setup_complete():
        return render_template_string(MFA_COMPLETE_HTML, app_name=APP_NAME)

    # ── Already enabled ───────────────────────────────────────────────────
    @flask_app.route("/mfa/already-enabled")
    @login_required
    def mfa_already_enabled():
        return render_template_string(MFA_ALREADY_HTML, app_name=APP_NAME)

    # ── Disable MFA ───────────────────────────────────────────────────────
    @flask_app.route("/mfa/disable", methods=["POST"])
    @login_required
    def mfa_disable():
        token = request.form.get("token", "").strip()
        rec   = _get_mfa_record(engine, current_user.id)
        if rec["mfa_secret"] and verify_token(rec["mfa_secret"], token):
            _set_mfa_enabled(engine, current_user.id, False)
            return redirect("/?mfa=disabled")
        return render_template_string(MFA_DISABLE_HTML,
                                      error="Invalid code.", app_name=APP_NAME)

    @flask_app.route("/mfa/disable", methods=["GET"])
    @login_required
    def mfa_disable_page():
        return render_template_string(MFA_DISABLE_HTML, error=None, app_name=APP_NAME)

    logger.info("[mfa] MFA routes registered.")


# ═════════════════════════════════════════════════════════════════════════════
# HTML Templates (minimal, brand-consistent)
# ═════════════════════════════════════════════════════════════════════════════

_BASE_STYLE = """
<style>
  body{margin:0;padding:0;background:#f4f6f9;font-family:Inter,Arial,sans-serif}
  .card{background:#fff;border-radius:14px;box-shadow:0 2px 12px rgba(0,0,0,0.08);
        padding:2rem 2.5rem;max-width:460px;margin:3rem auto}
  h2{color:#1e7e4b;font-weight:700;margin-bottom:0.25rem}
  p{color:#6b7280;font-size:0.88rem;line-height:1.6;margin-bottom:1rem}
  input[type=text]{width:100%;padding:0.65rem 1rem;border:1.5px solid #e2e8f0;
    border-radius:8px;font-size:1.1rem;letter-spacing:0.18em;
    text-align:center;outline:none;box-sizing:border-box}
  input[type=text]:focus{border-color:#1e7e4b}
  button,a.btn{display:inline-block;background:#1e7e4b;color:#fff;border:none;
    padding:0.65rem 1.5rem;border-radius:8px;font-size:0.9rem;font-weight:600;
    cursor:pointer;text-decoration:none;margin-top:0.5rem}
  .secret{font-family:monospace;background:#f0fdf4;padding:0.4rem 0.8rem;
           border-radius:6px;font-size:0.9rem;letter-spacing:0.08em;color:#1e7e4b}
  .err{color:#dc3545;font-size:0.82rem;margin-bottom:0.5rem}
  img.qr{display:block;margin:1rem auto;border-radius:8px;
         box-shadow:0 1px 6px rgba(0,0,0,0.1)}
</style>
"""

MFA_SETUP_HTML = _BASE_STYLE + """
<div class="card">
  <h2>🔐 Set Up Two-Factor Auth</h2>
  <p>Scan this QR code with Google Authenticator, Authy, or any TOTP app.</p>
  {% if qr_url %}
  <img src="{{ qr_url }}" width="200" height="200" class="qr" alt="QR Code">
  {% else %}
  <p>Can't show QR? Use this code manually in your authenticator app:</p>
  {% endif %}
  <p>Manual entry code: <span class="secret">{{ secret }}</span></p>
  <form method="POST">
    {% if error %}<div class="err">{{ error }}</div>{% endif %}
    <p style="margin-top:1rem">Enter the 6-digit code from your app to verify:</p>
    <input type="text" name="token" maxlength="6" placeholder="000000" autocomplete="off" autofocus>
    <br>
    <button type="submit">Verify & Enable MFA</button>
  </form>
</div>
"""

MFA_VERIFY_HTML = _BASE_STYLE + """
<div class="card">
  <h2>🔐 Enter Your 2FA Code</h2>
  <p>Open your authenticator app and enter the current 6-digit code for {{ app_name }}.</p>
  <form method="POST">
    {% if error %}<div class="err">{{ error }}</div>{% endif %}
    <input type="text" name="token" maxlength="6" placeholder="000000" autocomplete="off" autofocus>
    <br>
    <button type="submit">Verify</button>
  </form>
</div>
"""

MFA_COMPLETE_HTML = _BASE_STYLE + """
<div class="card" style="text-align:center">
  <div style="font-size:3rem;margin-bottom:0.5rem">✅</div>
  <h2>MFA Enabled!</h2>
  <p>Your account is now protected with two-factor authentication.
     You'll need your authenticator app on every login.</p>
  <a class="btn" href="/">Go to Dashboard</a>
</div>
"""

MFA_ALREADY_HTML = _BASE_STYLE + """
<div class="card" style="text-align:center">
  <div style="font-size:3rem;margin-bottom:0.5rem">🔒</div>
  <h2>MFA Already Active</h2>
  <p>Two-factor authentication is already enabled on your account.</p>
  <a class="btn" href="/">Dashboard</a>
  <a class="btn" href="/mfa/disable" style="background:#dc3545;margin-left:0.5rem">Disable MFA</a>
</div>
"""

MFA_DISABLE_HTML = _BASE_STYLE + """
<div class="card">
  <h2>Disable Two-Factor Auth</h2>
  <p>Enter your current authenticator code to confirm and disable MFA.</p>
  <form method="POST">
    {% if error %}<div class="err">{{ error }}</div>{% endif %}
    <input type="text" name="token" maxlength="6" placeholder="000000" autocomplete="off">
    <br>
    <button type="submit" style="background:#dc3545">Disable MFA</button>
    <a class="btn" href="/" style="background:#6b7280;margin-left:0.5rem">Cancel</a>
  </form>
</div>
"""


# ═════════════════════════════════════════════════════════════════════════════
# Dash tab widget — MFA settings card (embedded in a profile/settings tab)
# ═════════════════════════════════════════════════════════════════════════════

def render_mfa_settings_card(user_id: int, engine: Any) -> html.Div:
    """Render a compact MFA status card for embedding in profile/settings."""
    rec     = _get_mfa_record(engine, user_id)
    enabled = rec.get("mfa_enabled", False)

    if enabled:
        status = html.Div([
            html.Span("🔒 Enabled", style={"color":"#1e7e4b","fontWeight":700}),
            html.P("Your account is protected with TOTP two-factor authentication.",
                   style={"fontSize":"0.8rem","color":"#6b7280","marginTop":"4px"}),
            dbc.Button("Disable MFA", href="/mfa/disable", color="danger",
                       size="sm", outline=True, style={"fontSize":"0.78rem"}),
        ])
    else:
        status = html.Div([
            html.Span("⚠️ Not Enabled", style={"color":"#fd7e14","fontWeight":700}),
            html.P("Add an extra layer of security with an authenticator app.",
                   style={"fontSize":"0.8rem","color":"#6b7280","marginTop":"4px"}),
            dbc.Button("Enable MFA", href="/mfa/setup", color="success",
                       size="sm", style={"fontSize":"0.78rem","fontWeight":600}),
        ])

    if not HAS_PYOTP:
        status = html.Div([
            html.Span("⚠️ MFA unavailable",
                      style={"color":"#dc3545","fontWeight":700}),
            html.P("Install pyotp to enable MFA: pip install pyotp qrcode[pil]",
                   style={"fontSize":"0.78rem","color":"#6b7280","fontFamily":"monospace"}),
        ])

    return html.Div([
        html.Div("Two-Factor Authentication",
                 style={"fontWeight":700,"fontSize":"0.88rem","marginBottom":"0.5rem"}),
        status,
    ], style={"background":"#fff","borderRadius":"10px",
              "padding":"1.2rem","boxShadow":"0 1px 4px rgba(0,0,0,0.07)"})
