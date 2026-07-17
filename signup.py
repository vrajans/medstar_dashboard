"""
signup.py — InsightHub Self-Serve Signup
Provides /signup GET (form) and POST (create account).
BRD US-301 — 14-day free trial, no credit card required.

Creates a new tenant + admin user, sends welcome email,
then redirects to the dashboard pre-logged-in.
"""

import os
import re
import logging
import hashlib
import secrets
from datetime import datetime, timedelta
from sqlalchemy import text

log = logging.getLogger("signup")


SIGNUP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>InsightHub — Start Free Trial</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: Inter, -apple-system, BlinkMacSystemFont, sans-serif;
         background: #F8FAFC; color: #1E293B; min-height: 100vh;
         display: flex; flex-direction: column; }
  .nav { background: #1E293B; padding: 14px 32px; display: flex;
         align-items: center; justify-content: space-between; }
  .nav-logo { color: #fff; font-size: 17px; font-weight: 700; letter-spacing: -0.3px; }
  .nav-link  { color: #93C5FD; font-size: 13px; text-decoration: none; }
  .main { flex: 1; display: flex; align-items: center; justify-content: center;
          padding: 40px 24px; }
  .card { background: #fff; border: 1px solid #E2E8F0; border-radius: 16px;
          padding: 40px 36px; width: 100%; max-width: 460px; }
  .card-header { margin-bottom: 28px; }
  h1 { font-size: 22px; font-weight: 800; color: #0F172A; margin-bottom: 6px;
       letter-spacing: -0.4px; }
  .sub { font-size: 13px; color: #64748B; }
  .trial-badge { display: inline-flex; align-items: center; gap: 6px;
                 background: #EFF6FF; color: #2563EB; font-size: 12px; font-weight: 600;
                 padding: 4px 12px; border-radius: 100px; margin-bottom: 16px;
                 border: 1px solid #BFDBFE; }
  label { display: block; font-size: 12px; font-weight: 600; color: #374151;
          text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 5px;
          margin-top: 16px; }
  input, select { width: 100%; padding: 10px 14px; border: 1px solid #D1D5DB;
                  border-radius: 8px; font-size: 14px; color: #0F172A;
                  background: #FAFAFA; outline: none; transition: border 0.15s; }
  input:focus, select:focus { border-color: #2563EB; background: #fff;
                               box-shadow: 0 0 0 3px rgba(37,99,235,0.1); }
  .btn { display: block; width: 100%; background: #2563EB; color: #fff;
         padding: 13px 24px; border: none; border-radius: 9px; font-size: 15px;
         font-weight: 700; cursor: pointer; margin-top: 24px; letter-spacing: -0.1px; }
  .btn:hover { background: #1D4ED8; }
  .note { font-size: 12px; color: #94A3B8; text-align: center; margin-top: 14px; }
  .divider { border: none; border-top: 1px solid #F1F5F9; margin: 24px 0; }
  .login-link { text-align: center; font-size: 13px; color: #64748B; }
  .login-link a { color: #2563EB; text-decoration: none; font-weight: 500; }
  .error-box { background: #FEF2F2; border: 1px solid #FECACA; border-radius: 8px;
               padding: 12px 16px; font-size: 13px; color: #DC2626; margin-bottom: 16px; }
  .features { list-style: none; margin: 16px 0; }
  .features li { font-size: 13px; color: #374151; padding: 3px 0;
                 display: flex; align-items: center; gap: 7px; }
  .check { color: #059669; font-weight: 700; }
</style>
</head>
<body>

<nav class="nav">
  <div class="nav-logo">InsightHub</div>
  <a href="/pricing" class="nav-link">See pricing →</a>
</nav>

<div class="main">
<div class="card">
  <div class="card-header">
    <div class="trial-badge">✓ 14-day free trial</div>
    <h1>Start your free trial</h1>
    <div class="sub">No credit card required. Full access, instant setup.</div>
  </div>

  {error_block}

  <form method="POST" action="/signup">
    <label>Business name</label>
    <input type="text" name="business_name" placeholder="Acme Corp" required
           value="{business_name}" autocomplete="organization">

    <label>Your name</label>
    <input type="text" name="full_name" placeholder="Jane Smith" required
           value="{full_name}" autocomplete="name">

    <label>Work email</label>
    <input type="email" name="email" placeholder="jane@company.com" required
           value="{email}" autocomplete="email">

    <label>Password</label>
    <input type="password" name="password" placeholder="At least 8 characters" required
           autocomplete="new-password">

    <label>Business type</label>
    <select name="domain_type">
      <option value="professional_services" {sel_ps}>Professional Services / Consulting</option>
      <option value="retail" {sel_rt}>Retail / E-commerce</option>
      <option value="fnb" {sel_fb}>Food &amp; Beverage / Restaurant</option>
      <option value="manufacturing" {sel_mf}>Manufacturing / Distribution</option>
      <option value="finance" {sel_fi}>Finance / Accounting</option>
      <option value="generic" {sel_gn}>Other / General</option>
    </select>

    <label>Country</label>
    <select name="country">
      <option value="US">United States</option>
      <option value="CA">Canada</option>
      <option value="GB">United Kingdom</option>
      <option value="AU">Australia</option>
      <option value="IN">India</option>
      <option value="SG">Singapore</option>
      <option value="OTHER">Other</option>
    </select>

    <button type="submit" class="btn">Create free account →</button>
  </form>

  <p class="note">By signing up you agree to our Terms of Service and Privacy Policy.</p>

  <hr class="divider">
  <div class="login-link">
    Already have an account? <a href="/">Sign in</a>
  </div>
</div>
</div>

</body>
</html>"""


WELCOME_EMAIL_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Welcome to InsightHub!</title></head>
<body style="margin:0;padding:0;background:#F1F5F9;font-family:Inter,Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="padding:32px 0">
<tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%">
  <tr>
    <td style="background:#1E293B;border-radius:10px 10px 0 0;padding:22px 28px">
      <div style="font-size:17px;font-weight:700;color:#fff">InsightHub</div>
      <div style="font-size:11px;color:#94A3B8;margin-top:2px">Business Analytics Platform</div>
    </td>
  </tr>
  <tr>
    <td style="background:#fff;padding:32px 28px;border-radius:0 0 10px 10px;
               border:1px solid #E2E8F0;border-top:none">
      <p style="font-size:22px;font-weight:800;color:#0F172A;margin:0 0 8px;letter-spacing:-0.4px">
        Welcome, {first_name}! 🎉
      </p>
      <p style="font-size:14px;color:#64748B;margin:0 0 24px">
        Your 14-day free trial of InsightHub has started for <strong>{business_name}</strong>.
      </p>
      <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:28px">
        <tr>
          <td style="background:#EFF6FF;border:1px solid #BFDBFE;border-top:3px solid #2563EB;
                     border-radius:8px;padding:16px;text-align:center">
            <div style="font-size:12px;color:#64748B;text-transform:uppercase;
                        letter-spacing:0.06em;margin-bottom:4px">Trial ends</div>
            <div style="font-size:20px;font-weight:700;color:#2563EB">{trial_end}</div>
          </td>
        </tr>
      </table>
      <p style="font-size:14px;font-weight:600;color:#1E293B;margin:0 0 10px">
        Here's how to get started:
      </p>
      <table style="margin-bottom:24px">
        <tr><td style="padding:4px 0;font-size:13px;color:#334155">
          <span style="color:#059669;font-weight:700">1.</span>
          Upload your first data file (CSV or Excel) from the Upload tab
        </td></tr>
        <tr><td style="padding:4px 0;font-size:13px;color:#334155">
          <span style="color:#059669;font-weight:700">2.</span>
          Your revenue, customer, and trend charts appear instantly
        </td></tr>
        <tr><td style="padding:4px 0;font-size:13px;color:#334155">
          <span style="color:#059669;font-weight:700">3.</span>
          You'll receive weekly email reports every Monday morning
        </td></tr>
      </table>
      <div style="text-align:center;margin-bottom:24px">
        <a href="{dashboard_url}" style="display:inline-block;background:#2563EB;color:#fff;
           text-decoration:none;font-size:14px;font-weight:600;padding:12px 28px;
           border-radius:8px">Go to your dashboard →</a>
      </div>
      <p style="font-size:12px;color:#94A3B8;text-align:center;margin:0">
        Questions? Reply to this email or contact
        <a href="mailto:support@insighthub.io" style="color:#2563EB">support@insighthub.io</a>
      </p>
    </td>
  </tr>
  <tr>
    <td style="padding:16px 0;text-align:center;font-size:11px;color:#94A3B8">
      © 2026 InsightHub
    </td>
  </tr>
</table>
</td></tr>
</table>
</body>
</html>"""


# ── Helpers ───────────────────────────────────────────────────

def _slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s)
    s = re.sub(r"^-+|-+$", "", s)
    return s[:50] or "tenant"


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h    = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    return f"{salt}:{h}"


def _country_to_currency(country: str) -> str:
    m = {"US": "USD", "CA": "CAD", "GB": "GBP", "AU": "AUD",
         "IN": "INR", "SG": "SGD", "EU": "EUR"}
    return m.get(country.upper(), "USD")


def _send_welcome_email(email: str, first_name: str, business_name: str, trial_end: str, base_url: str):
    """Fire-and-forget welcome email using email_reports SMTP config."""
    try:
        from email_reports import send_email, SMTP_USER
        if not SMTP_USER:
            log.info(f"[signup] SMTP not configured — skipping welcome email to {email}")
            return
        body = WELCOME_EMAIL_HTML.format(
            first_name=first_name,
            business_name=business_name,
            trial_end=trial_end,
            dashboard_url=base_url,
        )
        send_email(email, f"Welcome to InsightHub — your trial has started!", body, "InsightHub")
    except Exception as exc:
        log.warning(f"[signup] Welcome email failed: {exc}")


# ── Database: create tenant + admin user ──────────────────────

CURRENCY_COUNTRY_MAP = {"US": "USD", "CA": "CAD", "GB": "GBP", "AU": "AUD",
                        "IN": "INR", "SG": "SGD"}

def _create_tenant_and_user(engine, business_name: str, full_name: str,
                             email: str, password: str,
                             domain_type: str, country: str) -> dict:
    """
    Create a new local_tenants row + an auth user row.
    Returns {"tenant_id": int, "user_id": int} or raises on duplicate email/slug.
    """
    slug     = _slugify(business_name)
    currency = CURRENCY_COUNTRY_MAP.get(country.upper(), "USD")
    trial_ends = (datetime.utcnow() + timedelta(days=14)).strftime("%Y-%m-%d")
    pw_hash  = _hash_password(password)

    with engine.connect() as conn:
        # Ensure trial columns exist
        for col, default in [
            ("stripe_status", "'trial'"),
            ("trial_ends_at", "NULL"),
            ("email_reports",  "1"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE local_tenants ADD COLUMN {col} TEXT DEFAULT {default}"))
                conn.commit()
            except Exception:
                pass

        # Unique slug
        base_slug = slug
        for i in range(1, 20):
            exists = conn.execute(
                text("SELECT 1 FROM local_tenants WHERE slug=:s"), {"s": slug}
            ).fetchone()
            if not exists:
                break
            slug = f"{base_slug}-{i}"

        # Insert tenant
        conn.execute(text("""
            INSERT INTO local_tenants
              (name, slug, domain_type, plan, contact_email, country, currency,
               stripe_status, trial_ends_at, email_reports)
            VALUES
              (:name, :slug, :dtype, 'starter_trial', :email, :country, :cur,
               'trial', :trial_ends, 1)
        """), {
            "name": business_name, "slug": slug, "dtype": domain_type,
            "email": email, "country": country, "cur": currency,
            "trial_ends": trial_ends,
        })
        conn.commit()

        tenant_id = conn.execute(
            text("SELECT id FROM local_tenants WHERE slug=:s"), {"s": slug}
        ).scalar()

        # Insert user (into the users table used by auth.py)
        try:
            conn.execute(text("""
                INSERT INTO users (username, email, password_hash, role, tenant_id, active)
                VALUES (:uname, :email, :pw, 'tenant_admin', :tid, 1)
            """), {
                "uname": email, "email": email, "pw": pw_hash, "tid": tenant_id,
            })
            conn.commit()
        except Exception as user_err:
            # users table may have different schema — try alternate
            log.warning(f"[signup] User insert attempt 1 failed: {user_err}")
            try:
                conn.execute(text("""
                    INSERT INTO users (username, password_hash, role, tenant_id)
                    VALUES (:uname, :pw, 'tenant_admin', :tid)
                """), {"uname": email, "pw": pw_hash, "tid": tenant_id})
                conn.commit()
            except Exception as user_err2:
                log.warning(f"[signup] User insert attempt 2 failed: {user_err2}")

        user_id = conn.execute(
            text("SELECT id FROM users WHERE username=:u OR email=:u LIMIT 1"),
            {"u": email}
        ).scalar()

    return {"tenant_id": tenant_id, "user_id": user_id, "trial_ends": trial_ends, "slug": slug}


# ── Route registration ────────────────────────────────────────

def register_signup_routes(app_server, engine, base_url: str = "http://localhost:8050"):
    """Register /signup GET + POST routes."""
    from flask import request, redirect, make_response, session as flask_session

    def _render_form(error="", business_name="", full_name="", email="", domain_type="professional_services"):
        err_html = f'<div class="error-box">{error}</div>' if error else ""
        opts = {
            "professional_services": "", "retail": "", "fnb": "",
            "manufacturing": "", "finance": "", "generic": "",
        }
        if domain_type in opts:
            opts[domain_type] = 'selected="selected"'
        html = SIGNUP_HTML.replace("{error_block}", err_html)
        for k, v in [("{business_name}", business_name), ("{full_name}", full_name),
                     ("{email}", email)]:
            html = html.replace(k, v)
        for dt, sel_key in [
            ("professional_services", "{sel_ps}"), ("retail", "{sel_rt}"),
            ("fnb", "{sel_fb}"), ("manufacturing", "{sel_mf}"),
            ("finance", "{sel_fi}"), ("generic", "{sel_gn}"),
        ]:
            html = html.replace(sel_key, opts.get(dt, ""))
        return html

    @app_server.route("/signup", methods=["GET"])
    def signup_get():
        r = make_response(_render_form())
        r.headers["Content-Type"] = "text/html; charset=utf-8"
        return r

    @app_server.route("/signup", methods=["POST"])
    def signup_post():
        business_name = request.form.get("business_name", "").strip()
        full_name     = request.form.get("full_name", "").strip()
        email         = request.form.get("email", "").strip().lower()
        password      = request.form.get("password", "")
        domain_type   = request.form.get("domain_type", "generic")
        country       = request.form.get("country", "US")

        # Validation
        if not business_name:
            r = make_response(_render_form("Please enter your business name.", business_name, full_name, email, domain_type))
            r.headers["Content-Type"] = "text/html; charset=utf-8"
            return r
        if not email or "@" not in email:
            r = make_response(_render_form("Please enter a valid email address.", business_name, full_name, email, domain_type))
            r.headers["Content-Type"] = "text/html; charset=utf-8"
            return r
        if len(password) < 8:
            r = make_response(_render_form("Password must be at least 8 characters.", business_name, full_name, email, domain_type))
            r.headers["Content-Type"] = "text/html; charset=utf-8"
            return r

        # Check email not already registered
        try:
            with engine.connect() as conn:
                existing = conn.execute(
                    text("SELECT 1 FROM users WHERE username=:e OR email=:e LIMIT 1"),
                    {"e": email}
                ).fetchone()
                if existing:
                    r = make_response(_render_form(
                        "An account with this email already exists. "
                        '<a href="/">Sign in instead</a>.',
                        business_name, full_name, email, domain_type,
                    ))
                    r.headers["Content-Type"] = "text/html; charset=utf-8"
                    return r
        except Exception:
            pass

        # Create account
        try:
            result = _create_tenant_and_user(
                engine, business_name, full_name, email, password, domain_type, country
            )
        except Exception as exc:
            log.error(f"[signup] Account creation error: {exc}")
            r = make_response(_render_form(
                "Account creation failed. Please try again or contact support.",
                business_name, full_name, email, domain_type,
            ))
            r.headers["Content-Type"] = "text/html; charset=utf-8"
            return r

        # Send welcome email (non-blocking)
        first_name = full_name.split()[0] if full_name else email.split("@")[0].capitalize()
        trial_end_pretty = datetime.strptime(result["trial_ends"], "%Y-%m-%d").strftime("%B %d, %Y")
        import threading
        threading.Thread(
            target=_send_welcome_email,
            args=(email, first_name, business_name, trial_end_pretty, base_url),
            daemon=True,
        ).start()

        log.info(f"[signup] New account: '{business_name}' (tenant={result['tenant_id']}, email={email})")

        # Auto-login: set Flask session so the user lands logged in
        flask_session["signup_email"]     = email
        flask_session["signup_tenant_id"] = result["tenant_id"]
        flask_session["signup_just_done"] = True

        # Redirect to dashboard with a welcome flag
        return redirect(f"/?welcome=1&tenant={result['tenant_id']}")

    log.info("[signup] Routes registered: /signup GET, /signup POST")
