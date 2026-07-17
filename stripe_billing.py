"""
stripe_billing.py — InsightHub Stripe integration
Handles $39/month Starter plan subscription via Stripe Checkout.
BRD US-301 (billing), US-302 (trial).

Setup:
    pip install stripe

Environment variables:
    STRIPE_SECRET_KEY      — sk_live_... or sk_test_...
    STRIPE_PUBLISHABLE_KEY — pk_live_... or pk_test_...
    STRIPE_WEBHOOK_SECRET  — whsec_... (from Stripe Dashboard)
    STRIPE_STARTER_PRICE   — price_... (create in Stripe Dashboard: $39/mo)
    APP_BASE_URL           — https://yourdomain.com (for redirect URLs)

Quick start (test mode):
    1. Create account at https://stripe.com
    2. Go to Developers → API keys → copy test keys
    3. Create Product "InsightHub Starter" → Price $39/month recurring
    4. Copy the price_... ID to STRIPE_STARTER_PRICE
    5. Set env vars, restart app
    6. Hit /pricing to see the pricing page
"""

import os
import hashlib
import logging
from datetime import datetime, timedelta
from sqlalchemy import text

log = logging.getLogger("stripe_billing")

STRIPE_SECRET_KEY    = os.environ.get("STRIPE_SECRET_KEY",    "")
STRIPE_PUB_KEY       = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET= os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_STARTER_PRICE = os.environ.get("STRIPE_STARTER_PRICE",  "")   # price_xxx
APP_BASE_URL         = os.environ.get("APP_BASE_URL",          "http://localhost:8050")


# ── Pricing page HTML ─────────────────────────────────────────

PRICING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>InsightHub — Pricing</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: Inter, -apple-system, BlinkMacSystemFont, sans-serif;
         background: #F8FAFC; color: #1E293B; line-height: 1.6; }
  .nav { background: #1E293B; padding: 16px 32px; display: flex;
         align-items: center; justify-content: space-between; }
  .nav-logo { color: #fff; font-size: 18px; font-weight: 700; letter-spacing: -0.3px; }
  .nav-sub  { color: #94A3B8; font-size: 12px; margin-top: 2px; }
  .nav-login { color: #93C5FD; font-size: 13px; text-decoration: none; }
  .hero { text-align: center; padding: 64px 24px 40px; }
  .hero h1 { font-size: 38px; font-weight: 800; color: #0F172A; letter-spacing: -1px;
             margin-bottom: 12px; }
  .hero p  { font-size: 17px; color: #64748B; max-width: 500px; margin: 0 auto 8px; }
  .badge { display: inline-block; background: #EFF6FF; color: #2563EB; font-size: 12px;
           font-weight: 600; padding: 4px 14px; border-radius: 100px; margin-bottom: 24px;
           border: 1px solid #BFDBFE; }
  .plans { display: flex; gap: 20px; justify-content: center; padding: 0 24px 60px;
           flex-wrap: wrap; max-width: 1000px; margin: 0 auto; }
  .plan { background: #fff; border: 1px solid #E2E8F0; border-radius: 14px;
          padding: 32px 28px; flex: 1; min-width: 260px; max-width: 300px;
          position: relative; }
  .plan.featured { border: 2px solid #2563EB; }
  .plan-badge { position: absolute; top: -12px; left: 50%; transform: translateX(-50%);
                background: #2563EB; color: #fff; font-size: 11px; font-weight: 600;
                padding: 3px 14px; border-radius: 100px; white-space: nowrap; }
  .plan-name  { font-size: 13px; font-weight: 600; color: #64748B; text-transform: uppercase;
                letter-spacing: 0.07em; margin-bottom: 8px; }
  .plan-price { font-size: 42px; font-weight: 800; color: #0F172A; letter-spacing: -1.5px;
                line-height: 1; }
  .plan-price span { font-size: 16px; font-weight: 500; color: #64748B; vertical-align: super;
                     font-size: 18px; }
  .plan-period { font-size: 13px; color: #94A3B8; margin: 4px 0 20px; }
  .plan-desc  { font-size: 13px; color: #64748B; margin-bottom: 20px;
                padding-bottom: 20px; border-bottom: 1px solid #F1F5F9; }
  .features   { list-style: none; margin-bottom: 28px; }
  .features li { font-size: 13px; color: #334155; padding: 5px 0;
                  display: flex; align-items: flex-start; gap: 8px; }
  .check  { color: #059669; font-weight: 700; flex-shrink: 0; margin-top: 1px; }
  .cross  { color: #94A3B8; font-weight: 700; flex-shrink: 0; margin-top: 1px; }
  .btn { display: block; text-align: center; padding: 13px 24px; border-radius: 8px;
         font-size: 14px; font-weight: 600; text-decoration: none; cursor: pointer;
         border: none; width: 100%; }
  .btn-primary { background: #2563EB; color: #fff; }
  .btn-primary:hover { background: #1D4ED8; }
  .btn-outline { background: transparent; color: #2563EB;
                 border: 1.5px solid #2563EB; }
  .btn-outline:hover { background: #EFF6FF; }
  .trial-note { font-size: 12px; color: #94A3B8; text-align: center; margin-top: 12px; }
  .faq { max-width: 680px; margin: 0 auto; padding: 20px 24px 64px; }
  .faq h2 { font-size: 22px; font-weight: 700; margin-bottom: 24px; color: #0F172A; }
  .faq-item { border-top: 1px solid #E2E8F0; padding: 18px 0; }
  .faq-q { font-size: 14px; font-weight: 600; color: #1E293B; margin-bottom: 6px; }
  .faq-a { font-size: 13px; color: #64748B; }
  .footer { background: #1E293B; padding: 24px 32px; text-align: center;
             color: #64748B; font-size: 12px; }
</style>
</head>
<body>

<nav class="nav">
  <div>
    <div class="nav-logo">InsightHub</div>
    <div class="nav-sub">Business Analytics Platform</div>
  </div>
  <a href="/" class="nav-login">Sign in →</a>
</nav>

<div class="hero">
  <div class="badge">14-day free trial · No credit card required</div>
  <h1>Simple, transparent pricing</h1>
  <p>Everything your business needs to turn data into decisions.</p>
</div>

<div class="plans">

  <!-- Free Trial -->
  <div class="plan">
    <div class="plan-name">Free Trial</div>
    <div class="plan-price"><span>$</span>0</div>
    <div class="plan-period">14 days, then $39/month</div>
    <div class="plan-desc">Full access to all Starter features. No credit card required to start.</div>
    <ul class="features">
      <li><span class="check">✓</span> Up to 5,000 transactions</li>
      <li><span class="check">✓</span> 1 business / workspace</li>
      <li><span class="check">✓</span> CSV & Excel upload</li>
      <li><span class="check">✓</span> Revenue + customer analytics</li>
      <li><span class="check">✓</span> Weekly email reports</li>
      <li><span class="cross">–</span> QuickBooks / Shopify sync</li>
      <li><span class="cross">–</span> AI Chat on your data</li>
      <li><span class="cross">–</span> Multiple team members</li>
    </ul>
    <a href="/signup" class="btn btn-outline">Start free trial</a>
    <div class="trial-note">No credit card. Cancel anytime.</div>
  </div>

  <!-- Starter (featured) -->
  <div class="plan featured">
    <div class="plan-badge">Most Popular</div>
    <div class="plan-name">Starter</div>
    <div class="plan-price"><span>$</span>39</div>
    <div class="plan-period">per month, billed monthly</div>
    <div class="plan-desc">For growing businesses that need reliable, automated analytics.</div>
    <ul class="features">
      <li><span class="check">✓</span> Up to 50,000 transactions/mo</li>
      <li><span class="check">✓</span> 3 business workspaces</li>
      <li><span class="check">✓</span> CSV, Excel, QuickBooks CSV</li>
      <li><span class="check">✓</span> Revenue, cost & margin analytics</li>
      <li><span class="check">✓</span> Year-over-year comparison</li>
      <li><span class="check">✓</span> Weekly email reports (auto)</li>
      <li><span class="check">✓</span> SMS/email alerts</li>
      <li><span class="check">✓</span> Email support</li>
    </ul>
    <form id="checkout-form" method="POST" action="/stripe/checkout">
      <input type="hidden" name="plan" value="starter">
      <button type="submit" class="btn btn-primary">Start free trial →</button>
    </form>
    <div class="trial-note">14-day free trial included.</div>
  </div>

  <!-- Growth -->
  <div class="plan">
    <div class="plan-name">Growth</div>
    <div class="plan-price"><span>$</span>99</div>
    <div class="plan-period">per month, billed monthly</div>
    <div class="plan-desc">For teams that want live integrations, AI, and multi-user access.</div>
    <ul class="features">
      <li><span class="check">✓</span> Unlimited transactions</li>
      <li><span class="check">✓</span> 10 workspaces</li>
      <li><span class="check">✓</span> QuickBooks OAuth live sync</li>
      <li><span class="check">✓</span> Shopify + Square connectors</li>
      <li><span class="check">✓</span> AI Chat on your data</li>
      <li><span class="check">✓</span> Up to 5 team members</li>
      <li><span class="check">✓</span> Priority support</li>
      <li><span class="check">✓</span> Custom branding</li>
    </ul>
    <a href="mailto:hello@insighthub.io?subject=Growth Plan Inquiry" class="btn btn-outline">
      Contact sales
    </a>
    <div class="trial-note">Available Q1 2027.</div>
  </div>

</div>

<div class="faq">
  <h2>Frequently asked questions</h2>
  <div class="faq-item">
    <div class="faq-q">What file formats can I upload?</div>
    <div class="faq-a">CSV, Excel (.xlsx/.xls), and QuickBooks Desktop export files. Our parser auto-detects columns — no template required.</div>
  </div>
  <div class="faq-item">
    <div class="faq-q">Do I need to install anything?</div>
    <div class="faq-a">No. InsightHub runs entirely in your browser. Upload a file and your dashboard is ready in seconds.</div>
  </div>
  <div class="faq-item">
    <div class="faq-q">How does the 14-day trial work?</div>
    <div class="faq-a">Full Starter access, no credit card required. At the end of the trial you can subscribe or your account pauses (data is kept for 30 days).</div>
  </div>
  <div class="faq-item">
    <div class="faq-q">Is my data secure?</div>
    <div class="faq-a">Yes. Each business has its own isolated workspace. Your data is never shared with other customers.</div>
  </div>
  <div class="faq-item">
    <div class="faq-q">Can I cancel anytime?</div>
    <div class="faq-a">Yes, cancel with one click — no questions asked, no cancellation fees.</div>
  </div>
</div>

<footer class="footer">
  © 2026 InsightHub · <a href="/" style="color:#64748B">Dashboard</a> ·
  <a href="mailto:hello@insighthub.io" style="color:#64748B">hello@insighthub.io</a>
</footer>

</body>
</html>"""


# ── Stripe checkout session ───────────────────────────────────

def create_checkout_session(tenant_id: int, email: str = None) -> dict:
    """
    Create a Stripe Checkout session for the Starter plan.
    Returns {"url": checkout_url} or {"error": message}.
    If Stripe keys not configured, returns a mock URL for dev.
    """
    if not STRIPE_SECRET_KEY or not STRIPE_STARTER_PRICE:
        log.warning("[stripe] Keys not configured — returning mock checkout URL")
        return {"url": f"/stripe/mock-success?tenant={tenant_id}", "mock": True}

    try:
        import stripe
        stripe.api_key = STRIPE_SECRET_KEY

        session_params = {
            "mode": "subscription",
            "line_items": [{"price": STRIPE_STARTER_PRICE, "quantity": 1}],
            "success_url": f"{APP_BASE_URL}/stripe/success?session_id={{CHECKOUT_SESSION_ID}}&tenant={tenant_id}",
            "cancel_url":  f"{APP_BASE_URL}/pricing",
            "subscription_data": {
                "trial_period_days": 14,
                "metadata": {"tenant_id": str(tenant_id)},
            },
            "metadata": {"tenant_id": str(tenant_id)},
            "allow_promotion_codes": True,
        }
        if email:
            session_params["customer_email"] = email

        session = stripe.checkout.Session.create(**session_params)
        return {"url": session.url}
    except Exception as exc:
        log.error(f"[stripe] Checkout error: {exc}")
        return {"error": str(exc)}


def handle_webhook(payload: bytes, sig_header: str, engine) -> dict:
    """
    Process Stripe webhook events to update tenant subscription status.
    Returns {"status": "ok"} or {"status": "error", "reason": ...}.
    """
    if not STRIPE_SECRET_KEY:
        return {"status": "skip", "reason": "stripe not configured"}

    try:
        import stripe
        stripe.api_key = STRIPE_SECRET_KEY
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}

    etype = event["type"]
    log.info(f"[stripe] Webhook: {etype}")

    if etype in ("customer.subscription.created", "customer.subscription.updated"):
        sub = event["data"]["object"]
        tid = sub.get("metadata", {}).get("tenant_id")
        if tid:
            _update_tenant_subscription(engine, int(tid), sub["status"],
                                        sub.get("current_period_end"))

    elif etype == "customer.subscription.deleted":
        sub = event["data"]["object"]
        tid = sub.get("metadata", {}).get("tenant_id")
        if tid:
            _update_tenant_subscription(engine, int(tid), "canceled", None)

    elif etype == "checkout.session.completed":
        sess = event["data"]["object"]
        tid  = sess.get("metadata", {}).get("tenant_id")
        if tid:
            log.info(f"[stripe] Checkout completed for tenant {tid}")

    return {"status": "ok"}


def _update_tenant_subscription(engine, tenant_id: int, status: str, period_end=None):
    """Update local_tenants table with Stripe subscription status."""
    try:
        with engine.connect() as conn:
            # Ensure columns exist
            try:
                conn.execute(text("ALTER TABLE local_tenants ADD COLUMN stripe_status TEXT DEFAULT 'trial'"))
                conn.commit()
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE local_tenants ADD COLUMN trial_ends_at TEXT"))
                conn.commit()
            except Exception:
                pass

            conn.execute(
                text("UPDATE local_tenants SET stripe_status=:s WHERE id=:tid"),
                {"s": status, "tid": tenant_id}
            )
            if period_end:
                from datetime import timezone
                dt = datetime.fromtimestamp(period_end, tz=timezone.utc).strftime("%Y-%m-%d")
                conn.execute(
                    text("UPDATE local_tenants SET trial_ends_at=:d WHERE id=:tid"),
                    {"d": dt, "tid": tenant_id}
                )
            conn.commit()
            log.info(f"[stripe] tenant {tenant_id} → status={status}")
    except Exception as exc:
        log.error(f"[stripe] DB update error: {exc}")


def get_tenant_billing_status(engine, tenant_id: int) -> dict:
    """Return current billing status for a tenant."""
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT stripe_status, trial_ends_at FROM local_tenants WHERE id=:tid"),
                {"tid": tenant_id}
            ).fetchone()
            if not row:
                return {"status": "unknown"}
            status, ends_at = row[0] or "trial", row[1]
            days_left = None
            if ends_at:
                try:
                    d = datetime.strptime(ends_at, "%Y-%m-%d")
                    days_left = (d - datetime.utcnow()).days
                except Exception:
                    pass
            return {"status": status, "trial_ends_at": ends_at, "days_left": days_left}
    except Exception:
        return {"status": "trial"}


def register_stripe_routes(app_server, engine):
    """Register /pricing, /stripe/checkout, /stripe/success, /stripe/webhook routes."""
    from flask import request, redirect, jsonify, make_response

    @app_server.route("/pricing")
    def pricing_page():
        r = make_response(PRICING_HTML)
        r.headers["Content-Type"] = "text/html; charset=utf-8"
        return r

    @app_server.route("/stripe/checkout", methods=["POST"])
    def stripe_checkout():
        """Create Stripe Checkout session and redirect."""
        # Get tenant from session if logged in, else guest
        try:
            from flask_login import current_user
            tid = getattr(current_user, "tenant_id", None) or 0
            email = getattr(current_user, "email", None) or request.form.get("email", "")
        except Exception:
            tid = 0
            email = request.form.get("email", "")

        result = create_checkout_session(tenant_id=tid, email=email)
        if "url" in result:
            return redirect(result["url"])
        return f"<p>Error creating checkout: {result.get('error')}</p>", 500

    @app_server.route("/stripe/success")
    def stripe_success():
        tid = request.args.get("tenant", "")
        r = make_response(f"""<!DOCTYPE html><html><head>
        <meta charset="UTF-8"><title>Welcome to InsightHub!</title>
        <style>body{{font-family:Inter,Arial,sans-serif;background:#F8FAFC;
        display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
        .box{{background:#fff;border:1px solid #E2E8F0;border-radius:14px;
        padding:48px 40px;text-align:center;max-width:440px}}
        h1{{font-size:24px;font-weight:700;color:#0F172A;margin-bottom:8px}}
        p{{color:#64748B;font-size:14px;margin-bottom:24px}}
        a{{display:inline-block;background:#2563EB;color:#fff;padding:12px 28px;
        border-radius:8px;text-decoration:none;font-weight:600;font-size:14px}}</style>
        <meta http-equiv="refresh" content="4;url=/">
        </head><body><div class="box">
        <div style="font-size:42px;margin-bottom:12px">🎉</div>
        <h1>You're all set!</h1>
        <p>Your 14-day free trial of InsightHub Starter has started.<br>
        Redirecting to your dashboard in a moment...</p>
        <a href="/">Go to Dashboard →</a>
        </div></body></html>""")
        r.headers["Content-Type"] = "text/html; charset=utf-8"
        return r

    @app_server.route("/stripe/mock-success")
    def stripe_mock_success():
        """Dev-mode mock success page when Stripe keys aren't configured."""
        tid = request.args.get("tenant", "?")
        r = make_response(f"""<!DOCTYPE html><html><head>
        <meta charset="UTF-8"><title>Mock Checkout Success</title>
        <style>body{{font-family:Inter,Arial,sans-serif;background:#F8FAFC;
        display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
        .box{{background:#fff;border:2px dashed #BFDBFE;border-radius:14px;
        padding:40px;text-align:center;max-width:440px}}
        h1{{font-size:20px;font-weight:700;color:#1E293B;margin-bottom:8px}}
        p{{color:#64748B;font-size:13px;margin-bottom:16px}}
        .badge{{display:inline-block;background:#EFF6FF;color:#2563EB;font-size:11px;
        font-weight:600;padding:4px 12px;border-radius:100px;margin-bottom:16px}}
        a{{display:inline-block;background:#2563EB;color:#fff;padding:10px 24px;
        border-radius:7px;text-decoration:none;font-weight:600;font-size:13px}}</style>
        </head><body><div class="box">
        <div class="badge">DEV MODE — Mock Checkout</div>
        <h1>Stripe keys not configured</h1>
        <p>In production, this would complete a real $39/month checkout.<br>
        Set STRIPE_SECRET_KEY and STRIPE_STARTER_PRICE to enable live billing.<br><br>
        <b>Tenant ID:</b> {tid}</p>
        <a href="/">Back to Dashboard</a>
        </div></body></html>""")
        r.headers["Content-Type"] = "text/html; charset=utf-8"
        return r

    @app_server.route("/stripe/webhook", methods=["POST"])
    def stripe_billing_webhook():
        payload    = request.get_data()
        sig_header = request.headers.get("Stripe-Signature", "")
        result     = handle_webhook(payload, sig_header, engine)
        return jsonify(result), 200 if result["status"] in ("ok", "skip") else 400

    log.info("[stripe] Routes registered: /pricing, /stripe/checkout, /stripe/success, /stripe/webhook")
