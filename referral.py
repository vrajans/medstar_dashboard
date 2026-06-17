"""
referral.py  —  InsightHub Referral Program
============================================
Tenant-to-tenant referral tracking with reward credits.

Flow
----
  Tenant A shares their referral link → Tenant B signs up using code
  → B's subscription is created → A gets credit on next billing cycle
  → Both parties see referral status in their dashboard

DB Tables
---------
  referral_codes   — one code per tenant (auto-generated)
  referral_events  — tracks each referral: pending → converted → rewarded

Reward Logic
------------
  India (INR):  ₹500 credit per successful referral (Growth/Pro plan only)
  USA (USD):    $10 credit per successful referral
  Cooldown:     30 days between reward payouts per referrer

Usage
-----
  from referral import init_referral_tables, register_referral_routes, render_referral_tab
"""

import os
import secrets
import string
import logging
from datetime import datetime, timedelta
from typing import Optional, Any

from sqlalchemy import text
from dash import html, dcc
import dash_bootstrap_components as dbc

logger = logging.getLogger(__name__)

APP_NAME      = os.getenv("APP_NAME",  "InsightHub")
APP_BASE_URL  = os.getenv("APP_BASE_URL", "https://insighthub.app")

C_GREEN  = "#1e7e4b"
C_BLUE   = "#0d6efd"
C_ORANGE = "#fd7e14"
C_GRAY   = "#6b7280"
C_PURPLE = "#6f42c1"
C_RED    = "#dc3545"

# Credit amounts per successful referral
REFERRAL_CREDIT_INR = 500   # ₹500 credit
REFERRAL_CREDIT_USD = 10    # $10 credit


# ═════════════════════════════════════════════════════════════════════════════
# DB Initialisation
# ═════════════════════════════════════════════════════════════════════════════

def init_referral_tables(engine: Any) -> None:
    """Create referral DB tables if they don't exist."""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS referral_codes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id   INTEGER NOT NULL UNIQUE,
                code        TEXT    NOT NULL UNIQUE,
                created_at  TEXT    NOT NULL,
                total_refs  INTEGER DEFAULT 0,
                total_rewarded INTEGER DEFAULT 0
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS referral_events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_tid    INTEGER NOT NULL,
                referred_email  TEXT,
                referred_tid    INTEGER,
                code            TEXT    NOT NULL,
                status          TEXT    DEFAULT 'pending',
                created_at      TEXT    NOT NULL,
                converted_at    TEXT,
                rewarded_at     TEXT,
                credit_amount   REAL    DEFAULT 0,
                currency        TEXT    DEFAULT 'INR'
            )
        """))
        conn.commit()
    logger.info("[referral] Tables initialised.")


# ═════════════════════════════════════════════════════════════════════════════
# Referral code management
# ═════════════════════════════════════════════════════════════════════════════

def _generate_code(length: int = 8) -> str:
    """Generate a short uppercase alphanumeric referral code."""
    alphabet = string.ascii_uppercase + string.digits
    return "IH-" + "".join(secrets.choice(alphabet) for _ in range(length))


def get_or_create_referral_code(tenant_id: int, engine: Any) -> str:
    """Return existing referral code for tenant, or create one."""
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT code FROM referral_codes WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        ).fetchone()
        if row:
            return row[0]

        # Create new code
        for _ in range(10):  # retry if collision
            code = _generate_code()
            try:
                conn.execute(text("""
                    INSERT INTO referral_codes (tenant_id, code, created_at)
                    VALUES (:tid, :code, :now)
                """), {"tid": tenant_id, "code": code,
                       "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                conn.commit()
                logger.info("[referral] Created code %s for tenant %s", code, tenant_id)
                return code
            except Exception:
                continue  # code collision, try again

    return "IH-ERROR"


def get_referral_stats(tenant_id: int, engine: Any) -> dict:
    """Return referral statistics for a tenant."""
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT total_refs, total_rewarded FROM referral_codes WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        ).fetchone()
        total_refs    = row[0] if row else 0
        total_rewarded= row[1] if row else 0

        events = conn.execute(text("""
            SELECT status, COUNT(*) AS cnt,
                   SUM(credit_amount) AS total_credit,
                   currency
            FROM referral_events
            WHERE referrer_tid = :tid
            GROUP BY status, currency
        """), {"tid": tenant_id}).fetchall()

    stats = {
        "total_sent":     total_refs,
        "total_rewarded": total_rewarded,
        "pending":   0,
        "converted": 0,
        "rewarded":  0,
        "total_credit_inr": 0.0,
        "total_credit_usd": 0.0,
    }
    for ev in events:
        status = ev[0]
        cnt    = ev[1]
        credit = ev[2] or 0.0
        currency = ev[3] or "INR"
        if status in stats:
            stats[status] += cnt
        if currency == "USD":
            stats["total_credit_usd"] += credit
        else:
            stats["total_credit_inr"] += credit

    return stats


def record_referral_signup(code: str, referred_email: str, engine: Any) -> bool:
    """
    Called when a new tenant signs up via a referral link.
    Creates a 'pending' referral event.
    """
    with engine.connect() as conn:
        referrer = conn.execute(
            text("SELECT tenant_id FROM referral_codes WHERE code = :code"),
            {"code": code},
        ).fetchone()
        if not referrer:
            logger.warning("[referral] Unknown code: %s", code)
            return False

        conn.execute(text("""
            INSERT INTO referral_events
                (referrer_tid, referred_email, code, status, created_at)
            VALUES (:rtid, :email, :code, 'pending', :now)
        """), {
            "rtid":  referrer[0],
            "email": referred_email,
            "code":  code,
            "now":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        # Increment total_refs counter
        conn.execute(text("""
            UPDATE referral_codes SET total_refs = total_refs + 1
            WHERE tenant_id = :tid
        """), {"tid": referrer[0]})
        conn.commit()
    logger.info("[referral] Pending referral recorded for code %s, email %s", code, referred_email)
    return True


def convert_referral(referred_tid: int, code: str, currency: str,
                     engine: Any) -> bool:
    """
    Called when a referred tenant completes their first paid subscription.
    Marks the referral as 'converted' and calculates credit.
    """
    credit = REFERRAL_CREDIT_USD if currency == "USD" else REFERRAL_CREDIT_INR
    with engine.connect() as conn:
        conn.execute(text("""
            UPDATE referral_events
            SET status = 'converted',
                referred_tid  = :rtid,
                converted_at  = :now,
                credit_amount = :credit,
                currency      = :curr
            WHERE code = :code AND status = 'pending'
        """), {
            "rtid":   referred_tid,
            "now":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "credit": credit,
            "curr":   currency,
            "code":   code,
        })
        conn.commit()
    logger.info("[referral] Referral converted: code=%s tenant=%s credit=%.2f %s",
                code, referred_tid, credit, currency)
    return True


def get_pending_rewards(engine: Any) -> list[dict]:
    """Return all converted (not yet rewarded) referral events for billing."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT re.id, re.referrer_tid, re.referred_tid,
                   re.credit_amount, re.currency,
                   rc.code
            FROM referral_events re
            JOIN referral_codes  rc ON rc.tenant_id = re.referrer_tid
            WHERE re.status = 'converted'
        """)).fetchall()
    return [
        {"event_id": r[0], "referrer_tid": r[1], "referred_tid": r[2],
         "credit": r[3], "currency": r[4], "code": r[5]}
        for r in rows
    ]


def mark_reward_paid(event_id: int, engine: Any) -> None:
    """Mark a referral event as rewarded after billing credit applied."""
    with engine.connect() as conn:
        conn.execute(text("""
            UPDATE referral_events
            SET status = 'rewarded', rewarded_at = :now
            WHERE id = :eid
        """), {"now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "eid": event_id})
        conn.execute(text("""
            UPDATE referral_codes
            SET total_rewarded = total_rewarded + 1
            WHERE tenant_id = (SELECT referrer_tid FROM referral_events WHERE id = :eid)
        """), {"eid": event_id})
        conn.commit()


# ═════════════════════════════════════════════════════════════════════════════
# Flask route registration
# ═════════════════════════════════════════════════════════════════════════════

def register_referral_routes(flask_app: Any, engine: Any) -> None:
    """Register /referral/* Flask routes."""
    from flask import request, redirect, session, jsonify
    from flask_login import current_user, login_required

    @flask_app.route("/referral/signup")
    def referral_signup_landing():
        """Landing page when clicking a referral link."""
        code = request.args.get("code", "")
        email = request.args.get("email", "")
        if code:
            session["referral_code"]  = code
            session["referral_email"] = email
        return redirect(f"/?referral={code}")

    @flask_app.route("/referral/record", methods=["POST"])
    def record_referral():
        """Called server-side when a referred user completes registration."""
        data  = request.get_json() or {}
        code  = data.get("code") or session.get("referral_code")
        email = data.get("email") or session.get("referral_email", "")
        if code and email:
            ok = record_referral_signup(code, email, engine)
            return jsonify({"ok": ok})
        return jsonify({"ok": False, "error": "Missing code or email"}), 400

    @flask_app.route("/referral/status")
    @login_required
    def referral_status():
        """Return referral stats for the logged-in tenant."""
        tid   = getattr(current_user, "tenant_id", None) or current_user.id
        stats = get_referral_stats(int(tid), engine)
        code  = get_or_create_referral_code(int(tid), engine)
        stats["code"]      = code
        stats["share_url"] = f"{APP_BASE_URL}/referral/signup?code={code}"
        return jsonify(stats)


# ═════════════════════════════════════════════════════════════════════════════
# Dash tab layout
# ═════════════════════════════════════════════════════════════════════════════

def render_referral_tab(tenant_id: int, tenant_name: str,
                        engine: Any, currency: str = "INR") -> html.Div:
    """
    Render the Referral Program tab for a tenant.
    Shows their unique referral link + stats.
    """
    code     = get_or_create_referral_code(tenant_id, engine)
    stats    = get_referral_stats(tenant_id, engine)
    share_url = f"{APP_BASE_URL}/referral/signup?code={code}"

    reward_str = f"₹{REFERRAL_CREDIT_INR:,}" if currency == "INR" else f"${REFERRAL_CREDIT_USD}"

    # Stat cards
    def _stat(label, value, color=C_GREEN):
        return html.Div([
            html.Div(str(value), style={"fontSize": "1.8rem", "fontWeight": 800, "color": color}),
            html.Div(label,      style={"fontSize": "0.75rem", "color": C_GRAY, "marginTop": "2px"}),
        ], style={
            "background": "#fff", "borderRadius": "10px",
            "padding": "1rem 1.4rem", "textAlign": "center",
            "boxShadow": "0 1px 3px rgba(0,0,0,0.08)",
            "border": f"1px solid {color}22",
            "minWidth": "110px",
        })

    credit_inr = stats["total_credit_inr"]
    credit_usd = stats["total_credit_usd"]
    credit_label = (f"₹{credit_inr:,.0f}" if currency == "INR"
                    else f"${credit_usd:,.2f}")

    stat_row = html.Div([
        _stat("Referrals Sent",    stats["total_sent"]),
        _stat("Pending",           stats["pending"],   C_ORANGE),
        _stat("Converted",         stats["converted"], C_BLUE),
        _stat("Rewarded",          stats["rewarded"],  C_PURPLE),
        _stat("Total Credits",     credit_label,       C_GREEN),
    ], style={"display": "flex", "gap": "1rem", "flexWrap": "wrap", "marginBottom": "1.5rem"})

    # Referral link card
    link_card = html.Div([
        html.Div("🎁 Your Referral Link", style={
            "fontWeight": 700, "fontSize": "0.95rem", "marginBottom": "0.75rem",
            "color": C_GREEN,
        }),
        html.Div([
            dbc.Input(
                value=share_url,
                readonly=True,
                id="referral-link-input",
                style={"fontSize": "0.82rem", "borderRadius": "8px 0 0 8px",
                       "background": "#f8fafc"},
            ),
            dbc.Button(
                "📋 Copy",
                id="referral-copy-btn",
                n_clicks=0,
                color="success",
                style={"borderRadius": "0 8px 8px 0", "fontWeight": 600},
            ),
        ], style={"display": "flex", "marginBottom": "0.75rem"}),
        html.Div(id="referral-copy-feedback",
                 style={"fontSize": "0.75rem", "color": C_GREEN, "minHeight": "18px"}),
        html.Hr(style={"margin": "0.75rem 0"}),
        html.Div([
            html.Span("Your referral code: ", style={"fontSize": "0.82rem", "color": C_GRAY}),
            html.Span(code, style={
                "fontFamily": "monospace", "fontWeight": 700,
                "fontSize": "1rem", "color": C_PURPLE,
                "background": "#f3e8ff", "padding": "2px 10px",
                "borderRadius": "6px",
            }),
        ]),
    ], style={
        "background": "#fff", "borderRadius": "12px",
        "padding": "1.25rem", "marginBottom": "1.5rem",
        "boxShadow": "0 1px 4px rgba(0,0,0,0.07)",
        "border": "1px solid #e2e8f0",
    })

    # How it works
    how_it_works = html.Div([
        html.Div("How It Works", style={
            "fontWeight": 700, "fontSize": "0.92rem",
            "color": C_GREEN, "marginBottom": "0.75rem",
        }),
        html.Div([
            _how_step("1", "Share your link", "Send your unique referral link to other business owners."),
            _how_step("2", "They sign up",    "Your contact signs up for InsightHub using your link."),
            _how_step("3", "They subscribe",  "When they activate a paid plan, your referral is confirmed."),
            _how_step("4", f"You earn {reward_str}", "Credit is applied to your next billing cycle automatically."),
        ], style={"display": "flex", "gap": "1rem", "flexWrap": "wrap"}),
    ], style={
        "background": "#f0fdf4", "borderRadius": "12px",
        "padding": "1.25rem", "marginBottom": "1.5rem",
        "border": "1px solid #bbf7d0",
    })

    return html.Div([
        # Header
        html.Div([
            html.Span("🤝", style={"fontSize": "1.4rem", "marginRight": "0.5rem"}),
            html.Span("Referral Program",
                      style={"fontWeight": 700, "fontSize": "1.05rem", "color": C_GREEN}),
            html.Span(f" · Earn {reward_str} per referral",
                      style={"fontSize": "0.8rem", "color": C_GRAY, "marginLeft": "0.5rem"}),
        ], style={"display": "flex", "alignItems": "center", "marginBottom": "1.25rem"}),

        stat_row,
        link_card,
        how_it_works,

        # Share buttons row
        html.Div([
            html.Div("Share via:", style={"fontSize": "0.8rem", "color": C_GRAY,
                                          "fontWeight": 600, "marginBottom": "0.5rem"}),
            html.Div([
                dbc.Button("📧 Email",     id="ref-share-email",   color="light",
                           size="sm", href=f"mailto:?subject=Join me on {APP_NAME}&body=Use my referral link: {share_url}",
                           external_link=True, style={"marginRight": "8px"}),
                dbc.Button("💬 WhatsApp", id="ref-share-whatsapp", color="light",
                           size="sm", href=f"https://wa.me/?text=Join me on {APP_NAME}! Use my link: {share_url}",
                           external_link=True, style={"marginRight": "8px"}),
                dbc.Button("🔗 LinkedIn", id="ref-share-linkedin", color="light",
                           size="sm", href=f"https://www.linkedin.com/sharing/share-offsite/?url={share_url}",
                           external_link=True),
            ]),
        ]),

        # Terms
        html.Div(
            f"Terms: Credits apply to Growth and Pro plans only. "
            f"One reward per referred tenant. Credits cannot be withdrawn as cash.",
            style={"fontSize": "0.72rem", "color": C_GRAY,
                   "marginTop": "1.5rem", "fontStyle": "italic"},
        ),

        # Copy feedback store
        dcc.Store(id="referral-code-store", data=code),
    ], style={
        "background": "#fff", "borderRadius": "12px",
        "padding": "1.25rem", "boxShadow": "0 1px 4px rgba(0,0,0,0.07)",
    })


def _how_step(num: str, title: str, desc: str) -> html.Div:
    return html.Div([
        html.Div(num, style={
            "width": "28px", "height": "28px",
            "borderRadius": "50%", "background": C_GREEN,
            "color": "#fff", "fontWeight": 700,
            "display": "flex", "alignItems": "center",
            "justifyContent": "center", "fontSize": "0.85rem",
            "marginBottom": "0.5rem",
        }),
        html.Div(title, style={"fontWeight": 600, "fontSize": "0.85rem",
                                "color": "#1f2937", "marginBottom": "2px"}),
        html.Div(desc,  style={"fontSize": "0.78rem", "color": C_GRAY,
                                "lineHeight": 1.4}),
    ], style={
        "flex": 1, "minWidth": "130px",
        "background": "#fff", "borderRadius": "10px",
        "padding": "0.8rem 1rem",
        "border": "1px solid #d1fae5",
    })
