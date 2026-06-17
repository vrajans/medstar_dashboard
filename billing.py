"""
billing.py  —  InsightHub dual-currency billing
================================================
Razorpay  — India (INR)
Stripe    — USA / Global (USD)

Plans
-----
  starter  : 1 branch, basic analytics
  growth   : 5 branches, alerts, integrations
  pro      : unlimited branches, AI features, white-label

Usage
-----
from billing import create_checkout_session, get_subscription_status, BillingEngine

eng = BillingEngine(currency="INR")   # or "USD"
url = eng.create_checkout("growth", tenant_id=3, success_url="https://...", cancel_url="https://...")
"""

import os
import json
import logging
import hashlib
import hmac
from datetime import datetime
from typing import Optional, Literal, Any

logger = logging.getLogger(__name__)

# ── Environment ───────────────────────────────────────────────────────────────
RAZORPAY_KEY_ID     = os.getenv("RAZORPAY_KEY_ID",     "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
STRIPE_SECRET_KEY   = os.getenv("STRIPE_SECRET_KEY",   "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")

Currency = Literal["INR", "USD", "GBP", "EUR"]

# ── Pricing catalogue ─────────────────────────────────────────────────────────
PLANS = {
    "starter": {
        "name":        "Starter",
        "description": "1 branch · Basic analytics · Email alerts",
        "INR":  {"monthly": 99900,   "yearly": 999900},    # paise
        "USD":  {"monthly":  1500,   "yearly":  14400},    # cents
        "features": ["1 branch", "Sales & Purchase analytics",
                     "Email alerts", "PDF reports", "1 admin user"],
        "branch_limit": 1,
        "user_limit":   1,
    },
    "growth": {
        "name":        "Growth",
        "description": "5 branches · WhatsApp & SMS alerts · Integrations",
        "INR":  {"monthly": 299900,  "yearly": 2999900},
        "USD":  {"monthly":  3900,   "yearly":  37200},
        "features": ["5 branches", "WhatsApp & SMS alerts",
                     "Marg/QuickBooks integration", "YoY comparison",
                     "GST reports", "CA access", "5 admin users"],
        "branch_limit": 5,
        "user_limit":   5,
    },
    "pro": {
        "name":        "Pro",
        "description": "Unlimited · AI chat · Anomaly detection · White-label",
        "INR":  {"monthly": 699900,  "yearly": 6999900},
        "USD":  {"monthly":  9900,   "yearly":  95000},
        "features": ["Unlimited branches", "AI Chat & RAG",
                     "Anomaly detection", "Slack alerts",
                     "Tamil/Hindi UI", "White-label", "Unlimited users"],
        "branch_limit": -1,   # unlimited
        "user_limit":   -1,
    },
}


def _fmt_price(amount_minor: int, currency: str) -> str:
    """Format minor units to display string."""
    major = amount_minor / 100
    if currency == "INR":
        if major >= 1000: return f"₹{major/1000:.1f}K/mo"
        return f"₹{major:.0f}/mo"
    elif currency == "USD":
        return f"${major:.0f}/mo"
    return f"{major:.2f} {currency}/mo"


# ═════════════════════════════════════════════════════════════════════════════
# RAZORPAY  —  India
# ═════════════════════════════════════════════════════════════════════════════

class RazorpayBilling:
    BASE = "https://api.razorpay.com/v1"

    def __init__(self):
        if not (RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET):
            logger.warning("[billing.razorpay] Credentials not set.")
        import base64
        creds    = f"{RAZORPAY_KEY_ID}:{RAZORPAY_KEY_SECRET}"
        self._auth = "Basic " + base64.b64encode(creds.encode()).decode()

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        import urllib.request
        url  = f"{self.BASE}{path}"
        data = json.dumps(body).encode() if body else None
        req  = urllib.request.Request(
            url, data=data,
            headers={
                "Authorization": self._auth,
                "Content-Type":  "application/json",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            err = json.loads(e.read())
            logger.error("[billing.razorpay] %s %s -> %s", method, path, err)
            return {"error": err}

    def create_subscription_link(
        self,
        plan_id: str,             # "starter" | "growth" | "pro"
        tenant_id: int,
        billing: str = "monthly", # "monthly" | "yearly"
        name: str = "",
        email: str = "",
        phone: str = "",
    ) -> Optional[str]:
        """
        Create a Razorpay Payment Link for subscription.
        Returns the payment link URL or None on failure.
        """
        plan   = PLANS.get(plan_id)
        if not plan:
            return None
        amount = plan["INR"][billing]
        body   = {
            "amount":       amount,
            "currency":     "INR",
            "description":  f"InsightHub {plan['name']} — {billing}",
            "customer": {"name": name, "email": email, "contact": phone},
            "notify":   {"sms": bool(phone), "email": bool(email)},
            "reminder_enable": True,
            "notes": {
                "tenant_id":   str(tenant_id),
                "plan":        plan_id,
                "billing":     billing,
                "source":      "insighthub",
            },
            "callback_url":    os.getenv("RAZORPAY_CALLBACK_URL", ""),
            "callback_method": "get",
        }
        result = self._request("POST", "/payment_links", body)
        if "error" in result:
            return None
        return result.get("short_url") or result.get("id")

    def verify_webhook(self, payload: bytes, signature: str) -> bool:
        """Verify Razorpay webhook X-Razorpay-Signature."""
        if not RAZORPAY_WEBHOOK_SECRET:
            return False
        expected = hmac.new(
            RAZORPAY_WEBHOOK_SECRET.encode(),
            payload, hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def get_payment(self, payment_id: str) -> dict:
        return self._request("GET", f"/payments/{payment_id}")


# ═════════════════════════════════════════════════════════════════════════════
# STRIPE  —  USA / Global
# ═════════════════════════════════════════════════════════════════════════════

class StripeBilling:

    def __init__(self):
        if not STRIPE_SECRET_KEY:
            logger.warning("[billing.stripe] STRIPE_SECRET_KEY not set.")

    def _request(self, method: str, path: str,
                 body: Optional[dict] = None,
                 params: Optional[dict] = None) -> dict:
        import urllib.request, urllib.parse
        base_url = "https://api.stripe.com/v1"
        url      = f"{base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data     = urllib.parse.urlencode(body).encode() if body else None
        import base64
        auth     = "Basic " + base64.b64encode(f"{STRIPE_SECRET_KEY}:".encode()).decode()
        req      = urllib.request.Request(
            url, data=data,
            headers={"Authorization": auth,
                     "Content-Type": "application/x-www-form-urlencoded"},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            err = json.loads(e.read())
            logger.error("[billing.stripe] %s %s -> %s", method, path, err)
            return {"error": err}

    def create_checkout_session(
        self,
        plan_id:     str,
        tenant_id:   int,
        billing:     str = "monthly",
        currency:    str = "usd",
        success_url: str = "",
        cancel_url:  str  = "",
        customer_email: str = "",
    ) -> Optional[str]:
        """
        Create a Stripe Checkout Session.
        Returns the checkout URL or None on failure.
        """
        plan   = PLANS.get(plan_id)
        if not plan:
            return None
        amount = plan["USD"][billing]
        body   = {
            "mode":                        "payment",
            "payment_method_types[]":      "card",
            "line_items[0][price_data][currency]":            currency,
            "line_items[0][price_data][unit_amount]":         str(amount),
            "line_items[0][price_data][product_data][name]":  f"InsightHub {plan['name']}",
            "line_items[0][price_data][product_data][description]": plan["description"],
            "line_items[0][quantity]":     "1",
            "success_url":                 success_url or "https://app.insighthub.ai/billing/success",
            "cancel_url":                  cancel_url  or "https://app.insighthub.ai/billing/cancel",
            "metadata[tenant_id]":         str(tenant_id),
            "metadata[plan]":              plan_id,
            "metadata[billing]":           billing,
        }
        if customer_email:
            body["customer_email"] = customer_email

        result = self._request("POST", "/checkout/sessions", body)
        if "error" in result:
            return None
        return result.get("url")

    def verify_webhook(self, payload: bytes, sig_header: str) -> Optional[dict]:
        """
        Verify and parse a Stripe webhook event.
        Returns the event dict on success, None on failure.
        """
        if not STRIPE_WEBHOOK_SECRET:
            return None
        try:
            # Parse Stripe-Signature header
            parts    = {k: v for k, v in (p.split("=", 1) for p in sig_header.split(","))}
            ts       = parts.get("t", "")
            v1_sig   = parts.get("v1", "")
            signed   = f"{ts}.".encode() + payload
            expected = hmac.new(
                STRIPE_WEBHOOK_SECRET.encode(), signed, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected, v1_sig):
                logger.warning("[billing.stripe] Webhook signature mismatch.")
                return None
            return json.loads(payload)
        except Exception as exc:
            logger.error("[billing.stripe] verify_webhook: %s", exc)
            return None

    def retrieve_session(self, session_id: str) -> dict:
        return self._request("GET", f"/checkout/sessions/{session_id}")


# ═════════════════════════════════════════════════════════════════════════════
# BILLING ENGINE  —  auto-selects provider by currency
# ═════════════════════════════════════════════════════════════════════════════

class BillingEngine:
    """
    High-level billing facade.

    BillingEngine("INR") → Razorpay
    BillingEngine("USD") → Stripe
    """

    def __init__(self, currency: str = "INR"):
        self.currency = currency.upper()
        self._razorpay: Optional[RazorpayBilling] = None
        self._stripe:   Optional[StripeBilling]   = None

        if self.currency == "INR":
            self._razorpay = RazorpayBilling()
        else:
            self._stripe = StripeBilling()

    def create_checkout(
        self,
        plan_id:     str,
        tenant_id:   int,
        billing:     str = "monthly",
        email:       str = "",
        name:        str = "",
        phone:       str = "",
        success_url: str = "",
        cancel_url:  str = "",
    ) -> Optional[str]:
        """Create checkout link. Returns URL or None."""
        if self._razorpay:
            return self._razorpay.create_subscription_link(
                plan_id, tenant_id, billing, name, email, phone
            )
        elif self._stripe:
            return self._stripe.create_checkout_session(
                plan_id, tenant_id, billing,
                currency=self.currency.lower(),
                success_url=success_url,
                cancel_url=cancel_url,
                customer_email=email,
            )
        return None

    def get_plan_display(self) -> list[dict]:
        """Return plan list with pricing formatted for this currency."""
        result = []
        for pid, plan in PLANS.items():
            price_map = plan.get(self.currency, {})
            result.append({
                "plan_id":     pid,
                "name":        plan["name"],
                "description": plan["description"],
                "monthly":     _fmt_price(price_map.get("monthly", 0), self.currency),
                "yearly":      _fmt_price(price_map.get("yearly",  0), self.currency),
                "features":    plan["features"],
                "branch_limit": plan["branch_limit"],
                "user_limit":   plan["user_limit"],
            })
        return result


# ═════════════════════════════════════════════════════════════════════════════
# DB HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def init_billing_tables(engine: Any) -> None:
    """Create billing tables if they don't exist."""
    ddl = """
    CREATE TABLE IF NOT EXISTS tenant_subscriptions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id       INTEGER NOT NULL UNIQUE,
        plan_id         TEXT    DEFAULT 'starter',
        billing_cycle   TEXT    DEFAULT 'monthly',
        currency        TEXT    DEFAULT 'INR',
        status          TEXT    DEFAULT 'trial',    -- trial|active|past_due|cancelled
        provider        TEXT,                        -- razorpay|stripe
        provider_ref    TEXT,                        -- payment_id or session_id
        trial_ends      TEXT,
        current_period_end TEXT,
        created_at      TEXT    DEFAULT (datetime('now')),
        updated_at      TEXT    DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS billing_events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id   INTEGER,
        event_type  TEXT,       -- payment.captured|checkout.session.completed|...
        amount      INTEGER,
        currency    TEXT,
        provider    TEXT,
        raw_payload TEXT,
        received_at TEXT DEFAULT (datetime('now'))
    );
    """
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            for stmt in ddl.strip().split(";"):
                s = stmt.strip()
                if s:
                    conn.execute(text(s))
        logger.info("[billing] Billing tables initialised.")
    except Exception as exc:
        logger.error("[billing] init_billing_tables: %s", exc)


def get_subscription(tenant_id: int, engine: Any) -> dict:
    """Return the subscription record for a tenant."""
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT plan_id, billing_cycle, currency, status,
                       trial_ends, current_period_end
                FROM   tenant_subscriptions
                WHERE  tenant_id = :tid
            """), {"tid": tenant_id}).fetchone()
        if row:
            return {"plan_id": row[0], "billing": row[1], "currency": row[2],
                    "status": row[3], "trial_ends": row[4], "period_end": row[5]}
    except Exception as exc:
        logger.error("[billing] get_subscription: %s", exc)
    return {"plan_id": "starter", "billing": "monthly",
            "currency": "INR", "status": "trial",
            "trial_ends": None, "period_end": None}


def upsert_subscription(tenant_id: int, plan_id: str, status: str,
                        provider: str, provider_ref: str,
                        currency: str, engine: Any) -> None:
    """Insert or update subscription record after a successful payment."""
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO tenant_subscriptions
                    (tenant_id, plan_id, status, provider, provider_ref, currency)
                VALUES (:tid, :plan, :st, :prov, :ref, :cur)
                ON CONFLICT(tenant_id) DO UPDATE SET
                    plan_id      = excluded.plan_id,
                    status       = excluded.status,
                    provider     = excluded.provider,
                    provider_ref = excluded.provider_ref,
                    currency     = excluded.currency,
                    updated_at   = datetime('now')
            """), {"tid": tenant_id, "plan": plan_id, "st": status,
                   "prov": provider, "ref": provider_ref, "cur": currency})
        logger.info("[billing] Subscription updated: tenant=%s plan=%s status=%s",
                    tenant_id, plan_id, status)
    except Exception as exc:
        logger.error("[billing] upsert_subscription: %s", exc)


# ── Flask route helpers (register on app.server) ─────────────────────────────

def register_billing_routes(flask_app: Any, engine: Any) -> None:
    """
    Register /billing/* Flask routes for webhook handling.

    Call after app.server is available:
        from billing import register_billing_routes
        register_billing_routes(app.server, engine)
    """
    from flask import request, jsonify

    @flask_app.route("/billing/stripe/webhook", methods=["POST"])
    def stripe_webhook():
        sig     = request.headers.get("Stripe-Signature", "")
        billing = StripeBilling()
        event   = billing.verify_webhook(request.data, sig)
        if not event:
            return jsonify({"error": "invalid signature"}), 400

        etype = event.get("type", "")
        if etype == "checkout.session.completed":
            session   = event["data"]["object"]
            meta      = session.get("metadata", {})
            tenant_id = int(meta.get("tenant_id", 0))
            plan_id   = meta.get("plan", "starter")
            if tenant_id:
                upsert_subscription(tenant_id, plan_id, "active",
                                    "stripe", session["id"], "USD", engine)
        return jsonify({"status": "ok"}), 200

    @flask_app.route("/billing/razorpay/webhook", methods=["POST"])
    def razorpay_webhook():
        sig     = request.headers.get("X-Razorpay-Signature", "")
        billing = RazorpayBilling()
        if not billing.verify_webhook(request.data, sig):
            return jsonify({"error": "invalid signature"}), 400

        event = request.get_json(force=True) or {}
        if event.get("event") == "payment_link.paid":
            payment = event.get("payload", {}).get("payment", {}).get("entity", {})
            notes   = payment.get("notes", {})
            tenant_id = int(notes.get("tenant_id", 0))
            plan_id   = notes.get("plan", "starter")
            if tenant_id:
                upsert_subscription(tenant_id, plan_id, "active",
                                    "razorpay", payment.get("id", ""), "INR", engine)
        return jsonify({"status": "ok"}), 200

    @flask_app.route("/billing/plans")
    def billing_plans():
        currency = request.args.get("currency", "INR").upper()
        eng      = BillingEngine(currency)
        return jsonify(eng.get_plan_display())

    logger.info("[billing] Billing routes registered.")
