"""
ccpa.py  —  InsightHub CCPA / Privacy Compliance (US-306)
=========================================================
Implements the minimum required for US launch:
  1. GET  /privacy            — full privacy policy page
  2. POST /data-deletion      — CCPA "Right to Delete" request
  3. GET  /cookie-consent.js  — cookie consent banner script

Cookie consent banner is injected via a tiny JS snippet that:
  - Shows a bottom-of-screen banner on first visit
  - Sets a cookie (ih_consent=1) when user accepts
  - Hides permanently once accepted

Usage (in app.py):
  from ccpa import register_ccpa_routes
  register_ccpa_routes(app.server, auth_engine)
"""

import os
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

APP_NAME    = os.getenv("APP_NAME",           "InsightHub")
APP_URL     = os.getenv("APP_URL",            "http://localhost:8050")
DPO_EMAIL   = os.getenv("DPO_EMAIL",         "privacy@insighthub.ai")
SENDGRID_API_KEY  = os.getenv("SENDGRID_API_KEY",  "")
SENDGRID_FROM     = os.getenv("SENDGRID_FROM_EMAIL","noreply@insighthub.ai")

# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def init_ccpa_tables(engine) -> None:
    """Create data_deletion_requests table."""
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS data_deletion_requests (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    email       TEXT    NOT NULL,
                    username    TEXT,
                    user_id     INTEGER,
                    reason      TEXT,
                    status      TEXT    DEFAULT 'pending',
                    requested_at TEXT   DEFAULT (datetime('now')),
                    completed_at TEXT
                )
            """))
        logger.info("[ccpa] data_deletion_requests table ensured.")
    except Exception as exc:
        logger.error("[ccpa] init_ccpa_tables: %s", exc)


def _log_deletion_request(engine, email: str, username: str,
                           user_id: int | None, reason: str) -> int:
    """Insert a deletion request row; return its ID."""
    from sqlalchemy import text
    with engine.begin() as conn:
        cur = conn.execute(text("""
            INSERT INTO data_deletion_requests (email, username, user_id, reason)
            VALUES (:email, :username, :uid, :reason)
        """), {"email": email, "username": username, "uid": user_id, "reason": reason})
        return cur.lastrowid


def _notify_dpo(email: str, username: str, request_id: int, reason: str) -> None:
    """Email DPO team about the deletion request (fire-and-forget)."""
    if not SENDGRID_API_KEY:
        logger.info("[ccpa] DPO notification (no SendGrid): request #%s from %s", request_id, email)
        return
    try:
        import urllib.request
        body = (
            f"New CCPA Data Deletion Request #{request_id}\n\n"
            f"User email:  {email}\n"
            f"Username:    {username}\n"
            f"Reason:      {reason or 'Not specified'}\n"
            f"Requested:   {datetime.now(timezone.utc).isoformat()}\n\n"
            f"Please process within 45 days as required by CCPA.\n"
            f"Log in to {APP_URL}/admin to update the request status."
        )
        payload = {
            "personalizations": [{"to": [{"email": DPO_EMAIL}]}],
            "from": {"email": SENDGRID_FROM, "name": APP_NAME},
            "subject": f"CCPA Deletion Request #{request_id} — {email}",
            "content": [{"type": "text/plain", "value": body}],
        }
        req = urllib.request.Request(
            "https://api.sendgrid.com/v3/mail/send",
            data    = json.dumps(payload).encode(),
            headers = {"Authorization": f"Bearer {SENDGRID_API_KEY}",
                       "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as exc:
        logger.error("[ccpa] _notify_dpo: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# HTML Templates
# ─────────────────────────────────────────────────────────────────────────────

_BASE_STYLE = """
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:Inter,-apple-system,BlinkMacSystemFont,sans-serif;
     background:#F8FAFC;color:#1E293B;line-height:1.7;
     -webkit-font-smoothing:antialiased}
.nav{background:#1E293B;padding:14px 32px;display:flex;
     align-items:center;gap:12px}
.nav-logo{font-size:1.1rem;font-weight:800;color:#fff;text-decoration:none}
.nav-back{margin-left:auto;font-size:0.8rem;color:#94A3B8;
          text-decoration:none}
.nav-back:hover{color:#fff}
.container{max-width:820px;margin:0 auto;padding:48px 24px}
h1{font-size:2rem;font-weight:800;color:#1E293B;margin-bottom:8px}
.subtitle{font-size:0.9rem;color:#64748B;margin-bottom:36px}
h2{font-size:1.15rem;font-weight:700;color:#1E293B;margin:32px 0 8px}
p,li{font-size:0.92rem;color:#475569;margin-bottom:8px}
ul{padding-left:1.4rem;margin-bottom:8px}
a{color:#2563EB;text-decoration:none}
a:hover{text-decoration:underline}
.card{background:#fff;border:1px solid #E2E8F0;border-radius:12px;
      padding:24px 28px;margin-bottom:20px}
.highlight{background:#EFF6FF;border-left:4px solid #2563EB;
           border-radius:0 8px 8px 0;padding:12px 16px;
           font-size:0.85rem;color:#1E40AF;margin:16px 0}
table{width:100%;border-collapse:collapse;font-size:0.85rem;margin:12px 0}
th{background:#F1F5F9;padding:8px 12px;text-align:left;font-weight:600;
   border:1px solid #E2E8F0}
td{padding:8px 12px;border:1px solid #E2E8F0}
.footer{text-align:center;font-size:0.78rem;color:#94A3B8;
        padding:32px 0 48px;border-top:1px solid #E2E8F0;margin-top:48px}
</style>"""

_NAV = f"""
<nav class="nav">
  <a href="/" class="nav-logo">&#x1F4CA; {APP_NAME}</a>
  <a href="/" class="nav-back">&larr; Back to Dashboard</a>
</nav>"""

PRIVACY_PAGE = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>Privacy Policy — {APP_NAME}</title>
{_BASE_STYLE}
</head>
<body>
{_NAV}
<div class="container">
  <h1>Privacy Policy</h1>
  <p class="subtitle">
    Last updated: July 18, 2026 &nbsp;·&nbsp;
    Effective: July 18, 2026
  </p>

  <div class="highlight">
    <strong>{APP_NAME}</strong> is committed to protecting your privacy.
    This policy explains what data we collect, how we use it, and your rights
    under the California Consumer Privacy Act (CCPA), GDPR, and other applicable laws.
  </div>

  <div class="card">
    <h2>1. Who We Are</h2>
    <p>{APP_NAME} ("we", "us", "our") is a business analytics SaaS platform
    operated by InsightHub Technologies, Inc. For privacy inquiries, contact us at
    <a href="mailto:{DPO_EMAIL}">{DPO_EMAIL}</a>.</p>
  </div>

  <div class="card">
    <h2>2. What Data We Collect</h2>
    <table>
      <tr><th>Category</th><th>Examples</th><th>Purpose</th></tr>
      <tr><td>Account data</td><td>Name, email, username, company name</td><td>Authentication, billing, support</td></tr>
      <tr><td>Business data you upload</td><td>Sales CSVs, purchase reports, inventory files</td><td>Analytics dashboard — never shared with third parties</td></tr>
      <tr><td>Usage data</td><td>Pages visited, tabs clicked, features used</td><td>Product improvement, bug fixing</td></tr>
      <tr><td>Payment data</td><td>Billing address, last 4 digits of card (via Stripe)</td><td>Subscription billing — full card number never stored by us</td></tr>
      <tr><td>Technical data</td><td>IP address, browser type, session cookies</td><td>Security, fraud prevention</td></tr>
    </table>
    <p>We do <strong>not</strong> sell, rent, or share your business data with any third party
    for advertising or marketing purposes.</p>
  </div>

  <div class="card">
    <h2>3. How We Use Your Data</h2>
    <ul>
      <li>Providing and improving the {APP_NAME} dashboard and analytics features</li>
      <li>Sending weekly email digests and configurable alerts (SMS, WhatsApp, email)</li>
      <li>Billing and subscription management via Stripe</li>
      <li>Responding to support requests and security incidents</li>
      <li>Complying with legal obligations</li>
    </ul>
  </div>

  <div class="card">
    <h2>4. Data Retention</h2>
    <p>We retain your uploaded business data for as long as your subscription is active.
    Upon subscription cancellation or account deletion request, we delete all uploaded data
    within <strong>30 days</strong> and account data within <strong>45 days</strong>.</p>
    <p>Anonymized, aggregated analytics (with no personally identifiable information)
    may be retained for product improvement purposes.</p>
  </div>

  <div class="card">
    <h2>5. Cookies</h2>
    <p>We use the following cookies:</p>
    <table>
      <tr><th>Cookie</th><th>Purpose</th><th>Duration</th></tr>
      <tr><td><code>session</code></td><td>Authentication session (Flask-Login)</td><td>Until logout</td></tr>
      <tr><td><code>ih_consent</code></td><td>Records that you accepted this cookie notice</td><td>1 year</td></tr>
    </table>
    <p>We do <strong>not</strong> use third-party advertising or tracking cookies.</p>
  </div>

  <div class="card">
    <h2>6. Your Rights (CCPA &amp; GDPR)</h2>
    <ul>
      <li><strong>Right to Know:</strong> You can request a copy of all personal data we hold about you.</li>
      <li><strong>Right to Delete:</strong> You can request deletion of your account and all associated data.</li>
      <li><strong>Right to Opt-Out:</strong> We do not sell personal data. No opt-out is needed.</li>
      <li><strong>Right to Correct:</strong> You can update your account information at any time.</li>
      <li><strong>Right to Non-Discrimination:</strong> Exercising your privacy rights will not affect your service level.</li>
    </ul>
    <p>To exercise any right, email <a href="mailto:{DPO_EMAIL}">{DPO_EMAIL}</a> or use the
    <a href="/data-deletion">Data Deletion Request</a> form below.
    We will respond within <strong>45 days</strong> as required by CCPA.</p>
  </div>

  <div class="card">
    <h2>7. Data Security</h2>
    <p>We protect your data with:</p>
    <ul>
      <li>TLS 1.3 encryption in transit</li>
      <li>bcrypt password hashing (cost factor 12)</li>
      <li>Optional TOTP multi-factor authentication</li>
      <li>Tenant-level row isolation in our database</li>
      <li>Session expiry and secure cookie flags</li>
    </ul>
  </div>

  <div class="card">
    <h2>8. Third-Party Services</h2>
    <table>
      <tr><th>Service</th><th>Purpose</th><th>Their Privacy Policy</th></tr>
      <tr><td>Stripe</td><td>Payment processing</td><td><a href="https://stripe.com/privacy" target="_blank">stripe.com/privacy</a></td></tr>
      <tr><td>SendGrid (Twilio)</td><td>Transactional email</td><td><a href="https://www.twilio.com/en-us/legal/privacy" target="_blank">twilio.com/privacy</a></td></tr>
      <tr><td>Twilio</td><td>SMS alerts (optional)</td><td><a href="https://www.twilio.com/en-us/legal/privacy" target="_blank">twilio.com/privacy</a></td></tr>
      <tr><td>Groq</td><td>AI Chat inference (optional)</td><td><a href="https://groq.com/privacy-policy/" target="_blank">groq.com/privacy-policy</a></td></tr>
    </table>
  </div>

  <div class="card">
    <h2>9. Contact Us</h2>
    <p>Privacy inquiries: <a href="mailto:{DPO_EMAIL}">{DPO_EMAIL}</a></p>
    <p>Mailing address: InsightHub Technologies, Inc., Privacy Team, [Address on file]</p>
    <p>To submit a data deletion request: <a href="/data-deletion">Click here</a></p>
  </div>

  <div class="footer">
    &copy; 2026 InsightHub Technologies, Inc. &nbsp;·&nbsp;
    <a href="/privacy">Privacy Policy</a> &nbsp;·&nbsp;
    <a href="/data-deletion">Delete My Data</a> &nbsp;·&nbsp;
    <a href="/login">Sign In</a>
  </div>
</div>
</body>
</html>"""


_DEL_STYLE = """
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:Inter,-apple-system,sans-serif;background:#0F172A;
     min-height:100vh;display:flex;align-items:center;
     justify-content:center;-webkit-font-smoothing:antialiased}
.wrap{width:100%;max-width:500px;padding:24px}
.logo{text-align:center;margin-bottom:28px}
.logo-icon{display:inline-flex;align-items:center;justify-content:center;
  width:48px;height:48px;background:linear-gradient(135deg,#2563EB,#0EA5E9);
  border-radius:14px;font-size:22px;margin-bottom:12px}
.logo-name{font-size:1.4rem;font-weight:800;color:#fff}
.card{background:#1E293B;border:1px solid rgba(255,255,255,0.08);
  border-radius:16px;padding:32px;box-shadow:0 24px 64px rgba(0,0,0,0.4)}
.card-title{font-size:1.05rem;font-weight:700;color:#F1F5F9;margin-bottom:4px}
.card-sub{font-size:0.8rem;color:#64748B;margin-bottom:24px;line-height:1.5}
label{display:block;font-size:0.69rem;font-weight:600;text-transform:uppercase;
  letter-spacing:.07em;color:#94A3B8;margin-bottom:5px;margin-top:16px}
input,textarea,select{width:100%;padding:10px 14px;font-size:0.88rem;
  background:#0F172A;border:1px solid rgba(255,255,255,0.10);
  border-radius:8px;outline:none;color:#F1F5F9;font-family:inherit;
  transition:border-color 0.15s}
input::placeholder,textarea::placeholder{color:#334155}
input:focus,textarea:focus,select:focus{border-color:#2563EB}
textarea{min-height:80px;resize:vertical}
.btn{display:block;width:100%;margin-top:24px;padding:12px;
  font-size:0.95rem;font-weight:700;
  background:linear-gradient(135deg,#DC2626,#B91C1C);color:#fff;
  border:none;border-radius:9px;cursor:pointer;font-family:inherit}
.btn:hover{opacity:0.88}
.ok{background:rgba(5,150,105,.12);border:1px solid rgba(5,150,105,.3);
  border-radius:8px;color:#6EE7B7;font-size:0.82rem;padding:12px;
  margin-bottom:16px;line-height:1.5}
.warn{background:rgba(220,38,38,.10);border:1px solid rgba(220,38,38,.25);
  border-radius:8px;color:#FCA5A5;font-size:0.8rem;padding:10px 14px;
  margin-bottom:16px}
.divider{border:none;border-top:1px solid rgba(255,255,255,.06);margin:24px 0 16px}
.back{text-align:center;font-size:0.75rem;color:#64748B}
.back a{color:#60A5FA;text-decoration:none}
</style>"""

DELETION_FORM = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>Delete My Data — {APP_NAME}</title>
{_DEL_STYLE}
</head>
<body>
<div class="wrap">
  <div class="logo">
    <div class="logo-icon">&#x1F4CA;</div>
    <div class="logo-name">{APP_NAME}</div>
  </div>
  <div class="card">
    <div class="card-title">Request Data Deletion</div>
    <div class="card-sub">
      Under the California Consumer Privacy Act (CCPA) and GDPR, you have the right
      to request deletion of your personal data and all uploaded business data.
      We will process your request within <strong style="color:#F1F5F9">45 days</strong>.
    </div>
    __SUCCESS__
    __ERROR__
    <form method="POST" action="/data-deletion">
      <label for="email">Email address on your account *</label>
      <input type="email" id="email" name="email" placeholder="you@company.com" required autofocus>
      <label for="reason">Reason for deletion (optional)</label>
      <textarea name="reason" placeholder="e.g. Closing my account, switching tools..."></textarea>
      <div class="warn">
        ⚠️ This action is <strong>irreversible</strong>. All your uploaded data,
        dashboards, and account information will be permanently deleted.
      </div>
      <button type="submit" class="btn">Submit Deletion Request</button>
    </form>
    <hr class="divider">
    <div class="back">
      <a href="/privacy">Privacy Policy</a> &nbsp;·&nbsp;
      <a href="/login">Sign In</a> &nbsp;·&nbsp;
      <a href="mailto:{DPO_EMAIL}">{DPO_EMAIL}</a>
    </div>
  </div>
</div>
</body>
</html>"""

DELETION_SUCCESS = """
<div class="ok">
  ✅ <strong>Request received.</strong><br>
  We will process your data deletion within 45 days and send a confirmation to your email.
  Your reference number is: <strong>#__ID__</strong>
</div>"""

# ─────────────────────────────────────────────────────────────────────────────
# Cookie consent banner JS (injected via /cookie-consent.js route)
# ─────────────────────────────────────────────────────────────────────────────

COOKIE_BANNER_JS = """
(function() {
  if (document.cookie.indexOf('ih_consent=1') !== -1) return;
  var banner = document.createElement('div');
  banner.id = 'ih-cookie-banner';
  banner.style.cssText = [
    'position:fixed','bottom:0','left:0','right:0','z-index:99999',
    'background:#1E293B','border-top:1px solid rgba(255,255,255,0.08)',
    'padding:14px 24px','display:flex','align-items:center',
    'gap:16px','flex-wrap:wrap','font-family:Inter,-apple-system,sans-serif',
    'box-shadow:0 -4px 24px rgba(0,0,0,0.4)'
  ].join(';');
  banner.innerHTML = [
    '<span style="font-size:0.82rem;color:#CBD5E1;flex:1;min-width:200px">',
    '&#x1F36A; We use cookies for authentication and session management only. ',
    'No advertising or tracking cookies. ',
    '<a href="/privacy" style="color:#60A5FA;text-decoration:none">Learn more</a>',
    '</span>',
    '<button id="ih-cookie-accept" style="background:linear-gradient(135deg,#2563EB,#1D4ED8);',
    'color:#fff;border:none;border-radius:8px;padding:8px 20px;font-size:0.82rem;',
    'font-weight:600;cursor:pointer;white-space:nowrap;font-family:inherit">',
    'Accept &amp; Continue</button>',
    '<a href="/data-deletion" style="font-size:0.75rem;color:#64748B;',
    'text-decoration:none;white-space:nowrap">Delete my data</a>',
  ].join('');
  document.body.appendChild(banner);
  document.getElementById('ih-cookie-accept').addEventListener('click', function() {
    var d = new Date();
    d.setFullYear(d.getFullYear() + 1);
    document.cookie = 'ih_consent=1; path=/; expires=' + d.toUTCString() + '; SameSite=Lax';
    banner.style.display = 'none';
  });
})();
"""


# ─────────────────────────────────────────────────────────────────────────────
# Flask route registration
# ─────────────────────────────────────────────────────────────────────────────

def register_ccpa_routes(flask_app, engine) -> None:
    """Register /privacy, /data-deletion, /cookie-consent.js Flask routes."""
    from flask import request, Response

    @flask_app.route("/privacy")
    def privacy_policy():
        return Response(PRIVACY_PAGE, mimetype="text/html")

    @flask_app.route("/data-deletion", methods=["GET", "POST"])
    def data_deletion():
        success_block = ""
        error_block   = ""
        if request.method == "POST":
            email  = request.form.get("email", "").strip()
            reason = request.form.get("reason", "").strip()
            if not email or "@" not in email:
                error_block = '<div class="warn">⚠️ Please enter a valid email address.</div>'
            else:
                # Find user (don't block if user doesn't exist — CCPA requires honoring requests)
                uid, username = None, email
                try:
                    from sqlalchemy import text
                    with engine.connect() as conn:
                        row = conn.execute(text(
                            "SELECT id, username FROM users WHERE email=:e LIMIT 1"
                        ), {"e": email}).fetchone()
                        if row:
                            uid, username = row[0], row[1]
                except Exception:
                    pass
                req_id = _log_deletion_request(engine, email, username, uid, reason)
                _notify_dpo(email, username, req_id, reason)
                success_block = DELETION_SUCCESS.replace("__ID__", str(req_id))

        html = (DELETION_FORM
                .replace("__SUCCESS__", success_block)
                .replace("__ERROR__",   error_block))
        return Response(html, mimetype="text/html")

    @flask_app.route("/cookie-consent.js")
    def cookie_consent_js():
        return Response(COOKIE_BANNER_JS, mimetype="application/javascript",
                        headers={"Cache-Control": "public, max-age=86400"})

    logger.info("[ccpa] Routes registered: /privacy, /data-deletion, /cookie-consent.js")
