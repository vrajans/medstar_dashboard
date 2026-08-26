"""
app.py - InsightHub Analytics Platform  (Multi-tenant SaaS)
Tabs: Overview | Sales | Purchases | Branch Compare | Upload (admin) | Users (admin)
Run:  python app.py   then open  http://127.0.0.1:8050
"""

# Load .env FIRST — before any other imports read os.getenv()
from pathlib import Path as _Path
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(_Path(__file__).parent / ".env", override=True)
except ImportError:
    pass   # python-dotenv not installed; rely on OS env vars

import io
import os
import logging
import threading
from io import BytesIO

logger = logging.getLogger(__name__)
from datetime import date, timedelta

import dash
from dash import dcc, html, Input, Output, State, dash_table, no_update, ctx
import dash_bootstrap_components as dbc
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import requests as _req
from flask import redirect, request, session as flask_session
from flask_login import login_user, logout_user, current_user

from data_loader import (
    get_data, get_upload_history, parse_upload,
    build_preview, append_upload_to_db, load_from_db,
    detect_generic_report,
)
from pdf_report  import generate_pdf
from auth        import init_auth, authenticate, list_users, create_user, \
                        update_user_role, deactivate_user, reactivate_user, reset_password
from login_page     import render_login
from tenant_portal  import render_tenants_tab, call_api, MODULE_LABELS, API_BASE

# ── Phase 1 & 2 feature imports ───────────────────────────────
from gst_report       import render_gst_tab
from yoy_report       import render_yoy_tab
from expiry_dashboard import render_expiry_tab, render_stock_tab, render_cash_credit_tab
from ai.rag           import (render_ai_chat_tab, rag_answer, agent_answer,
                               render_user_message, render_assistant_message,
                               render_agent_trace, render_anomaly_results,
                               get_anomaly_report)
from ai.memory        import init_agent_memory
from domain_config    import get_domain_config, get_domain_from_format
from domain_tabs      import render_domain_tab
from billing          import init_billing_tables, register_billing_routes, BillingEngine, PLANS
from mfa              import init_mfa_tables, register_mfa_routes, render_mfa_settings_card
from onboarding       import render_upload_rollback_tab, do_rollback
from tenant_analytics import load_tenant_df, render_tab as render_tenant_tab
from referral         import (init_referral_tables, register_referral_routes,
                               render_referral_tab, get_or_create_referral_code)
from ai.anomaly       import detect_all as _detect_anomalies, render_anomaly_banners
from alert_settings   import (render_alert_settings_tab, register_alert_settings_routes,
                               _add_channel as _alert_add_channel,
                               _delete_channel as _alert_delete_channel,
                               _toggle_channel as _alert_toggle_channel,
                               _get_channels as _alert_get_channels,
                               _render_channel_table as _render_alert_channel_table)

# ── Load pharmacy data ────────────────────────────────────────
sales_df, purchase_df, engine = get_data()

# ── Phase-1 dimensional warehouse (shadow mode) ───────────────
# Initialise + one-time backfill in the background so the star schema mirrors
# existing data for demos. Fully guarded: never blocks or breaks app startup.
def _init_warehouse_bg():
    try:
        import warehouse as _wh
        _wh.init_warehouse(engine)
        _res = _wh.backfill_all(engine)
        logger.info("[warehouse] startup backfill complete: %d tenant(s)", len(_res))
    except Exception as _we:
        logger.warning("[warehouse] startup backfill skipped (non-fatal): %s", _we)
    try:
        from ai import llm_gateway as _llm
        _llm.init_llm_config_table(engine)
    except Exception as _le:
        logger.warning("[llm] config table init skipped: %s", _le)
    try:
        import orchestrator as _orch
        _orch.init_orchestrator_tables(engine)
    except Exception as _oe:
        logger.warning("[orchestrator] table init skipped: %s", _oe)
try:
    threading.Thread(target=_init_warehouse_bg, daemon=True).start()
except Exception:
    pass

# ── Brand colours — InsightHub Design System v3 ───────────────
C_NAVY   = "#1E293B"   # nav, headers
C_BLUE   = "#2563EB"   # primary brand
C_SKY    = "#0EA5E9"   # secondary accent
C_TEAL   = "#0D9488"   # teal
C_AMBER  = "#D97706"   # warning/costs
C_PURPLE = "#4F46E5"   # indigo
C_GREEN  = "#059669"   # positive/success (kept as alias for backwards compat)
C_ORANGE = "#D97706"   # alias
_BRANCH_PALETTE = [C_BLUE, C_SKY, C_TEAL, C_PURPLE, C_AMBER]

def get_branch_color_map():
    branches = sorted(set(
        (sales_df["branch"].unique().tolist()    if not sales_df.empty    else []) +
        (purchase_df["branch"].unique().tolist() if not purchase_df.empty else [])
    ))
    return {b: _BRANCH_PALETTE[i % len(_BRANCH_PALETTE)] for i, b in enumerate(branches)}

CHART_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor ="rgba(0,0,0,0)",
    font=dict(family="Inter,system-ui,-apple-system,sans-serif", size=11, color="#1E293B"),
    margin=dict(l=8, r=8, t=30, b=8),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                bgcolor="rgba(0,0,0,0)", font=dict(size=11)),
    xaxis=dict(gridcolor="#F1F5F9", linecolor="#E2E8F0", tickfont=dict(size=10),
               showgrid=False, zeroline=False),
    yaxis=dict(gridcolor="#F1F5F9", linecolor="rgba(0,0,0,0)", tickfont=dict(size=10),
               showgrid=True, zeroline=False),
    hoverlabel=dict(bgcolor="#FFFFFF", bordercolor="#E2E8F0",
                    font=dict(family="Inter,system-ui,sans-serif", size=12, color="#1E293B")),
)

# ── Threshold configuration ───────────────────────────────────
THRESHOLDS = {
    "margin_pct_min":      20.0,
    "daily_sales_min":  50_000,
    "return_pct_max":       5.0,
    "purchase_ratio_max":  85.0,
}

# ── Date helpers ──────────────────────────────────────────────
def _data_date_bounds():
    dates = []
    if not sales_df.empty and "bill_date" in sales_df.columns:
        valid = sales_df["bill_date"].dropna()
        if not valid.empty:
            dates += [valid.min(), valid.max()]
    if not purchase_df.empty and "grn_date" in purchase_df.columns:
        valid = purchase_df["grn_date"].dropna()
        if not valid.empty:
            dates += [valid.min(), valid.max()]
    if dates:
        return min(dates).date(), max(dates).date()
    today = date.today()
    return date(today.year, today.month, 1), today

def apply_date_filter(df, start_date, end_date, date_col):
    if df.empty or date_col not in df.columns:
        return df
    s = pd.to_datetime(start_date) if start_date else None
    e = pd.to_datetime(end_date)   if end_date   else None
    if s is not None:
        df = df[df[date_col] >= s]
    if e is not None:
        df = df[df[date_col] <= e]
    return df

def get_prev_period(df, start_date, end_date, date_col):
    if df.empty or not start_date or not end_date or date_col not in df.columns:
        return pd.DataFrame()
    start = pd.to_datetime(start_date)
    end   = pd.to_datetime(end_date)
    days  = (end - start).days + 1
    prev_end   = start - pd.Timedelta(days=1)
    prev_start = prev_end - pd.Timedelta(days=days - 1)
    return df[(df[date_col] >= prev_start) & (df[date_col] <= prev_end)].copy()

def pct_delta(curr, prev):
    try:
        if prev is not None and abs(float(prev)) > 0.001:
            return round((float(curr) - float(prev)) / abs(float(prev)) * 100, 1)
    except (TypeError, ValueError):
        pass
    return None

def delta_el(delta):
    if delta is None:
        return []
    cls   = "up" if delta >= 0 else "dn"
    arrow = "▲" if delta >= 0 else "▼"
    return [html.Div([
        html.Span("{} {:.1f}%".format(arrow, abs(delta)), className=cls),
        html.Span(" vs prior", className="prior"),
    ], className="kpi-delta")]

def empty_state(icon="", title="No data", sub="Adjust filters or upload data."):
    return html.Div([
        html.Div(icon,  className="empty-icon"),
        html.Div(title, className="empty-title"),
        html.Div(sub,   className="empty-sub"),
    ], className="empty-state")

# ── Alert helpers ─────────────────────────────────────────────
def check_alerts(s, p):
    alerts = []
    if not s.empty:
        if "margin_pct" in s.columns:
            avg_m = s["margin_pct"].mean()
            if avg_m < THRESHOLDS["margin_pct_min"]:
                alerts.append({"level":"danger",
                    "msg":"Avg Margin {:.1f}% is below minimum {:.0f}%. Review pricing.".format(
                        avg_m, THRESHOLDS["margin_pct_min"])})
        if "net_amount" in s.columns:
            avg_d = s["net_amount"].mean()
            if avg_d < THRESHOLDS["daily_sales_min"]:
                alerts.append({"level":"warning",
                    "msg":"Avg daily sales {} below target {}. Consider promotions.".format(
                        fmt_inr(avg_d), fmt_inr(THRESHOLDS["daily_sales_min"]))})
        if "cash_return" in s.columns and "net_amount" in s.columns:
            ret = s["cash_return"].sum()
            sal = s["net_amount"].sum()
            if sal > 0 and (ret / sal * 100) > THRESHOLDS["return_pct_max"]:
                alerts.append({"level":"warning",
                    "msg":"Returns {:.1f}% of sales — above {:.0f}% threshold.".format(
                        ret/sal*100, THRESHOLDS["return_pct_max"])})
    if not s.empty and not p.empty:
        sal = s["net_amount"].sum() if "net_amount" in s.columns else 0
        pur = p["net_amount"].sum() if "net_amount" in p.columns else 0
        if sal > 0 and (pur/sal*100) > THRESHOLDS["purchase_ratio_max"]:
            alerts.append({"level":"warning",
                "msg":"Purchase/Sales ratio {:.1f}% — above {:.0f}% threshold. Cash-flow risk.".format(
                    pur/sal*100, THRESHOLDS["purchase_ratio_max"])})
    return alerts

def render_alert_banners(alerts):
    if not alerts:
        return html.Div(
            html.Span("✅  All KPIs within thresholds.",
                      style={"fontSize":"0.78rem","color":C_GREEN,"fontWeight":600}),
            style={"background":"#e8f5e9","border":"1px solid #a8d5a8","borderRadius":"8px",
                   "padding":"0.45rem 0.9rem","marginBottom":"0.8rem"})
    icon_map = {"danger":"⚠️","warning":"\U0001f4c9","info":"ℹ️"}
    return html.Div([
        dbc.Alert([html.Strong("{} ".format(icon_map.get(a["level"],"⚠"))),
                   html.Span(a["msg"])],
                  color=a["level"], dismissable=True,
                  style={"fontSize":"0.8rem","padding":"0.45rem 0.9rem","marginBottom":"0.35rem"})
        for a in alerts
    ], style={"marginBottom":"0.6rem"})

# ── UI helpers ────────────────────────────────────────────────
def fmt_inr(val):
    try:
        val = float(val)
    except (TypeError, ValueError):
        return str(val)
    if val >= 100000: return "Rs.{:.2f}L".format(val/100000)
    if val >= 1000:   return "Rs.{:.1f}K".format(val/1000)
    return "Rs.{:.0f}".format(val)

def kpi_card(label, value, sub="", color_class="", icon="*", delta=None):
    return html.Div(
        [html.Div(icon, className="kpi-icon"),
         html.Div(label, className="kpi-label"),
         html.Div(value, className="kpi-value")]
        + delta_el(delta)
        + [html.Div(sub, className="kpi-sub")],
        className="kpi-card {}".format(color_class))

def chart_card(title, figure, height=280):
    return html.Div([
        html.Div(title, className="chart-card-title"),
        dcc.Graph(figure=figure,
                  config={"displayModeBar": False, "responsive": True},
                  style={"height": f"{height}px"}),
    ], className="chart-card")

def get_filter_options():
    sb = sales_df["branch"].unique().tolist()    if not sales_df.empty    else []
    pb = purchase_df["branch"].unique().tolist() if not purchase_df.empty else []
    return ["All"] + sorted(set(sb + pb))

def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0,2,4))

# ── Sidebar download bar ──────────────────────────────────────
def download_bar():
    def btn(label, bid, col):
        return dbc.Button(label, id=bid, color=col, size="sm", outline=True,
                          style={"fontSize":"0.71rem","fontWeight":600})
    return html.Div([
        html.Div("Export", className="sidebar-label",
                 style={"marginBottom":"0.3rem"}),
        html.Div([
            btn("Sales CSV",      "btn-dl-sales-csv",  "success"),
            btn("Sales Excel",    "btn-dl-sales-xlsx", "success"),
            btn("Purchase CSV",   "btn-dl-purch-csv",  "primary"),
            btn("Purchase Excel", "btn-dl-purch-xlsx", "primary"),
        ], id="sidebar-pharmacy-exports",
           style={"display":"grid","gridTemplateColumns":"1fr 1fr","gap":"4px",
                  "marginBottom":"4px"}),
        dbc.Button("\U0001f4c4 PDF Report", id="btn-dl-pdf", color="dark", size="sm",
                   style={"width":"100%","fontSize":"0.71rem","fontWeight":600}),
    ])

# ── Sidebar ───────────────────────────────────────────────────
def make_sidebar():
    branches = get_filter_options()
    data_min, data_max = _data_date_bounds()
    return html.Div([
        html.Div([
            html.Span("\U0001f4ca", id="sidebar-logo-icon", style={
                "fontSize":"1.1rem","lineHeight":"1",
            }),
            html.Span("InsightHub", style={
                "fontWeight":800,"fontSize":"0.88rem","color":C_NAVY,
                "letterSpacing":"-0.2px","marginLeft":"6px",
            }),
        ], style={"display":"flex","alignItems":"center","justifyContent":"center",
                  "marginBottom":"0.25rem"}),
        html.Div("Analytics", style={
            "fontWeight":500,"textAlign":"center","fontSize":"0.65rem",
            "color":"#64748B","marginBottom":"0.9rem","letterSpacing":"0.04em",
        }),
        html.Div(className="s-divider"),
        # Admin-only: view any customer's analytics (hidden for tenant/viewer roles)
        html.Div([
            html.Div("Customer", className="sidebar-label"),
            dcc.Dropdown(id="admin-tenant-select",
                         options=[{"label": "MedStar (internal)", "value": 0}],
                         value=0, clearable=False,
                         style={"fontSize": "0.75rem", "marginBottom": "0.6rem"}),
        ], id="admin-tenant-wrap", style={"display": "none"}),
        html.Div("Branch", className="sidebar-label", id="sidebar-filter-label"),
        dcc.Dropdown(id="filter-branch",
                     options=[{"label":b,"value":b} for b in branches],
                     value="All", clearable=False,
                     style={"fontSize":"0.75rem","marginBottom":"0.6rem"}),
        html.Div("Date Range", className="sidebar-label"),
        html.Div([
            html.Button("This M", id="qs-this", n_clicks=0, className="qs-btn"),
            html.Button("Last M", id="qs-last", n_clicks=0, className="qs-btn"),
            html.Button("3M",     id="qs-3m",   n_clicks=0, className="qs-btn"),
            html.Button("All",    id="qs-all",  n_clicks=0, className="qs-btn"),
        ], className="quick-select-group"),
        html.Div(
            dcc.DatePickerRange(
                id="filter-date",
                start_date=str(data_min), end_date=str(data_max),
                min_date_allowed="2020-01-01", max_date_allowed="2030-12-31",
                display_format="DD MMM YY", style={"width":"100%"},
            ),
            className="date-picker-wrap", style={"marginBottom":"0.75rem"},
        ),
        html.Div(className="s-divider"),
        download_bar(),
        html.Div(className="s-divider"),
        html.Div("Data Status", className="sidebar-label", id="sidebar-data-status-label"),
        html.Div(id="sidebar-data-status"),
        html.Div(className="s-divider"),
        html.Div("Sources", className="sidebar-label", id="sidebar-sources-label"),
        html.Div(id="sidebar-sources"),
        html.Div(className="s-divider"),
        html.Div(id="sidebar-domain-badge"),
    ], className="sidebar", style={"width":"210px","minWidth":"210px"})

# ── Navbar ────────────────────────────────────────────────────
def make_navbar():
    return dbc.Navbar(
        dbc.Container([
            # Left: logo + product name + tenant subtitle
            html.Div([
                html.Div([
                    html.Span(id="navbar-brand-icon", children="📊", style={
                        "fontSize":"1.25rem","marginRight":"9px","verticalAlign":"middle",
                        "display":"inline-flex","alignItems":"center","justifyContent":"center",
                        "width":"32px","height":"32px","borderRadius":"8px",
                        "background":"linear-gradient(135deg,#2563EB,#0EA5E9)",
                    }),
                    html.Span(id="navbar-brand-name", children="InsightHub", style={
                        "fontWeight":800,"fontSize":"1.1rem","letterSpacing":"-0.3px",
                        "color":"#FFFFFF","verticalAlign":"middle",
                    }),
                ], style={"display":"flex","alignItems":"center"}),
                html.Span(id="navbar-brand-subtitle",
                          children="Analytics Platform",
                          style={
                              "fontSize":"0.7rem","color":"rgba(255,255,255,0.40)",
                              "marginLeft":"41px","letterSpacing":"0.03em",
                          }),
            ], style={"lineHeight":"1.3"}),

            # Center: period label
            html.Span(id="navbar-period-label", style={
                "fontSize":"0.72rem","color":"rgba(255,255,255,0.50)",
                "position":"absolute","left":"50%","transform":"translateX(-50%)",
            }),

            # Right: user chip + sign out
            html.Div([
                html.Div(id="navbar-user-info",
                         style={"display":"flex","alignItems":"center","gap":"8px"}),
            ], style={"display":"flex","alignItems":"center","marginLeft":"auto","gap":"8px"}),
        ], fluid=True, style={"position":"relative"}),
        dark=True,
        style={
            "background": C_NAVY,
            "padding":"0 1.25rem",
            "height":"62px",
            "boxShadow":"0 1px 0 rgba(255,255,255,0.06),0 2px 12px rgba(0,0,0,0.25)",
            "position":"sticky","top":"0","zIndex":"200",
        },
    )

# ── Dash app ──────────────────────────────────────────────────
app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP],
    suppress_callback_exceptions=True,
    title="InsightHub Analytics",
)

# ── Init auth (must happen after app is created) ──────────────
from data_loader import DB_PATH
auth_engine = init_auth(app.server, DB_PATH)
import os as _os
app.server.secret_key = _os.environ.get("FLASK_SECRET_KEY", "insighthub-secret-change-in-prod-2026")

# ── Phase 1 & 2: init DB tables ──────────────────────────────
try:
    init_billing_tables(auth_engine)
    init_mfa_tables(auth_engine)
    init_referral_tables(auth_engine)
    from password_reset import init_reset_tables
    init_reset_tables(auth_engine)
    from alerts import init_alert_tables
    init_alert_tables(auth_engine)
    from scheduler import start_scheduler
    start_scheduler(auth_engine)
except Exception as _init_err:
    print(f"[app] Phase 1/2 init warning: {_init_err}")

# ── Multi-Agent Memory (SQLite — zero external deps) ─────────
_agent_memory = None
try:
    _agent_memory = init_agent_memory(DB_PATH)
    print("[app] Multi-Agent Memory ready")
except Exception as _mem_err:
    print(f"[app] Agent Memory init warning: {_mem_err}")


# ── Local SQLite tenant store (fallback when FastAPI is offline) ──────────────
def _init_local_tenants():
    """Create a local tenants table in the auth SQLite DB if it doesn't exist."""
    from sqlalchemy import text as _text
    try:
        with auth_engine.connect() as conn:
            conn.execute(_text("""
                CREATE TABLE IF NOT EXISTS local_tenants (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    name          TEXT NOT NULL,
                    slug          TEXT UNIQUE NOT NULL,
                    domain_type   TEXT DEFAULT 'pharmacy',
                    plan          TEXT DEFAULT 'basic',
                    contact_email TEXT DEFAULT '',
                    is_active     INTEGER DEFAULT 1,
                    created_at    TEXT DEFAULT (datetime('now')),
                    country       TEXT DEFAULT 'IN',
                    currency      TEXT DEFAULT 'INR'
                )
            """))
            # Add currency/country columns to existing DBs
            for _col, _def in [("country", "TEXT DEFAULT 'IN'"), ("currency", "TEXT DEFAULT 'INR'")]:
                try:
                    conn.execute(_text(f"ALTER TABLE local_tenants ADD COLUMN {_col} {_def}"))
                except Exception:
                    pass
            conn.commit()
    except Exception:
        pass

_init_local_tenants()


def _create_tenant_local(name, slug, domain_type, plan, email) -> tuple[bool, str]:
    """Insert a tenant directly into local SQLite. Returns (success, message)."""
    from sqlalchemy import text as _text
    try:
        with auth_engine.connect() as conn:
            conn.execute(_text("""
                INSERT INTO local_tenants (name, slug, domain_type, plan, contact_email)
                VALUES (:name, :slug, :domain, :plan, :email)
            """), {"name": name, "slug": slug, "domain": domain_type,
                   "plan": plan, "email": email})
            conn.commit()
        return True, f"Tenant '{name}' created locally (FastAPI offline — data saved to SQLite)."
    except Exception as exc:
        return False, str(exc)


def _list_tenants_local() -> list[dict]:
    """Read tenants from local SQLite."""
    from sqlalchemy import text as _text
    try:
        with auth_engine.connect() as conn:
            rows = conn.execute(_text(
                "SELECT id, name, slug, domain_type, plan, is_active, "
                "contact_email, created_at FROM local_tenants ORDER BY id DESC"
            )).fetchall()
        return [
            {
                "id":      r[0],
                "Name":    r[1],
                "Slug":    r[2],
                "Domain":  (r[3] or "pharmacy").capitalize(),
                "Plan":    (r[4] or "basic").capitalize(),
                "Status":  "Active" if r[5] else "Inactive",
                "Contact": r[6] or "",
                "Created": (r[7] or "")[:10],
            }
            for r in rows
        ]
    except Exception:
        return []


def _tenant_name_by_id(tid) -> str:
    """Look up a tenant's display name by id (for admin impersonation)."""
    try:
        for t in _list_tenants_neon() or _list_tenants_local():
            if int(t["id"]) == int(tid):
                return t.get("Name", "")
    except Exception:
        pass
    return ""


# ══════════════════════════════════════════════════════════════
# NEON (cloud) tenant data — the customer source of truth
# ══════════════════════════════════════════════════════════════
_NEON_ENGINE = None

def _get_neon_engine():
    """Sync SQLAlchemy engine for the Neon Postgres DB (customer data lives here).
    Returns None if not configured/reachable — callers must handle gracefully."""
    global _NEON_ENGINE
    if _NEON_ENGINE is not None:
        return _NEON_ENGINE
    dsn = os.getenv("PG_DSN_SYNC") or os.getenv("PG_DSN")
    if not dsn:
        return None
    dsn = dsn.replace("+asyncpg", "+psycopg2").replace("ssl=require", "sslmode=require")
    try:
        from sqlalchemy import create_engine as _ce
        _NEON_ENGINE = _ce(dsn, pool_pre_ping=True)
        return _NEON_ENGINE
    except Exception as _ne:
        logger.warning("[neon] engine init failed: %s", _ne)
        return None


def _list_tenants_neon() -> list[dict]:
    """Customers from Neon via the API — the same source the Tenants tab writes to."""
    try:
        status, data = call_api("GET", "/tenants")
        if status == 200 and isinstance(data, list):
            return [{"id": t.get("id"), "Name": t.get("name", ""),
                     "Domain": (t.get("domain_type") or "generic"),
                     "Plan": (t.get("plan") or "basic")} for t in data]
    except Exception as _te:
        logger.debug("[neon] tenant list failed: %s", _te)
    return []


def _neon_tenant_domain(tid) -> str:
    for t in _list_tenants_neon():
        try:
            if int(t["id"]) == int(tid):
                return (t.get("Domain") or "generic").lower()
        except Exception:
            continue
    return "generic"


def _load_tenant_from_neon(tid):
    """Return (sales_df, purchases_df) for a customer from Neon (Postgres-safe SQL)."""
    eng = _get_neon_engine()
    if eng is None or not tid:
        return pd.DataFrame(), pd.DataFrame()
    def _q(table):
        try:
            df = pd.read_sql_query(text(f"SELECT * FROM {table} WHERE tenant_id = :t"),
                                   eng, params={"t": int(tid)})
        except Exception:
            return pd.DataFrame()
        if not df.empty:
            if "bill_date" in df.columns:
                df["bill_date"] = pd.to_datetime(df["bill_date"], errors="coerce")
            if "net_amount" in df.columns:
                df["net_amount"] = pd.to_numeric(df["net_amount"], errors="coerce").fillna(0)
        return df
    return _q("sales"), _q("purchases")


def _append_upload_neon(store_data, tid, domain):
    """Admin upload-on-behalf: write a customer's file to Neon + rebuild their marts.
    Postgres-safe (does not use the SQLite-specific append_upload_to_db)."""
    eng = _get_neon_engine()
    if eng is None:
        return 0, "Neon is not configured (PG_DSN_SYNC missing)."
    try:
        df = pd.read_json(io.StringIO(store_data["df_json"]))
        rtype = store_data.get("report_type", "")
        table = "purchases" if rtype in ("purchase", "generic_purchases") else "sales"
        for col in ("bill_date", "grn_date", "invoice_date"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
        df["tenant_id"] = int(tid)
        # ensure scoping columns exist on the Neon table (per-statement ALTER)
        from sqlalchemy import inspect as _insp
        try:
            existing = {c["name"] for c in _insp(eng).get_columns(table)}
        except Exception:
            existing = set()
        for col, typ in (("tenant_id", "INTEGER"), ("upload_id", "INTEGER"), ("supplier_name", "TEXT")):
            if existing and col not in existing:
                try:
                    with eng.begin() as _c:
                        _c.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typ}"))
                    existing.add(col)
                except Exception:
                    pass
        if existing:
            df = df[[c for c in df.columns if c in existing]]
        df.to_sql(table, eng, if_exists="append", index=False)
        # rebuild this customer's warehouse marts on Neon so the customer app updates
        try:
            import warehouse as _wh
            _wh.load_tenant_to_warehouse(eng, int(tid), (domain or "generic"))
        except Exception as _we:
            logger.warning("[neon upload] mart rebuild failed: %s", _we)
        return len(df), None
    except Exception as _ue:
        logger.warning("[neon upload] failed: %s", _ue)
        return 0, str(_ue)


def _get_active_domain(tenant_id: int = 0) -> str:
    """Return the active domain for a tenant.

    Priority:
      1. current_user.tenant_domain — stored in SQLite at user-creation time (fastest)
      2. Agent Memory 'detected_domain' — set after a file upload (most accurate)
      3. FastAPI or local SQLite tenant record
      4. Fallback: 'pharmacy'
    """
    # 1. Read directly from the logged-in user object — zero network calls
    try:
        from flask_login import current_user as _cu
        if _cu.is_authenticated and _cu.is_tenant_user():
            dom = getattr(_cu, "tenant_domain", None)
            if dom and dom != "pharmacy" or (dom == "pharmacy" and tenant_id == 0):
                return dom
    except Exception:
        pass

    # 2. Check agent memory (overrides after data upload)
    if _agent_memory is not None and tenant_id:
        try:
            prefs = _agent_memory.get_preferences(tenant_id)
            if prefs.get("detected_domain"):
                return prefs["detected_domain"]
        except Exception:
            pass

    if not tenant_id:
        return "pharmacy"

    # 3a. Try local SQLite tenants table (fast, no network)
    try:
        from sqlalchemy import text as _text
        with auth_engine.connect() as conn:
            row = conn.execute(
                _text("SELECT domain_type FROM local_tenants WHERE id = :tid"),
                {"tid": tenant_id},
            ).fetchone()
            if row and row[0]:
                return row[0]
    except Exception:
        pass

    # 3b. Try FastAPI (last resort — network call)
    try:
        from tenant_portal import call_api
        status, data = call_api("GET", f"/tenants/{tenant_id}")
        if status == 200 and isinstance(data, dict):
            domain = data.get("domain_type", "")
            if domain:
                if _agent_memory is not None:
                    try:
                        _agent_memory.save_preferences(tenant_id, {"detected_domain": domain})
                    except Exception:
                        pass
                return domain
    except Exception:
        pass

    return "pharmacy"

# ── Register new Flask routes ─────────────────────────────────
try:
    register_billing_routes(app.server, auth_engine)
    register_mfa_routes(app.server, auth_engine)
    register_referral_routes(app.server, auth_engine)
    from password_reset import register_password_reset_routes
    register_password_reset_routes(app.server, auth_engine)
    from ccpa import register_ccpa_routes, init_ccpa_tables
    init_ccpa_tables(auth_engine)
    register_ccpa_routes(app.server, auth_engine)
    register_alert_settings_routes(app.server, auth_engine)
    from google_sso import register_google_sso_routes
    register_google_sso_routes(app.server, auth_engine)
    from integrations.quickbooks import register_qb_routes, init_qb_tables
    init_qb_tables(auth_engine)
    register_qb_routes(app.server, auth_engine)
except Exception as _route_err:
    print(f"[app] Route registration warning: {_route_err}")

# ── Stripe billing routes ─────────────────────────────────────
try:
    from stripe_billing import register_stripe_routes
    register_stripe_routes(app.server, engine)
except Exception as _stripe_err:
    print(f"[app] Stripe init warning: {_stripe_err}")

# ── Self-serve signup routes ──────────────────────────────────
try:
    from signup import register_signup_routes
    _app_base_url = _os.environ.get("APP_BASE_URL", "http://localhost:8050")
    register_signup_routes(app.server, engine, base_url=_app_base_url)
except Exception as _signup_err:
    print(f"[app] Signup init warning: {_signup_err}")

# ── Flask auth routes ─────────────────────────────────────────

def _warm_up_api():
    """Background ping to wake the FastAPI service (free tier spins down after 15 min)."""
    try:
        _req.get(f"{API_BASE}/docs", timeout=90)
    except Exception:
        pass

@app.server.route("/login", methods=["GET","POST"])
def login_route():
    if current_user.is_authenticated:
        return redirect("/")
    error = None
    username_val = ""
    next_url = request.args.get("next", "/")
    if request.method == "GET":
        # Pre-warm the FastAPI service in background so it's ready by login time
        threading.Thread(target=_warm_up_api, daemon=True).start()
    if request.method == "POST":
        username_val = request.form.get("username","").strip()
        password     = request.form.get("password","")
        next_url     = request.form.get("next", "/")
        user = authenticate(username_val, password)
        if user:
            login_user(user, remember=True)
            # Also obtain a FastAPI JWT so tenant_portal.call_api() works
            try:
                api_resp = _req.post(
                    f"{API_BASE}/auth/login",
                    json={"username": username_val, "password": password},
                    timeout=45,  # free tier may take 30-60s to wake up
                )
                if api_resp.ok:
                    api_data = api_resp.json()
                    flask_session["api_access_token"]  = api_data.get("access_token")
                    flask_session["api_refresh_token"] = api_data.get("refresh_token")
                else:
                    print(f"[Auth] FastAPI JWT bridge failed: {api_resp.status_code} {api_resp.text[:200]}")
            except Exception as _jwt_err:
                print(f"[Auth] FastAPI JWT bridge error: {_jwt_err}")
            return redirect(next_url or "/")
        error = "Invalid username or password."
    return render_login(error=error, next_url=next_url, username_val=username_val)

@app.server.route("/logout")
def logout_route():
    # Revoke the FastAPI refresh token if one is stored
    refresh_tok = flask_session.pop("api_refresh_token", None)
    if refresh_tok:
        access_tok = flask_session.get("api_access_token")
        try:
            _req.post(
                f"{API_BASE}/auth/logout",
                json={"refresh_token": refresh_tok},
                headers={"Authorization": f"Bearer {access_tok}"} if access_tok else {},
                timeout=3,
            )
        except Exception:
            pass
    flask_session.pop("api_access_token", None)
    logout_user()
    return redirect("/login")

# ── Protect all Dash routes ───────────────────────────────────
@app.server.before_request
def require_login():
    public_prefixes = ("/login", "/_dash-", "/assets/", "/_reload-hash",
                       "/_favicon", "/favicon")
    if any(request.path.startswith(p) for p in public_prefixes):
        return None
    if not current_user.is_authenticated:
        return redirect("/login?next={}".format(request.path))
    return None

# ── Layout ────────────────────────────────────────────────────
app.layout = html.Div([
    make_navbar(),
    dcc.Store(id="data-version",      data=0),
    dcc.Store(id="upload-raw-store",  data=None),
    dcc.Store(id="upload-prev-store", data=None),
    dcc.Store(id="ai-chat-history",   data=[]),
    dcc.Store(id="ai-kpi-context",    data={}),
    dcc.Download(id="dl-sales-csv"),
    dcc.Download(id="dl-sales-xlsx"),
    dcc.Download(id="dl-purch-csv"),
    dcc.Download(id="dl-purch-xlsx"),
    dcc.Download(id="dl-pdf"),

    html.Div([
        make_sidebar(),
        html.Div([
            dcc.Tabs(id="main-tabs", value="overview",
                     className="custom-tabs-container",
                     children=[
                # ── Core tabs (always visible) ────────────────────────────
                dcc.Tab(label="Overview",       value="overview",
                        id="tab-overview",
                        className="custom-tab", selected_className="custom-tab--selected"),
                dcc.Tab(label="Sales",          value="sales",
                        id="tab-sales",
                        className="custom-tab", selected_className="custom-tab--selected"),
                dcc.Tab(label="Purchases",      value="purchases",
                        id="tab-purchases",
                        className="custom-tab", selected_className="custom-tab--selected"),
                dcc.Tab(label="Branch Compare", value="compare",
                        id="tab-compare",
                        className="custom-tab", selected_className="custom-tab--selected"),
                # ── Phase 1 new tabs ──────────────────────────────────────
                dcc.Tab(label="YoY Analysis",   value="yoy",
                        id="tab-yoy",
                        className="custom-tab", selected_className="custom-tab--selected"),
                dcc.Tab(label="GST / Tax",      value="gst",
                        id="tab-gst",
                        className="custom-tab", selected_className="custom-tab--selected"),
                dcc.Tab(label="Expiry",         value="expiry",
                        id="tab-expiry",
                        className="custom-tab", selected_className="custom-tab--selected"),
                dcc.Tab(label="Stock",          value="stock",
                        id="tab-stock",
                        className="custom-tab", selected_className="custom-tab--selected"),
                dcc.Tab(label="Cash/Credit",    value="cashcredit",
                        id="tab-cashcredit",
                        className="custom-tab", selected_className="custom-tab--selected"),
                # ── Phase 2 new tabs ──────────────────────────────────────
                dcc.Tab(label="AI Chat 🤖",    value="aichat",
                        id="tab-aichat",
                        className="custom-tab", selected_className="custom-tab--selected"),
                dcc.Tab(label="Referrals 🤝",  value="referral",
                        id="tab-referral",
                        className="custom-tab", selected_className="custom-tab--selected"),
                # ── Admin-only tabs (hidden by RBAC) ──────────────────────
                dcc.Tab(label="Upload Data",    value="upload",
                        id="tab-upload",
                        className="custom-tab", selected_className="custom-tab--selected",
                        style={"display":"none"}),
                dcc.Tab(label="Upload History", value="rollback",
                        id="tab-rollback",
                        className="custom-tab", selected_className="custom-tab--selected",
                        style={"display":"none"}),
                dcc.Tab(label="Users",          value="users",
                        id="tab-users",
                        className="custom-tab", selected_className="custom-tab--selected",
                        style={"display":"none"}),
                dcc.Tab(label="Tenants",        value="tenants",
                        id="tab-tenants",
                        className="custom-tab", selected_className="custom-tab--selected",
                        style={"display":"none"}),
                dcc.Tab(label="Billing",        value="billing",
                        id="tab-billing",
                        className="custom-tab", selected_className="custom-tab--selected",
                        style={"display":"none"}),
                dcc.Tab(label="🔔 Alerts",      value="alerts",
                        id="tab-alerts",
                        className="custom-tab", selected_className="custom-tab--selected",
                        style={"display":"none"}),
                dcc.Tab(label="🤖 AI Settings", value="ai_settings",
                        id="tab-ai-settings",
                        className="custom-tab", selected_className="custom-tab--selected",
                        style={"display":"none"}),
            ]),
            html.Div(id="tab-content", style={"padding":"1rem 0.5rem"}),
        ], style={"flex":1,"padding":"0.5rem 1rem","overflowY":"auto"}),
    ], style={"display":"flex","height":"calc(100vh - 62px)","overflow":"hidden"}),
])

# ── Navbar user info callback ─────────────────────────────────
_TAB_SHOW = {}               # visible
_TAB_HIDE = {"display":"none"}  # hidden

@app.callback(
    Output("navbar-user-info",      "children"),
    Output("navbar-brand-icon",     "children"),
    Output("navbar-brand-name",     "children"),
    Output("navbar-brand-subtitle", "children"),
    # Domain-adaptive tab labels
    Output("tab-sales",      "label"),
    Output("tab-purchases",  "label"),
    Output("tab-compare",    "label"),
    Output("tab-gst",        "label"),
    Output("tab-stock",      "label"),
    Output("tab-cashcredit", "label"),
    # Domain-adaptive tab visibility
    Output("tab-sales",      "style"),
    Output("tab-purchases",  "style"),
    Output("tab-compare",    "style"),
    Output("tab-gst",        "style"),
    Output("tab-expiry",     "style"),
    Output("tab-stock",      "style"),
    Output("tab-cashcredit", "style"),
    # Admin RBAC tabs
    Output("tab-upload",     "style"),
    Output("tab-rollback",   "style"),
    Output("tab-users",      "style"),
    Output("tab-tenants",    "style"),
    Output("tab-billing",    "style"),
    Output("tab-alerts",     "style"),
    Output("tab-ai-settings","style"),
    Output("admin-tenant-select", "options"),
    Output("admin-tenant-wrap",   "style"),
    Input("data-version",    "data"),
)
def update_navbar_user(_v):
    try:
        u           = current_user
        authed      = u.is_authenticated
        is_admin    = authed and u.is_admin()
        is_tenant   = authed and u.is_tenant_user()
        is_ca       = authed and getattr(u, "role", "") == "ca"
        display     = u.display_name if authed else "Guest"
        role        = u.role_label   if authed else ""
        role_col    = u.role_color   if authed else C_BLUE
        tenant_name = u.tenant_name  if (authed and is_tenant) else None
        tenant_id   = u.tenant_id    if (authed and is_tenant) else None
    except Exception:
        is_admin    = False
        is_tenant   = False
        is_ca       = False
        display     = "Guest"
        role        = ""
        role_col    = C_BLUE
        tenant_name = None
        tenant_id   = None

    # ── Detect domain for this user ──────────────────────────────────────────
    try:
        from domain_config import get_domain_config
        if is_tenant and tenant_id:
            active_domain = _get_active_domain(int(tenant_id))
        else:
            active_domain = "pharmacy"  # InsightHub internal = pharmacy default
        domain_cfg = get_domain_config(active_domain)
    except Exception:
        active_domain = "pharmacy"
        domain_cfg    = {}

    # ── Navbar brand ─────────────────────────────────────────────────────────
    brand_icon = domain_cfg.get("icon", "🏢") + " "
    if is_tenant and tenant_name:
        brand_name     = tenant_name
        brand_subtitle = f"  {domain_cfg.get('label', 'Analytics')} · Powered by InsightHub"
    else:
        brand_name     = "InsightHub"
        brand_subtitle = "  Analytics Platform"

    # ── MFA indicator ────────────────────────────────────────────────────────
    mfa_icon = []
    try:
        from sqlalchemy import text
        with auth_engine.connect() as conn:
            row = conn.execute(text("SELECT mfa_enabled FROM users WHERE id = :uid"),
                               {"uid": current_user.id}).fetchone()
        if row and row[0]:
            mfa_icon = [html.Span("🔒", title="MFA enabled",
                                  style={"fontSize":"0.75rem","opacity":"0.8"})]
    except Exception:
        pass

    # Initials avatar
    initials = "".join(w[0].upper() for w in display.split() if w)[:2] or "?"
    user_info = html.Div([
        # Avatar circle with initials
        html.Div(initials, style={
            "width":"32px","height":"32px","borderRadius":"50%",
            "background":f"linear-gradient(135deg,{role_col},{C_SKY})",
            "display":"flex","alignItems":"center","justifyContent":"center",
            "fontSize":"0.7rem","fontWeight":700,"color":"white","flexShrink":0,
        }),
        # Name + role
        html.Div([
            html.Div(display, style={"fontSize":"0.78rem","fontWeight":600,"color":"#F1F5F9","lineHeight":"1.2"}),
            html.Div(role,    style={"fontSize":"0.62rem","color":"rgba(255,255,255,0.45)","lineHeight":"1.2"}),
        ], style={"lineHeight":"1.2"}),
        *mfa_icon,
        # Divider
        html.Div(style={"width":"1px","height":"22px","background":"rgba(255,255,255,0.12)","margin":"0 2px"}),
        # Sign out
        html.A("Sign Out", href="/logout", style={
            "fontSize":"0.72rem","color":"rgba(255,255,255,0.55)",
            "textDecoration":"none","whiteSpace":"nowrap",
            "transition":"color 0.15s",
        }),
    ], style={"display":"flex","alignItems":"center","gap":"8px",
              "background":"rgba(255,255,255,0.05)","borderRadius":"8px",
              "padding":"5px 10px 5px 8px","border":"1px solid rgba(255,255,255,0.08)"})

    # ── Domain-adaptive tab labels & visibility ──────────────────────────────
    # Map domain config tab IDs to the dash tab slots we have:
    #   sales slot     → revenue / sales / income
    #   purchases slot → costs / purchases / expenses
    #   compare slot   → customers / branch compare
    #   gst slot       → tax report / gst
    #   stock slot     → inventory / stock
    #   cashcredit slot→ cash flow / cash/credit

    _domain_tabs = {t["id"] for t in domain_cfg.get("tabs", [])}

    def _tab_label(primary_id, fallback_label):
        """Return label from domain config tabs if present, else fallback."""
        for t in domain_cfg.get("tabs", []):
            if t["id"] == primary_id:
                return t["icon"] + " " + t["label"]
        return fallback_label

    def _show_if(*tab_ids):
        """Show tab if ANY of the given tab_ids appear in the domain's tab list."""
        return _TAB_SHOW if any(tid in _domain_tabs for tid in tab_ids) else _TAB_HIDE

    # Sales slot: revenue / sales / income
    sales_label = _tab_label("revenue",   _tab_label("sales",    "Sales"))
    # Purchases slot: costs / purchases / expenses
    purch_label = _tab_label("costs",     _tab_label("purchases", "Purchases"))
    # Compare slot: customers / compare
    comp_label  = _tab_label("customers", _tab_label("compare",   "Branch Compare"))
    # GST slot: gst / tax — label adapts to tenant currency (US = Sales Tax, India = GST)
    _tenant_currency = "INR"
    try:
        if is_tenant and tenant_id:
            with auth_engine.connect() as _cc:
                _cr = _cc.execute(
                    text("SELECT currency FROM local_tenants WHERE id=:tid"),
                    {"tid": int(tenant_id)}
                ).fetchone()
                if _cr and _cr[0]:
                    _tenant_currency = _cr[0]
    except Exception:
        pass
    _is_us_tenant = _tenant_currency in ("USD", "CAD", "AUD", "GBP", "EUR")
    gst_label = _tab_label("gst", "🧾 Sales Tax" if _is_us_tenant else "🧾 GST / Tax")
    # Stock slot: inventory / stock
    stock_label = _tab_label("inventory", _tab_label("stock",     "Stock"))
    # Cashcredit slot: cashflow / cashcredit
    cash_label  = _tab_label("cashflow",  "Cash/Credit")

    sales_style  = _show_if("revenue",   "sales")
    purch_style  = _show_if("costs",     "purchases")
    comp_style   = _show_if("customers", "compare")
    gst_style    = _show_if("gst")
    expiry_style = _show_if("expiry")
    stock_style  = _show_if("inventory", "stock")
    cash_style   = _show_if("cashflow",  "cashcredit")

    # ── RBAC admin tabs ──────────────────────────────────────────────────────
    internal_admin = is_admin and not is_tenant and not is_ca
    # Upload: both internal admins AND tenant admins can upload their own data
    upload_style   = _TAB_SHOW if is_admin else _TAB_HIDE
    rollback_style = _TAB_SHOW if internal_admin else _TAB_HIDE
    # Users: both internal admins AND tenant admins (scoped to their tenant)
    users_style    = _TAB_SHOW if is_admin else _TAB_HIDE
    tenants_style  = _TAB_SHOW if internal_admin else _TAB_HIDE
    billing_style  = _TAB_SHOW if internal_admin else _TAB_HIDE
    # Alerts tab: visible to tenant admins AND internal admins
    alerts_style   = _TAB_SHOW if (is_admin or is_tenant) else _TAB_HIDE
    # AI Settings tab: tenant admins (BYO LLM) and internal admins
    ai_settings_style = _TAB_SHOW if (is_admin or is_tenant) else _TAB_HIDE

    # Admin customer selector: list every tenant so an internal admin can view
    # any customer's analytics. Hidden for tenant/viewer roles.
    admin_tenant_opts = [{"label": "MedStar (internal)", "value": 0}]
    admin_tenant_wrap = _TAB_HIDE
    if internal_admin:
        try:
            # customers live in Neon (same source the Tenants tab writes to)
            for t in _list_tenants_neon():
                if t.get("id") is not None:
                    admin_tenant_opts.append({
                        "label": f"{t['Name']} ({t.get('Domain','generic')})",
                        "value": int(t["id"])})
        except Exception:
            pass
        admin_tenant_wrap = {"display": "block"}

    return (
        user_info, brand_icon, brand_name, brand_subtitle,
        # labels
        sales_label, purch_label, comp_label, gst_label, stock_label, cash_label,
        # domain visibility
        sales_style, purch_style, comp_style, gst_style,
        expiry_style, stock_style, cash_style,
        # admin RBAC
        upload_style, rollback_style, users_style, tenants_style, billing_style,
        alerts_style, ai_settings_style,
        # admin customer selector
        admin_tenant_opts, admin_tenant_wrap,
    )

# ── Quick-select ──────────────────────────────────────────────
@app.callback(
    Output("filter-date", "start_date"),
    Output("filter-date", "end_date"),
    Input("qs-this","n_clicks"),
    Input("qs-last","n_clicks"),
    Input("qs-3m",  "n_clicks"),
    Input("qs-all", "n_clicks"),
    prevent_initial_call=True,
)
def apply_quick_select(n_this, n_last, n_3m, n_all):
    today     = date.today()
    triggered = ctx.triggered_id
    if triggered == "qs-this":
        s = date(today.year, today.month, 1); e = today
    elif triggered == "qs-last":
        first = date(today.year, today.month, 1)
        e = first - timedelta(days=1); s = date(e.year, e.month, 1)
    elif triggered == "qs-3m":
        s = (today.replace(day=1) - timedelta(days=60)).replace(day=1); e = today
    else:
        s, e = _data_date_bounds()
    return str(s), str(e)

# ── Tenant welcome page (shown instead of analytics to tenant users) ──────────
def _render_tenant_welcome(tenant_name):
    tname = tenant_name or "Your Organisation"
    return html.Div([
        html.Div([
            html.Div("🏢", style={"fontSize":"4rem","marginBottom":"0.5rem"}),
            html.H2(f"Welcome, {tname}",
                    style={"color":C_GREEN,"fontWeight":700,"marginBottom":"0.5rem"}),
            html.P(
                "Your tenant portal is active. "
                "Use the Tenants tab to view your account details and configured modules.",
                style={"color":"#64748b","fontSize":"0.95rem","maxWidth":"480px",
                       "margin":"0 auto","lineHeight":1.6}
            ),
            html.Hr(style={"margin":"2rem auto","width":"80px",
                           "border":"none","borderTop":"2px solid #e2e8f0"}),
            html.Div([
                html.Div([
                    html.Span("📤", style={"fontSize":"1.4rem"}),
                    html.P("Upload your data using the Upload Data tab to get started.",
                           style={"fontSize":"0.8rem","color":"#94a3b8","margin":"4px 0 0"}),
                ], style={"textAlign":"center","padding":"1rem",
                          "background":"#f8fafc","borderRadius":"12px",
                          "border":"1px solid #e2e8f0","maxWidth":"320px","margin":"0 auto"}),
            ]),
        ], style={"textAlign":"center","padding":"4rem 2rem"}),
    ], style={"minHeight":"60vh","display":"flex","alignItems":"center","justifyContent":"center"})


# ── Tab router ────────────────────────────────────────────────
@app.callback(
    Output("tab-content",          "children"),
    Output("filter-branch",        "options"),
    Output("sidebar-sources",      "children"),
    Output("sidebar-data-status",  "children"),
    Output("navbar-period-label",  "children"),
    Output("sidebar-domain-badge", "children"),
    Output("sidebar-pharmacy-exports", "style"),
    Input("main-tabs",    "value"),
    Input("filter-branch","value"),
    Input("filter-date",  "start_date"),
    Input("filter-date",  "end_date"),
    Input("data-version", "data"),
    Input("admin-tenant-select", "value"),
)
def render_tab(tab, branch, start_date, end_date, _version, admin_tenant=0):
    # Role guard — redirect viewers away from admin-only tabs
    try:
        is_admin    = current_user.is_authenticated and current_user.is_admin()
        is_tenant   = current_user.is_authenticated and current_user.is_tenant_user()
        tenant_name = current_user.tenant_name if is_tenant else None
    except Exception:
        is_admin    = False
        is_tenant   = False
        tenant_name = None

    # ── Effective tenant to VIEW ──────────────────────────────────
    # Tenant users always view their own data. An internal admin can pick any
    # customer from the sidebar selector to view that tenant's analytics
    # (MedStar / value 0 = the internal dataset, rendered by the normal path).
    _internal_admin = is_admin and not is_tenant
    try:
        _admin_tid = int(admin_tenant) if admin_tenant not in (None, "", 0, "0", "medstar") else None
    except (TypeError, ValueError):
        _admin_tid = None
    if is_tenant:
        _view_tid  = int(current_user.tenant_id) if current_user.tenant_id else None
        _view_name = tenant_name or "Your Business"
    elif _internal_admin and _admin_tid:
        _view_tid  = _admin_tid
        _view_name = _tenant_name_by_id(_admin_tid) or f"Tenant {_admin_tid}"
    else:
        _view_tid  = None
        _view_name = tenant_name or "Your Business"

    if tab in ("upload","users") and not is_admin:
        tab = "overview"

    # ── Resolve tenant domain before the fence ──────────────────────
    try:
        _tid_for_fence = int(current_user.tenant_id) if (
            current_user.is_authenticated and is_tenant and current_user.tenant_id
        ) else 0
    except Exception:
        _tid_for_fence = 0
    _active_domain_fence = _get_active_domain(_tid_for_fence) if _tid_for_fence else "pharmacy"
    _is_pharmacy_tenant  = (_active_domain_fence == "pharmacy")

    # ── Tenant data fence (SECURITY — do not relax without per-tenant data loading) ──
    # The global sales_df / purchase_df belong exclusively to the MedStar internal
    # account.  They must NEVER be visible to any external tenant user, regardless
    # of domain type.  Tenants are fenced here until per-tenant data upload +
    # isolated storage is wired up (Phase 2 roadmap item).
    #
    # Allowed tabs for tenants: upload (their own data), tenants (portal), login.
    # All analytics tabs (overview, sales, purchases, compare, cashflow, inventory,
    # yoy, gst, expiry, stock, threshold, billing) are blocked until the tenant has
    # uploaded their own dataset.
    _TENANT_ANALYTICS_TABS = (
        "overview", "sales", "purchases", "compare",
        "cashflow", "inventory", "yoy", "gst",
        "expiry", "stock", "threshold", "billing",
    )
    if _view_tid and tab in _TENANT_ANALYTICS_TABS:
        # Admin viewing a cloud (Neon) customer, vs a tenant user on local data.
        _view_from_neon = _internal_admin and (_admin_tid is not None)
        # Check whether this customer has data yet
        _tenant_has_data = False
        if _view_from_neon:
            try:
                _chk_s, _chk_p = _load_tenant_from_neon(_view_tid)
                _tenant_has_data = (not _chk_s.empty) or (not _chk_p.empty)
            except Exception:
                _tenant_has_data = False
        else:
            try:
                _t_id_check = _view_tid
                if _t_id_check:
                    from data_loader import get_upload_history
                    _hist = get_upload_history(auth_engine, tenant_id=_t_id_check)
                    _tenant_has_data = not _hist.empty
            except Exception:
                _tenant_has_data = False

        if not _tenant_has_data:
            # No tenant-specific data — show upload prompt
            welcome = _render_tenant_welcome(_view_name)
            return welcome, [], [], html.Div(), "", html.Div(), {"display":"none"}

        # Tenant HAS uploaded data — render real per-tenant analytics (Phase 2)
        _t_err = None
        try:
            _t_id_data = _view_tid
            if _t_id_data:
                # ── Analytics source: warehouse serving mart (feature-flagged) ──
                # Set WAREHOUSE_ANALYTICS=1 to read from the star-schema marts.
                # Falls back to the flat tables automatically if the mart is empty,
                # so enabling the flag can never blank out a tenant's dashboard.
                _t_sales = _t_purchases = None
                if _view_from_neon:
                    _t_sales, _t_purchases = _load_tenant_from_neon(_t_id_data)
                    print(f"[tenant] id={_t_id_data} source=NEON sales={len(_t_sales)} purchases={len(_t_purchases)}")
                _use_wh = os.getenv("WAREHOUSE_ANALYTICS", "0") == "1"
                if _t_sales is None and _use_wh:
                    try:
                        import warehouse as _wh
                        _ms, _mp = _wh.load_tenant_df_from_mart(engine, _t_id_data)
                        if not _ms.empty or not _mp.empty:
                            # mart is populated (kept fresh by upload hook + startup backfill)
                            _t_sales, _t_purchases = _ms, _mp
                            print(f"[tenant] id={_t_id_data} source=WAREHOUSE sales={len(_ms)} purchases={len(_mp)}")
                        else:
                            # first time for this tenant — backfill in background, use flat now
                            _dom = _get_active_domain(_t_id_data) or "generic"
                            threading.Thread(
                                target=lambda: _wh.load_tenant_to_warehouse(engine, _t_id_data, _dom),
                                daemon=True).start()
                    except Exception as _we:
                        logger.warning("[tenant] warehouse read failed, using flat: %s", _we)
                if _t_sales is None:
                    _t_sales, _t_purchases = load_tenant_df(engine, _t_id_data)
                    print(f"[tenant] id={_t_id_data} source=FLAT sales={len(_t_sales)} purchases={len(_t_purchases)}")
                # Apply date range filter from sidebar
                for _df in (_t_sales, _t_purchases):
                    if not _df.empty and "bill_date" in _df.columns:
                        _df["bill_date"] = pd.to_datetime(_df["bill_date"], errors="coerce")
                if start_date and not _t_sales.empty and "bill_date" in _t_sales.columns:
                    _t_sales = _t_sales[_t_sales["bill_date"] >= pd.to_datetime(start_date)]
                if end_date and not _t_sales.empty and "bill_date" in _t_sales.columns:
                    _t_sales = _t_sales[_t_sales["bill_date"] <= pd.to_datetime(end_date)]
                if start_date and not _t_purchases.empty and "bill_date" in _t_purchases.columns:
                    _t_purchases = _t_purchases[_t_purchases["bill_date"] >= pd.to_datetime(start_date)]
                if end_date and not _t_purchases.empty and "bill_date" in _t_purchases.columns:
                    _t_purchases = _t_purchases[_t_purchases["bill_date"] <= pd.to_datetime(end_date)]
                # Apply client/branch filter
                if branch and branch != "All" and not _t_sales.empty and "supplier_name" in _t_sales.columns:
                    _t_sales = _t_sales[_t_sales["supplier_name"] == branch]
            else:
                _t_sales, _t_purchases = pd.DataFrame(), pd.DataFrame()
                _t_err = "Tenant ID not found on session."
        except Exception as _te:
            _t_sales, _t_purchases = pd.DataFrame(), pd.DataFrame()
            _t_err = str(_te)
            print(f"[tenant] load error: {_te}")
        _t_name = _view_name or "Your Business"
        # Look up tenant currency (default USD for SaaS tenants, INR for pharmacy)
        _t_cur = "$"
        try:
            _t_id_cur = _view_tid
            if _t_id_cur:
                with engine.connect() as _cc:
                    _cur_row = _cc.execute(
                        text("SELECT currency FROM local_tenants WHERE id=:tid"),
                        {"tid": _t_id_cur}
                    ).fetchone()
                    if _cur_row and _cur_row[0]:
                        _sym_map = {"USD": "$", "INR": "₹", "EUR": "€", "GBP": "£",
                                    "CAD": "CA$", "AUD": "A$", "SGD": "S$"}
                        _t_cur = _sym_map.get(_cur_row[0], _cur_row[0])
        except Exception:
            pass
        analytics_content = render_tenant_tab(tab, _t_sales, _t_purchases, _t_name,
                                              error=_t_err, cur=_t_cur)
        # Populate sidebar Client dropdown with tenant's unique customers
        _t_clients = []
        try:
            if not _t_sales.empty and "supplier_name" in _t_sales.columns:
                # Reload all (unfiltered) to get full client list
                _all_s, _ = load_tenant_df(engine, _t_id_data)
                _clients = sorted(_all_s["supplier_name"].dropna().unique().tolist())
                _t_clients = [{"label": "All Clients", "value": "All"}] +                              [{"label": c, "value": c} for c in _clients]
            elif not _t_purchases.empty and "supplier_name" in _t_purchases.columns:
                _clients = sorted(_t_purchases["supplier_name"].dropna().unique().tolist())
                _t_clients = [{"label": "All", "value": "All"}] +                              [{"label": c, "value": c} for c in _clients]
        except Exception:
            _t_clients = []
        return analytics_content, _t_clients, [], html.Div(), "", html.Div(), {"display":"none"}

    branches = get_filter_options()
    BCM      = get_branch_color_map()

    # SECURITY: tenant users must never see MedStar's branch names or row counts
    # in the sidebar — even when on admin-permitted tabs (Users, Upload).
    if is_tenant:
        b_opts  = []
        sources = []
        # Show actual upload count if tenant has data, else prompt to upload
        try:
            _t_sid = int(current_user.tenant_id) if current_user.tenant_id else None
            _t_hist = get_upload_history(auth_engine, tenant_id=_t_sid) if _t_sid else None
            _t_rows = int(_t_hist["row_count"].sum()) if (_t_hist is not None and not _t_hist.empty) else 0
            _t_files = len(_t_hist) if (_t_hist is not None and not _t_hist.empty) else 0
        except Exception:
            _t_rows = 0
            _t_files = 0
        if _t_rows > 0:
            status = html.Div([
                html.Div([
                    html.Span(f"{_t_rows:,} rows", style={"fontWeight":700,"color":C_GREEN}),
                    html.Span(" loaded", style={"color":"#64748b","fontSize":"0.78rem"}),
                ], className="stat-row"),
                html.Div([
                    html.Span(f"{_t_files} file{'s' if _t_files != 1 else ''} uploaded",
                              style={"color":"#94a3b8","fontSize":"0.75rem"}),
                ], className="stat-row"),
            ], className="data-status")
        else:
            status = html.Div([
                html.Div([html.Span("Upload your data to see analytics.")],
                         className="stat-row"),
            ], className="data-status")
    else:
        b_opts = [{"label":b,"value":b} for b in branches]
        all_b  = sorted(set(
            (sales_df["branch"].unique().tolist()    if not sales_df.empty    else []) +
            (purchase_df["branch"].unique().tolist() if not purchase_df.empty else [])
        ))
        sources = [
            html.Div([
                html.Span(className="source-dot", style={"background":BCM.get(b,C_TEAL)}),
                html.Span(b, style={"fontSize":"0.72rem","color":"#555"}),
            ], className="source-item")
            for b in all_b
        ]
        s_rows = len(sales_df)    if not sales_df.empty    else 0
        p_rows = len(purchase_df) if not purchase_df.empty else 0
        dm, dx = _data_date_bounds()
        status = html.Div([
            html.Div([html.Span("Sales rows"),    html.Span("{:,}".format(s_rows), className="stat-val")], className="stat-row"),
            html.Div([html.Span("Purchase rows"), html.Span("{:,}".format(p_rows), className="stat-val")], className="stat-row"),
            html.Div([html.Span("Span"),
                      html.Span("{} - {}".format(dm.strftime("%b %y"), dx.strftime("%b %y")),
                                className="stat-val")], className="stat-row"),
        ], className="data-status")

    try:
        s_str = pd.to_datetime(start_date).strftime("%d %b %Y") if start_date else "All"
        e_str = pd.to_datetime(end_date).strftime("%d %b %Y")   if end_date   else "All"
        plabel = "Period: {}  to  {}".format(s_str, e_str)
    except Exception:
        plabel = ""

    # ── Determine tenant_id for per-tenant views ─────────────────
    try:
        _tid = (current_user.tenant_id
                if current_user.is_authenticated and current_user.is_tenant_user()
                else None)
    except Exception:
        _tid = None

    # ── Detect active domain ──────────────────────────────────────
    try:
        _tenant_id_int = int(_tid) if _tid else 0
    except Exception:
        _tenant_id_int = 0
    active_domain = _get_active_domain(_tenant_id_int)
    is_pharmacy   = (active_domain == "pharmacy")

    # ── Domain-adaptive KPI data for non-pharmacy tabs ────────────
    _kpi_data_for_domain = {
        "sales":      sales_df["net_amount"].sum()   if not sales_df.empty and "net_amount" in sales_df.columns else 0,
        "purchases":  purchase_df["net_amount"].sum() if not purchase_df.empty and "net_amount" in purchase_df.columns else 0,
        "margin":     sales_df["margin_pct"].mean()  if not sales_df.empty and "margin_pct" in sales_df.columns else 0,
        "bills":      int(sales_df["total_bills"].sum()) if not sales_df.empty and "total_bills" in sales_df.columns else len(sales_df),
        "top_branch": sales_df.groupby("branch")["net_amount"].sum().idxmax() if (not sales_df.empty and "branch" in sales_df.columns and "net_amount" in sales_df.columns) else "—",
    }

    # ── Tab routing ───────────────────────────────────────────────
    if tab == "overview":
        if is_pharmacy:
            content = render_overview(branch, start_date, end_date)
        else:
            content = render_domain_tab("overview", sales_df, purchase_df, _kpi_data_for_domain, active_domain)
    elif tab == "sales":
        if is_pharmacy:
            content = render_sales(branch, start_date, end_date)
        else:
            content = render_domain_tab("revenue", sales_df, purchase_df, _kpi_data_for_domain, active_domain)
    elif tab == "purchases":
        if is_pharmacy:
            content = render_purchases(branch, start_date, end_date)
        else:
            content = render_domain_tab("costs", sales_df, purchase_df, _kpi_data_for_domain, active_domain)
    elif tab == "compare":
        if is_pharmacy:
            content = render_compare(start_date, end_date)
        else:
            content = render_domain_tab("customers", sales_df, purchase_df, _kpi_data_for_domain, active_domain)
    # Domain-only tabs (non-pharmacy)
    elif tab == "cashflow":
        content = render_domain_tab("cashflow", sales_df, purchase_df, _kpi_data_for_domain, active_domain)
    elif tab == "inventory":
        content = render_domain_tab("inventory", sales_df, purchase_df, _kpi_data_for_domain, active_domain)

    # ── Phase 1 new tabs ──────────────────────────────────────────
    elif tab == "yoy":
        try:
            content = render_yoy_tab(sales_df, purchase_df, branch)
        except Exception as _e:
            content = html.Div(f"YoY tab error: {_e}", style={"color":"red","padding":"1rem"})

    elif tab == "gst":
        try:
            # Detect tenant country: USD/CAD tenants → US Sales Tax view
            _gst_country = "IN"
            try:
                _gst_tid = (int(current_user.tenant_id)
                            if current_user.is_authenticated and current_user.is_tenant_user()
                            and current_user.tenant_id else None)
                if _gst_tid:
                    with engine.connect() as _gcc:
                        _gcr = _gcc.execute(
                            text("SELECT currency FROM local_tenants WHERE id=:tid"),
                            {"tid": _gst_tid}
                        ).fetchone()
                        if _gcr and _gcr[0] in ("USD","CAD","AUD","GBP","EUR"):
                            _gst_country = "US"
            except Exception:
                pass
            content = render_gst_tab(purchase_df, branch, start_date, end_date,
                                     country=_gst_country, sales_df=sales_df)
        except Exception as _e:
            content = html.Div(f"Tax tab error: {_e}", style={"color":"red","padding":"1rem"})

    elif tab == "expiry":
        try:
            content = render_expiry_tab(auth_engine, _tid)
        except Exception as _e:
            content = html.Div(f"Expiry tab error: {_e}", style={"color":"red","padding":"1rem"})

    elif tab == "stock":
        try:
            content = render_stock_tab(auth_engine, _tid)
        except Exception as _e:
            content = html.Div(f"Stock tab error: {_e}", style={"color":"red","padding":"1rem"})

    elif tab == "cashcredit":
        try:
            content = render_cash_credit_tab(sales_df, branch, start_date, end_date)
        except Exception as _e:
            content = html.Div(f"Cash/Credit tab error: {_e}", style={"color":"red","padding":"1rem"})

    # ── Phase 2 new tabs ──────────────────────────────────────────
    elif tab == "aichat":
        try:
            tname = (current_user.tenant_name if
                     current_user.is_authenticated and current_user.is_tenant_user()
                     else "InsightHub")
            content = render_ai_chat_tab(tenant_name=tname)
        except Exception as _e:
            content = html.Div(f"AI Chat tab error: {_e}", style={"color":"red","padding":"1rem"})

    elif tab == "referral":
        try:
            _ref_tid  = int(_tid) if _tid else 1
            _ref_tname = (current_user.tenant_name if
                          current_user.is_authenticated and current_user.is_tenant_user()
                          else "InsightHub")
            _currency  = "INR"  # TODO: derive from tenant profile
            content = render_referral_tab(_ref_tid, _ref_tname, auth_engine, _currency)
        except Exception as _e:
            content = html.Div(f"Referral tab error: {_e}", style={"color":"red","padding":"1rem"})

    # ── Admin tabs ────────────────────────────────────────────────
    elif tab == "upload":
        content = render_upload_tab()
    elif tab == "rollback":
        try:
            content = render_upload_rollback_tab(auth_engine, _tid)
        except Exception as _e:
            content = html.Div(f"Rollback tab error: {_e}", style={"color":"red","padding":"1rem"})
    elif tab == "users":
        content = render_users_tab()
    elif tab == "tenants":
        content = render_tenants_tab(local_tenants=_list_tenants_local())
    elif tab == "billing":
        content = _render_billing_tab()
    elif tab == "alerts":
        try:
            _alerts_tid = int(current_user.tenant_id) if (
                current_user.is_authenticated and current_user.is_tenant_user()
                and current_user.tenant_id
            ) else (1 if is_admin else None)
            if _alerts_tid:
                content = render_alert_settings_tab(_alerts_tid, auth_engine)
            else:
                content = html.Div("No tenant context.", style={"color": "#ef4444", "padding": "1rem"})
        except Exception as _ae:
            content = html.Div(f"Alert settings error: {_ae}", style={"color": "red", "padding": "1rem"})
    elif tab == "ai_settings":
        try:
            from llm_settings import render_llm_settings_tab
            _llm_tid = int(current_user.tenant_id) if (
                current_user.is_authenticated and current_user.is_tenant_user()
                and current_user.tenant_id
            ) else (1 if is_admin else None)
            if _llm_tid:
                content = render_llm_settings_tab(_llm_tid, engine)
            else:
                content = html.Div("No tenant context.", style={"color": "#ef4444", "padding": "1rem"})
        except Exception as _le:
            content = html.Div(f"AI settings error: {_le}", style={"color": "red", "padding": "1rem"})
    else:
        content = html.Div()

    # ── Domain badge for sidebar ──────────────────────────────────
    try:
        _dom_cfg   = get_domain_config(active_domain)
        _dom_color = {"pharmacy": "#1e7e4b", "saas": "#0d6efd", "retail": "#fd7e14",
                      "accounting": "#6f42c1", "generic": "#6b7280"}.get(active_domain, "#6b7280")
        domain_badge = html.Div([
            html.Div("Business Type", className="sidebar-label"),
            html.Span(
                f"{_dom_cfg['icon']} {_dom_cfg['label']}",
                style={
                    "background": _dom_color, "color": "white",
                    "padding": "3px 10px", "borderRadius": "10px",
                    "fontSize": "0.72rem", "fontWeight": "600",
                    "display": "inline-block", "marginTop": "4px",
                },
            ),
        ]) if active_domain != "pharmacy" else html.Div()
    except Exception:
        domain_badge = html.Div()

    # Hide pharmacy Sales/Purchase export buttons for tenant users (MedStar data only)
    _exp_style = {"display":"none"} if is_tenant else {"display":"grid","gridTemplateColumns":"1fr 1fr","gap":"4px","marginBottom":"4px"}
    return content, b_opts, sources, status, plabel, domain_badge, _exp_style

# ══════════════════════════════════════════════════════════════
# TAB 1 — OVERVIEW
# ══════════════════════════════════════════════════════════════
def render_overview(branch, start_date, end_date):
    s = sales_df.copy(); p = purchase_df.copy()
    if branch != "All":
        s = s[s["branch"]==branch]; p = p[p["branch"]==branch]
    s = apply_date_filter(s, start_date, end_date, "bill_date")
    p = apply_date_filter(p, start_date, end_date, "grn_date")

    base_s = sales_df    if branch=="All" else sales_df[sales_df["branch"]==branch]
    base_p = purchase_df if branch=="All" else purchase_df[purchase_df["branch"]==branch]
    s_prev = get_prev_period(base_s, start_date, end_date, "bill_date")
    p_prev = get_prev_period(base_p, start_date, end_date, "grn_date")
    BCM    = get_branch_color_map()

    sv   = s["net_amount"].sum()   if not s.empty else 0
    pv   = p["net_amount"].sum()   if not p.empty else 0
    adv  = s["net_amount"].mean()  if not s.empty else 0
    am   = s["margin_pct"].mean()  if (not s.empty and "margin_pct" in s.columns) else None
    tb   = int(s["total_bills"].sum()) if (not s.empty and "total_bills" in s.columns) else 0
    gst  = p["total_gst"].sum()   if (not p.empty and "total_gst"  in p.columns) else 0

    sp_sv  = s_prev["net_amount"].sum()   if not s_prev.empty else None
    sp_pv  = p_prev["net_amount"].sum()   if not p_prev.empty else None
    sp_adv = s_prev["net_amount"].mean()  if not s_prev.empty else None
    sp_am  = s_prev["margin_pct"].mean()  if (not s_prev.empty and "margin_pct" in s_prev.columns) else None
    sp_tb  = int(s_prev["total_bills"].sum()) if (not s_prev.empty and "total_bills" in s_prev.columns) else None
    sp_gst = p_prev["total_gst"].sum()   if (not p_prev.empty and "total_gst"  in p_prev.columns) else None

    kpis = dbc.Row([
        dbc.Col(kpi_card("Total Sales",    fmt_inr(sv) if not s.empty else "--", "{} days".format(len(s)),    "",        "\U0001f4b0", pct_delta(sv,  sp_sv)),  md=2),
        dbc.Col(kpi_card("Total Purchase", fmt_inr(pv) if not p.empty else "--", "{} invoices".format(len(p)),"purchase","\U0001f6d2", pct_delta(pv,  sp_pv)),  md=2),
        dbc.Col(kpi_card("Avg Daily Sales",fmt_inr(adv)if not s.empty else "--", "",                          "",        "\U0001f4c5", pct_delta(adv, sp_adv)), md=2),
        dbc.Col(kpi_card("Avg Margin %",   "{:.1f}%".format(am) if am is not None else "N/A", "", "margin", "\U0001f4ca", pct_delta(am,sp_am)), md=2),
        dbc.Col(kpi_card("Total Bills",    "{:,}".format(tb),    "",              "bills",   "\U0001f9fe", pct_delta(tb,  sp_tb)),  md=2),
        dbc.Col(kpi_card("GST Paid",       fmt_inr(gst),         "On purchases", "returns", "\U0001f3db️", pct_delta(gst, sp_gst)), md=2),
    ], className="g-3 mb-3")

    try:
        badge_text = "{} - {}".format(
            pd.to_datetime(start_date).strftime("%d %b %Y") if start_date else "All",
            pd.to_datetime(end_date).strftime("%d %b %Y")   if end_date   else "All")
    except Exception:
        badge_text = "All data"

    fig_trend = go.Figure()
    if not s.empty:
        for b, grp in s.groupby("branch"):
            grp = grp.sort_values("bill_date")
            rgb = _hex_to_rgb(BCM.get(b, C_TEAL))
            fig_trend.add_trace(go.Scatter(
                x=grp["bill_date"], y=grp["net_amount"], name=b,
                mode="lines+markers", line=dict(color=BCM.get(b,C_TEAL), width=2.5),
                fill="tozeroy", fillcolor="rgba({},{},{},0.07)".format(*rgb)))
    fig_trend.update_layout(**CHART_LAYOUT, title="Daily Net Sales Trend")

    fig_donut = go.Figure()
    if not s.empty and "pharma_sales" in s.columns:
        ph = s["pharma_sales"].sum(); np_ = s["non_pharma_sales"].sum(); tot = ph+np_ or 1
        fig_donut = go.Figure(go.Pie(labels=["Pharma","Non-Pharma"], values=[ph,np_],
            hole=0.55, marker_colors=[C_BLUE, C_AMBER]))
        fig_donut.update_layout(**CHART_LAYOUT, title="Sales Mix",
            annotations=[dict(text="{:.0f}%<br>Pharma".format(ph/tot*100),
                              x=0.5, y=0.5, font_size=13, showarrow=False)])
    else:
        fig_donut.update_layout(**CHART_LAYOUT, title="Sales Mix")

    if not p.empty and "grn_date" in p.columns:
        _p = p.copy()
        _p["_month"] = _p["grn_date"].dt.to_period("M").astype(str)
        p_m = _p.groupby(["_month","branch"])["net_amount"].sum().reset_index()
        fig_p = px.bar(p_m, x="_month", y="net_amount", color="branch",
            color_discrete_map=BCM, barmode="group", text_auto=".2s",
            labels={"_month":"Month","net_amount":"Purchase (Rs.)","branch":"Branch"},
            title="Monthly Purchase by Branch")
    else:
        fig_p = go.Figure()
    fig_p.update_layout(**CHART_LAYOUT)

    # ── Anomaly detection (Z-score, US-304) ─────────────────────────────────
    try:
        _anomalies = _detect_anomalies(s, p)
        _anomaly_banners = render_anomaly_banners(_anomalies)
    except Exception:
        _anomaly_banners = html.Div()

    return html.Div([
        html.Div([html.Span(className="accent"),html.Span("Overview"),
                  html.Span(badge_text,className="period-badge")], className="section-heading"),
        render_alert_banners(check_alerts(s, p)),
        _anomaly_banners,
        kpis,
        dbc.Row([dbc.Col(chart_card("Daily Sales Trend",fig_trend),md=8),
                 dbc.Col(chart_card("Sales Mix",fig_donut),md=4)],className="g-3"),
        dbc.Row([dbc.Col(chart_card("Monthly Purchase by Branch",fig_p),md=12)],className="g-3"),
    ])

# ══════════════════════════════════════════════════════════════
# TAB 2 — SALES
# ══════════════════════════════════════════════════════════════
def render_sales(branch, start_date, end_date):
    s = sales_df.copy()
    if branch != "All": s = s[s["branch"]==branch]
    s = apply_date_filter(s, start_date, end_date, "bill_date")
    if s.empty:
        return html.Div([html.Div([html.Span(className="accent"),html.Span("Sales Analysis")],className="section-heading"),
                         empty_state("\U0001f4c8","No sales data","Adjust the date range or branch filter.")])
    BCM = get_branch_color_map()

    fig_d = px.area(s.sort_values("bill_date"),x="bill_date",y="net_amount",color="branch",
        color_discrete_map=BCM,labels={"bill_date":"Date","net_amount":"Net Sales (Rs.)"},title="Daily Net Sales")
    fig_d.update_layout(**CHART_LAYOUT)

    fig_b = px.bar(s.sort_values("bill_date"),x="bill_date",y="total_bills",color="branch",
        color_discrete_map=BCM,labels={"bill_date":"Date","total_bills":"Bills"},title="Daily Bill Count")
    fig_b.update_layout(**CHART_LAYOUT)

    fig_m = px.line(s.sort_values("bill_date"),x="bill_date",y="margin_pct",color="branch",
        color_discrete_map=BCM,markers=True,labels={"bill_date":"Date","margin_pct":"Margin %"},title="Margin % Trend")
    fig_m.add_hline(y=s["margin_pct"].mean(), line_dash="dash", line_color="#aaa",
                    annotation_text="Avg {:.1f}%".format(s["margin_pct"].mean()))
    fig_m.add_hline(y=THRESHOLDS["margin_pct_min"], line_dash="dot", line_color="#ef4444",
                    annotation_text="Min {:.0f}%".format(THRESHOLDS["margin_pct_min"]),
                    annotation_font_color="#ef4444")
    fig_m.update_layout(**CHART_LAYOUT)

    cash=s.get("cash_sales",pd.Series([0])).sum()
    credit=s.get("credit_sales",pd.Series([0])).sum()
    card=s.get("card_sales",pd.Series([0])).sum()
    fig_pay = go.Figure(go.Pie(labels=["Cash","Credit","Card"],values=[cash,credit,card],
        hole=0.5,marker_colors=[C_BLUE,C_AMBER,C_SKY],textinfo="label+percent"))
    fig_pay.update_layout(**CHART_LAYOUT, title="Payment Mode")

    s = s.copy(); s["_month"] = s["bill_date"].dt.to_period("M").astype(str)
    pm = s.groupby(["_month","branch"])[["pharma_sales","non_pharma_sales"]].sum().reset_index()
    pm = pm.melt(id_vars=["_month","branch"],value_vars=["pharma_sales","non_pharma_sales"],
                 var_name="cat",value_name="amount")
    pm["cat"] = pm["cat"].map({"pharma_sales":"Pharma","non_pharma_sales":"Non-Pharma"})
    fig_ph = px.bar(pm,x="_month",y="amount",color="cat",barmode="group",
        color_discrete_map={"Pharma":C_BLUE,"Non-Pharma":C_AMBER},text_auto=".2s",
        labels={"_month":"Month","amount":"Sales (Rs.)","cat":"Category"},title="Pharma vs Non-Pharma")
    fig_ph.update_layout(**CHART_LAYOUT)

    ret = s[s.get("cash_return",pd.Series([0])>0)].sort_values("bill_date") if "cash_return" in s.columns else pd.DataFrame()
    fig_ret = (px.bar(ret,x="bill_date",y="cash_return",color="branch",color_discrete_map=BCM,
        labels={"bill_date":"Date","cash_return":"Return (Rs.)"},title="Daily Returns")
        if not ret.empty else go.Figure())
    fig_ret.update_layout(**CHART_LAYOUT)

    return html.Div([
        html.Div([html.Span(className="accent"),html.Span("Sales Analysis")],className="section-heading"),
        dbc.Row([dbc.Col(chart_card("Daily Net Sales",fig_d),md=8),
                 dbc.Col(chart_card("Payment Mode",fig_pay),md=4)],className="g-3"),
        dbc.Row([dbc.Col(chart_card("Daily Bill Count",fig_b),md=4),
                 dbc.Col(chart_card("Margin % Trend",fig_m),md=4),
                 dbc.Col(chart_card("Pharma vs Non-Pharma",fig_ph),md=4)],className="g-3"),
        dbc.Row([dbc.Col(chart_card("Daily Returns",fig_ret),md=6)],className="g-3"),
    ])

# ══════════════════════════════════════════════════════════════
# TAB 3 — PURCHASES
# ══════════════════════════════════════════════════════════════
def render_purchases(branch, start_date, end_date):
    p = purchase_df.copy()
    if branch != "All": p = p[p["branch"]==branch]
    p = apply_date_filter(p, start_date, end_date, "grn_date")
    if p.empty:
        return html.Div([html.Div([html.Span(className="accent"),html.Span("Purchase Analysis")],className="section-heading"),
                         empty_state("\U0001f6d2","No purchase data","Adjust the date range or branch filter.")])
    BCM = get_branch_color_map()

    ts = p.groupby("supplier_name")["net_amount"].sum().sort_values(ascending=True).tail(15).reset_index()
    fig_s = px.bar(ts,x="net_amount",y="supplier_name",orientation="h",
        color="net_amount",color_continuous_scale=[[0,"#BFDBFE"],[1,C_BLUE]],
        text_auto=".2s",title="Top 15 Suppliers",
        labels={"net_amount":"Purchase (Rs.)","supplier_name":"Supplier"})
    fig_s.update_coloraxes(showscale=False); fig_s.update_layout(**CHART_LAYOUT,height=420)

    pd_ = p.dropna(subset=["grn_date"]).groupby(["grn_date","branch"])["net_amount"].sum().reset_index()
    fig_d = (px.line(pd_.sort_values("grn_date"),x="grn_date",y="net_amount",color="branch",
        color_discrete_map=BCM,markers=True,
        labels={"grn_date":"Date","net_amount":"Purchase (Rs.)"},title="Daily Purchase Trend")
        if not pd_.empty else go.Figure())
    fig_d.update_layout(**CHART_LAYOUT)

    sg=p.get("sgst",pd.Series([0])).sum(); cg=p.get("cgst",pd.Series([0])).sum()
    ig=p.get("igst",pd.Series([0])).sum(); gt=p.get("total_gst",pd.Series([0])).sum()
    fig_g = go.Figure(go.Pie(labels=["SGST","CGST","IGST"],values=[sg,cg,ig],
        hole=0.5,marker_colors=[C_BLUE,C_SKY,C_AMBER],textinfo="label+percent"))
    fig_g.update_layout(**CHART_LAYOUT,title="GST Split -- {}".format(fmt_inr(gt)))

    sf = p.groupby("supplier_name")["grn_number"].count().sort_values(ascending=False).head(10).reset_index()
    sf.columns = ["supplier_name","grn_count"]
    fig_f = px.bar(sf,x="grn_count",y="supplier_name",orientation="h",
        color="grn_count",color_continuous_scale=[[0,"#bbdefb"],[1,C_BLUE]],
        text_auto=True,title="Top 10 by Delivery Frequency",labels={"grn_count":"GRNs","supplier_name":""})
    fig_f.update_coloraxes(showscale=False); fig_f.update_layout(**CHART_LAYOUT,height=320)

    return html.Div([
        html.Div([html.Span(className="accent"),html.Span("Purchase Analysis")],className="section-heading"),
        dbc.Row([dbc.Col(chart_card("Top 15 Suppliers",fig_s),md=7),
                 dbc.Col([chart_card("GST Breakdown",fig_g),chart_card("Delivery Frequency",fig_f)],md=5)],className="g-3"),
        dbc.Row([dbc.Col(chart_card("Daily Purchase Trend",fig_d),md=12)],className="g-3"),
    ])

# ══════════════════════════════════════════════════════════════
# TAB 4 — BRANCH COMPARE
# ══════════════════════════════════════════════════════════════
def render_compare(start_date, end_date):
    s = apply_date_filter(sales_df.copy(),    start_date, end_date, "bill_date")
    p = apply_date_filter(purchase_df.copy(), start_date, end_date, "grn_date")
    BCM = get_branch_color_map()
    sb = sorted(s["branch"].unique().tolist()) if not s.empty else []
    pb = sorted(p["branch"].unique().tolist()) if not p.empty else []
    all_b = sorted(set(sb+pb))

    if len(all_b) < 2:
        msg = "No data loaded yet." if not all_b else "Only one branch ({}). Upload a second.".format(all_b[0])
        return html.Div([html.Div([html.Span(className="accent"),html.Span("Branch Comparison")],className="section-heading"),
                         html.Div("ℹ️  {}".format(msg), className="info-banner")])

    def bk(b):
        col = BCM.get(b, C_TEAL)
        bs = s[s["branch"]==b] if not s.empty else pd.DataFrame()
        bp = p[p["branch"]==b] if not p.empty else pd.DataFrame()
        return dbc.Card([
            dbc.CardHeader(html.Span(b, style={"fontWeight":700,"color":col})),
            dbc.CardBody([
                dbc.Row([
                    dbc.Col(kpi_card("Total Sales",fmt_inr(bs["net_amount"].sum()) if not bs.empty else "--","{} days".format(len(bs)) if not bs.empty else ""),md=6),
                    dbc.Col(kpi_card("Avg Daily",fmt_inr(bs["net_amount"].mean()) if not bs.empty else "--"),md=6),
                ],className="g-2 mb-2"),
                dbc.Row([
                    dbc.Col(kpi_card("Total Purchase",fmt_inr(bp["net_amount"].sum()) if not bp.empty else "--","","purchase"),md=6),
                    dbc.Col(kpi_card("Avg Margin %","{:.1f}%".format(bs["margin_pct"].mean()) if (not bs.empty and "margin_pct" in bs.columns) else "--","","margin"),md=6),
                ],className="g-2"),
            ]),
        ], style={"border":"2px solid {}".format(col),"borderRadius":"12px"})

    cw = max(3, 12//len(all_b))
    krow = dbc.Row([dbc.Col(bk(b),md=cw) for b in all_b], className="g-3 mb-3")

    if not p.empty and "grn_date" in p.columns:
        _p = p.copy(); _p["_month"] = _p["grn_date"].dt.to_period("M").astype(str)
        pc = _p.groupby(["_month","branch"])["net_amount"].sum().reset_index()
        fig_pc = px.bar(pc,x="branch",y="net_amount",color="_month",barmode="group",
            color_discrete_sequence=[C_BLUE, C_SKY, C_TEAL, C_PURPLE],text_auto=".2s",
            title="Purchase by Branch and Month",
            labels={"net_amount":"Purchase (Rs.)","branch":"Branch","_month":"Month"})
    else:
        fig_pc = go.Figure()
    fig_pc.update_layout(**CHART_LAYOUT)

    secs = [html.Div([html.Span(className="accent"),html.Span("Branch Comparison")],className="section-heading"),
            krow,
            dbc.Row([dbc.Col(chart_card("Purchase by Branch and Month",fig_pc),md=12)],className="g-3")]

    if len(pb) >= 2 and "supplier_name" in p.columns:
        b1,b2 = pb[0],pb[1]
        s1=set(p[p["branch"]==b1]["supplier_name"].dropna().unique())
        s2=set(p[p["branch"]==b2]["supplier_name"].dropna().unique())
        shared = s1&s2
        venn = pd.DataFrame({"Category":["Only {}".format(b1),"Shared","Only {}".format(b2)],
                              "Count":[len(s1-s2),len(shared),len(s2-s1)]})
        fv = px.bar(venn,x="Category",y="Count",color="Category",
            color_discrete_map={"Only {}".format(b1):BCM.get(b1,C_GREEN),
                                 "Shared":C_SKY, "Only {}".format(b2):BCM.get(b2,C_PURPLE)},
            text_auto=True,title="Supplier Overlap ({} shared)".format(len(shared)))
        fv.update_layout(**CHART_LAYOUT)
        secs.append(dbc.Row([dbc.Col(chart_card("Supplier Overlap",fv),md=12)],className="g-3"))

    if not p.empty and "supplier_name" in p.columns:
        tc = []
        for b in pb:
            bp = p[p["branch"]==b]
            if bp.empty: continue
            ts = bp.groupby("supplier_name")["net_amount"].sum().sort_values(ascending=True).tail(8).reset_index()
            ft = px.bar(ts,x="net_amount",y="supplier_name",orientation="h",
                title="Top Suppliers -- {}".format(b),color_discrete_sequence=[BCM.get(b,C_TEAL)],
                text_auto=".2s",labels={"net_amount":"Rs.","supplier_name":""})
            ft.update_layout(**CHART_LAYOUT,height=280)
            tc.append(dbc.Col(chart_card("Top Suppliers -- {}".format(b),ft),md=cw))
        if tc: secs.append(dbc.Row(tc,className="g-3"))
    return html.Div(secs)

# ══════════════════════════════════════════════════════════════
# TAB 5 — UPLOAD DATA  (Admin only)
# ══════════════════════════════════════════════════════════════
def render_upload_tab():
    # Determine caller's domain to adapt UI wording
    try:
        _is_tenant = current_user.is_authenticated and current_user.is_tenant_user()
        _domain    = getattr(current_user, "tenant_domain", "pharmacy") if _is_tenant else "pharmacy"
        _tid_upload = int(current_user.tenant_id) if _is_tenant and current_user.tenant_id else None
    except Exception:
        _domain    = "pharmacy"
        _tid_upload = None

    _is_pharmacy = (_domain == "pharmacy")

    # Domain-specific copy
    if _is_pharmacy:
        _banner     = "Upload Sales or Purchase reports from your POS system. App auto-detects report type."
        _step1_title = "Step 1 — Drop your Excel file"
        _file_types  = ".xlsx,.xls,.csv"
        _file_hint   = "Supports: .xlsx  .xls  .csv  (POS export or any tabular format)"
        _branch_lbl  = "Branch / Location Name"
        _branch_ph   = "e.g. Keelkattalai"
    else:
        _banner     = ("Upload any CSV or Excel file with your business data. "
                       "InsightHub auto-detects your column headers and classifies revenue vs cost data.")
        _step1_title = "Step 1 — Drop your data file"
        _file_types  = ".xlsx,.xls,.csv"
        _file_hint   = "Supports: .xlsx  .xls  .csv  (QuickBooks, Tally, and standard exports)"
        _branch_lbl  = "Division / Region / Product"
        _branch_ph   = "e.g. APAC, Enterprise, SaaS"

    # Fetch upload history scoped to this tenant
    hist = get_upload_history(engine, tenant_id=_tid_upload)
    hist_cols = [{"name":"File","id":"filename"},{"name":"Type","id":"report_type"},
                 {"name":"Division / Branch","id":"branch"},{"name":"Period","id":"month_label"},
                 {"name":"Rows","id":"row_count"},{"name":"Uploaded At","id":"uploaded_at"},
                 {"name":"Duplicate?","id":"duplicate_warning"}]
    hist_data = hist.to_dict("records") if not hist.empty else []

    return html.Div([
        html.Div([html.Span(className="accent"), html.Span("Upload New Data")],
                 className="section-heading"),
        html.Div(_banner, className="info-banner"),

        html.Div(className="chart-card", children=[
            html.Div(_step1_title, className="chart-card-title"),
            dcc.Upload(
                id="upload-file", accept=_file_types, multiple=False,
                children=html.Div([
                    html.Div("📂", style={"fontSize":"2.5rem","marginBottom":"0.3rem"}),
                    html.Div("Drag and Drop or Click to Browse", style={"fontWeight":600}),
                    html.Div(_file_hint,
                             style={"fontSize":"0.72rem","color":"#aaa","marginTop":"0.4rem"}),
                ]),
                className="upload-area",
            ),
            html.Div(id="upload-detect-result", style={"marginTop":"0.8rem"}),
        ]),

        html.Div(id="upload-config-card", style={"display":"none"}, children=[
            html.Div(className="chart-card", children=[
                html.Div("Step 2 — Confirm details and Load", className="chart-card-title"),
                dbc.Row([
                    dbc.Col([html.Div("Detected Type", className="sidebar-label"),
                             html.Div(id="upload-type-badge")], md=3),
                    dbc.Col([html.Div(_branch_lbl, className="sidebar-label"),
                             dbc.Input(id="upload-branch", placeholder=_branch_ph,
                                       debounce=True, style={"fontSize":"0.85rem"})], md=3),
                    dbc.Col([html.Div("Period (Month / Quarter / Year)", className="sidebar-label"),
                             dbc.Input(id="upload-month", placeholder="e.g. Apr 2026 or Q1 2026",
                                       debounce=True, style={"fontSize":"0.85rem"})], md=3),
                    dbc.Col([html.Div(" ", className="sidebar-label"),
                             dbc.Button("Load into Dashboard", id="upload-confirm-btn",
                                        color="success", className="w-100", disabled=True,
                                        style={"fontWeight":600})], md=3),
                ], className="g-3 mb-3"),
                html.Div(id="upload-preview-container"),
            ]),
        ]),

        html.Div(id="upload-status-msg", style={"marginBottom":"0.8rem"}),

        html.Div(className="chart-card", children=[
            html.Div([
                html.Div("Upload History", className="chart-card-title",
                         style={"display":"inline-block"}),
                dbc.Button("🧹 Remove Duplicate Uploads", id="fix-duplicates-btn",
                           color="warning", size="sm", outline=True,
                           style={"fontSize":"0.78rem","fontWeight":600,"float":"right",
                                  "marginTop":"-4px"}),
            ], style={"overflow":"hidden","marginBottom":"0.5rem"}),
            html.Div(id="fix-duplicates-feedback"),
            html.Div(id="upload-history-container", children=[
                dash_table.DataTable(
                    id="upload-history-table", columns=hist_cols, data=hist_data,
                    page_size=10, style_table={"overflowX":"auto"},
                    style_cell={"fontSize":"0.78rem","padding":"6px 10px","textAlign":"left"},
                    style_header={"backgroundColor":"#EFF6FF","fontWeight":"bold","color":C_NAVY},
                    style_data_conditional=[{"if":{"filter_query":"{duplicate_warning} = 1"},
                        "backgroundColor":"#fff3cd","color":"#856404"}],
                ) if hist_data else html.Div("No uploads yet.",
                                             style={"color":"#888","fontSize":"0.85rem"}),
            ]),
        ]),
    ])

# ══════════════════════════════════════════════════════════════
# TAB 6 — USER MANAGEMENT  (Admin only)
# ══════════════════════════════════════════════════════════════
def render_users_tab():
    # Determine if the caller is a tenant admin — scope the list accordingly
    try:
        _caller_is_tenant = current_user.is_authenticated and current_user.is_tenant_user()
        _caller_tid       = int(current_user.tenant_id) if _caller_is_tenant and current_user.tenant_id else None
        _caller_tname     = current_user.tenant_name    if _caller_is_tenant else None
    except Exception:
        _caller_is_tenant = False
        _caller_tid       = None
        _caller_tname     = None

    # Fetch only the relevant users:
    #   - internal admin → all internal (no tenant) users  [tenant_id=None]
    #   - tenant admin   → only their own tenant's users   [tenant_id=N]
    if _caller_is_tenant and _caller_tid:
        users = list_users(tenant_id=_caller_tid)
    else:
        users = list_users()          # internal users only (default)

    user_rows = users.to_dict("records") if not users.empty else []
    user_cols = [
        {"name":"ID",           "id":"id"},
        {"name":"Username",     "id":"username"},
        {"name":"Display Name", "id":"display_name"},
        {"name":"Role",         "id":"role"},
        {"name":"Tenant",       "id":"tenant_name"},
        {"name":"Active",       "id":"active"},
        {"name":"Created",      "id":"created_at"},
    ]

    # Build the tenant assignment dropdown (internal admins only)
    cloud_tenant_opts = []
    if not _caller_is_tenant:
        _, tenants_resp = call_api("GET", "/tenants")
        tenant_options = [{"label": "— None —", "value": ""}]
        if isinstance(tenants_resp, list):
            tenant_options += [
                {"label": t.get("name", ""), "value": f"{t.get('id','')}|{t.get('name','')}"}
                for t in tenants_resp
            ]
            cloud_tenant_opts = [{"label": f"{t.get('name','')} ({t.get('domain_type','')})",
                                  "value": int(t.get("id"))}
                                 for t in tenants_resp if t.get("id") is not None]
        tenant_col = dbc.Col([
            html.Div("Assign Tenant", className="sidebar-label"),
            dcc.Dropdown(id="new-tenant", options=tenant_options,
                         value="", clearable=True, placeholder="— None —",
                         style={"fontSize":"0.85rem"}),
        ], md=2)
    else:
        # Tenant admins: pre-assign to their own tenant (hidden, value carried in a Store)
        tenant_col = dbc.Col([
            html.Div("Tenant", className="sidebar-label"),
            html.Div(
                _caller_tname or "Your Tenant",
                style={"fontSize":"0.85rem","padding":"6px 10px",
                       "background":"#f0f8f4","borderRadius":"6px",
                       "border":"1px solid #BFDBFE","color":C_BLUE,"fontWeight":600},
            ),
            # Hidden dropdown so the callback still has an id="new-tenant" to read
            dcc.Dropdown(id="new-tenant",
                         options=[{"label": _caller_tname or "", "value": f"{_caller_tid}|{_caller_tname or ''}"}],
                         value=f"{_caller_tid}|{_caller_tname or ''}",
                         clearable=False, style={"display":"none"}),
        ], md=2)

    title_suffix = f" — {_caller_tname}" if _caller_is_tenant else ""

    return html.Div([
        html.Div([html.Span(className="accent"),
                  html.Span(f"User Management{title_suffix}")],
                 className="section-heading"),
        html.Div("🛡️  Only Admins can access this page. Viewer accounts cannot upload data or manage users.",
                 className="info-banner"),

        # Users table
        html.Div(className="chart-card", children=[
            html.Div("Active Users", className="chart-card-title"),
            dash_table.DataTable(
                id="users-table",
                columns=user_cols, data=user_rows,
                page_size=10,
                style_table={"overflowX":"auto"},
                style_cell={"fontSize":"0.82rem","padding":"7px 12px","textAlign":"left"},
                style_header={"backgroundColor":"#EFF6FF","fontWeight":"bold","color":C_NAVY},
                style_data_conditional=[
                    {"if":{"filter_query":"{role} = admin"},
                     "fontWeight":"600","color":C_GREEN},
                    {"if":{"filter_query":'{active} = "No"'},
                     "color":"#94a3b8","fontStyle":"italic"},
                ],
                row_selectable="single",
                selected_rows=[],
            ),
            html.Div(id="user-action-result", style={"marginTop":"0.6rem"}),
            html.Hr(style={"borderColor":"#e2e8f0","margin":"0.8rem 0"}),
            html.Div("Selected user actions:", className="sidebar-label",
                     style={"marginBottom":"0.4rem"}),
            dbc.Row([
                dbc.Col(dbc.Button("Toggle Role (Admin⇔Viewer)", id="btn-toggle-role",
                                   color="warning", size="sm", outline=True), md=3),
                dbc.Col(dbc.Button("Deactivate User", id="btn-deactivate",
                                   color="danger", size="sm", outline=True), md=2),
                dbc.Col(dbc.Button("Reactivate User", id="btn-reactivate",
                                   color="success", size="sm", outline=True), md=2),
            ], className="g-2"),
            html.Hr(style={"borderColor":"#e2e8f0","margin":"0.8rem 0"}),
            html.Div("Reset password (passwords are encrypted and cannot be shown — set a new one):",
                     className="sidebar-label", style={"marginBottom":"0.4rem"}),
            dbc.Row([
                dbc.Col(dbc.Input(id="reset-pw-input", placeholder="new password (blank = auto-generate)",
                                  type="text", size="sm", style={"fontSize":"0.8rem"}), md=4),
                dbc.Col(dbc.Button("Reset Password", id="btn-reset-pw",
                                   color="primary", size="sm", outline=True), md=2),
            ], className="g-2"),
        ]),

        # Add new user
        html.Div(className="chart-card", style={"marginTop":"1rem"}, children=[
            html.Div("Add New User", className="chart-card-title"),
            dbc.Row([
                dbc.Col([html.Div("Username",     className="sidebar-label"),
                         dbc.Input(id="new-username", placeholder="username",
                                   debounce=True, style={"fontSize":"0.85rem"})], md=2),
                dbc.Col([html.Div("Display Name", className="sidebar-label"),
                         dbc.Input(id="new-display", placeholder="Full Name",
                                   debounce=True, style={"fontSize":"0.85rem"})], md=2),
                dbc.Col([html.Div("Password",     className="sidebar-label"),
                         dbc.Input(id="new-password", placeholder="password",
                                   type="password", debounce=True,
                                   style={"fontSize":"0.85rem"})], md=2),
                dbc.Col([html.Div("Role",         className="sidebar-label"),
                         dcc.Dropdown(id="new-role",
                             options=[{"label":"Admin","value":"admin"},
                                      {"label":"Viewer","value":"viewer"}],
                             value="viewer", clearable=False,
                             style={"fontSize":"0.85rem"})], md=2),
                tenant_col,
                dbc.Col([html.Div(" ", className="sidebar-label"),
                         dbc.Button("Add User", id="btn-add-user", color="success",
                                    style={"width":"100%","fontWeight":600})], md=2),
            ], className="g-3 mb-2"),
            html.Div(id="add-user-result"),
        ]),

        # Create a CLOUD login (Neon) mapped to a customer — this is the login the
        # CUSTOMER APP (:3000) uses. Internal admins only.
        (html.Div(className="chart-card", style={"marginTop":"1rem"}, children=[
            html.Div("Create Customer Login (customer app)", className="chart-card-title"),
            html.Div("This login works in the customer app (:3000) and is scoped to the "
                     "selected customer's data.",
                     style={"fontSize":"0.78rem","color":"#64748b","marginBottom":"0.6rem"}),
            dbc.Row([
                dbc.Col([html.Div("Customer", className="sidebar-label"),
                         dcc.Dropdown(id="cust-login-tenant", options=cloud_tenant_opts,
                                      placeholder="Select customer",
                                      style={"fontSize":"0.85rem"})], md=3),
                dbc.Col([html.Div("Username", className="sidebar-label"),
                         dbc.Input(id="cust-login-user", placeholder="username",
                                   style={"fontSize":"0.85rem"})], md=2),
                dbc.Col([html.Div("Password", className="sidebar-label"),
                         dbc.Input(id="cust-login-pass", placeholder="password", type="text",
                                   style={"fontSize":"0.85rem"})], md=3),
                dbc.Col([html.Div("Role", className="sidebar-label"),
                         dcc.Dropdown(id="cust-login-role",
                             options=[{"label":"Viewer","value":"viewer"},
                                      {"label":"Admin","value":"admin"}],
                             value="viewer", clearable=False,
                             style={"fontSize":"0.85rem"})], md=2),
                dbc.Col([html.Div(" ", className="sidebar-label"),
                         dbc.Button("Create Login", id="btn-cust-login", color="primary",
                                    style={"width":"100%","fontWeight":600})], md=2),
            ], className="g-3 mb-2"),
            html.Div(id="cust-login-result"),
        ]) if not _caller_is_tenant else html.Div()),
    ])

# ══════════════════════════════════════════════════════════════
# USER MANAGEMENT CALLBACKS
# ══════════════════════════════════════════════════════════════
@app.callback(
    Output("cust-login-result", "children"),
    Input("btn-cust-login", "n_clicks"),
    State("cust-login-tenant", "value"),
    State("cust-login-user", "value"),
    State("cust-login-pass", "value"),
    State("cust-login-role", "value"),
    prevent_initial_call=True,
)
def create_customer_login(_n, tenant_id, username, password, role):
    if not tenant_id:
        return dbc.Alert("Select a customer.", color="warning", dismissable=True)
    if not username or not password:
        return dbc.Alert("Username and password are required.", color="warning", dismissable=True)
    if len(password) < 6:
        return dbc.Alert("Password must be at least 6 characters.", color="warning", dismissable=True)
    try:
        status, data = call_api("POST", "/auth/users", json_body={
            "username": username.strip().lower(),
            "display_name": username.strip(),
            "password": password,
            "role": role or "viewer",
            "tenant_id": int(tenant_id),
        })
        if status in (200, 201):
            return dbc.Alert([
                html.Strong(f"✅ Customer login '{username.strip().lower()}' created."),
                html.Br(),
                html.Span("They can now sign in at the customer app (:3000) and will see only "
                          "this customer's data.", style={"fontSize": "0.8rem"}),
            ], color="success", dismissable=True)
        if status == 409:
            return dbc.Alert("That username already exists — pick another (or reset its password).",
                             color="warning", dismissable=True)
        return dbc.Alert(f"Create failed ({status}): {data}", color="danger", dismissable=True)
    except Exception as e:
        return dbc.Alert(f"Error: {e}", color="danger", dismissable=True)


@app.callback(
    Output("user-action-result", "children", allow_duplicate=True),
    Input("btn-reset-pw", "n_clicks"),
    State("users-table", "selected_rows"),
    State("users-table", "data"),
    State("reset-pw-input", "value"),
    prevent_initial_call=True,
)
def reset_user_password(_n, selected, data, new_pw):
    if not selected or not data:
        return dbc.Alert("Select a user row first.", color="warning", duration=3000)
    row  = data[selected[0]]
    uid  = row.get("id")
    name = row.get("username", "user")
    pw   = (new_pw or "").strip()
    if not pw:
        import secrets
        pw = "IH-" + secrets.token_urlsafe(6)
    try:
        reset_password(uid, pw)
        return dbc.Alert([
            html.Strong(f"Password reset for '{name}'.  "),
            html.Span("New password (copy it now — it won't be shown again): "),
            html.Code(pw, style={"fontSize": "0.9rem", "background": "#EFF6FF",
                                 "padding": "2px 6px", "borderRadius": "4px"}),
        ], color="success", dismissable=True)
    except Exception as e:
        return dbc.Alert(f"Reset failed: {e}", color="danger", dismissable=True)


@app.callback(
    Output("user-action-result","children"),
    Output("users-table","data"),
    Input("btn-toggle-role", "n_clicks"),
    Input("btn-deactivate",  "n_clicks"),
    Input("btn-reactivate",  "n_clicks"),
    State("users-table","selected_rows"),
    State("users-table","data"),
    prevent_initial_call=True,
)
def user_row_action(n_toggle, n_deact, n_react, selected, data):
    if not selected or not data:
        return dbc.Alert("Select a user row first.", color="warning", duration=3000), no_update
    row  = data[selected[0]]
    uid  = row["id"]
    name = row["username"]
    triggered = ctx.triggered_id

    if triggered == "btn-toggle-role":
        if name == "admin":
            return dbc.Alert("Cannot change role of the primary admin.", color="danger", duration=4000), no_update
        new_role = "viewer" if row["role"] == "admin" else "admin"
        update_user_role(uid, new_role)
        msg = "Role for '{}' changed to {}.".format(name, new_role)
        color = "success"
    elif triggered == "btn-deactivate":
        if name == "admin":
            return dbc.Alert("Cannot deactivate the primary admin.", color="danger", duration=4000), no_update
        deactivate_user(uid)
        msg = "User '{}' deactivated.".format(name)
        color = "warning"
    elif triggered == "btn-reactivate":
        reactivate_user(uid)
        msg = "User '{}' reactivated.".format(name)
        color = "success"
    else:
        return no_update, no_update

    # Refresh the table scoped to the caller's tenant
    try:
        _rtid = (int(current_user.tenant_id)
                 if current_user.is_authenticated and current_user.is_tenant_user()
                 and current_user.tenant_id else None)
    except Exception:
        _rtid = None
    fresh = list_users(tenant_id=_rtid).to_dict("records")
    return dbc.Alert(msg, color=color, duration=4000), fresh


@app.callback(
    Output("add-user-result","children"),
    Output("users-table","data",allow_duplicate=True),
    Input("btn-add-user",  "n_clicks"),
    State("new-username",  "value"),
    State("new-display",   "value"),
    State("new-password",  "value"),
    State("new-role",      "value"),
    State("new-tenant",    "value"),
    prevent_initial_call=True,
)
def add_new_user(_, username, display, password, role, tenant_val):
    if not username or not password:
        return dbc.Alert("Username and Password are required.", color="warning", duration=4000), no_update
    # Parse tenant value: "id|name" format
    tenant_id, tenant_name = None, None
    if tenant_val:
        parts = tenant_val.split("|", 1)
        if len(parts) == 2:
            try: tenant_id = int(parts[0])
            except ValueError: pass
            tenant_name = parts[1]
    # Tenant admins can only create users for their own tenant — enforce it
    try:
        if current_user.is_authenticated and current_user.is_tenant_user():
            tenant_id   = int(current_user.tenant_id) if current_user.tenant_id else tenant_id
            tenant_name = current_user.tenant_name    or tenant_name
            # Also inherit domain from the caller's profile
            _tdom = getattr(current_user, "tenant_domain", "pharmacy")
        else:
            _tdom = "pharmacy"
    except Exception:
        _tdom = "pharmacy"

    err = create_user(username.strip(), password, role or "viewer", display or "",
                      tenant_id=tenant_id, tenant_name=tenant_name,
                      tenant_domain=_tdom)
    if err:
        return dbc.Alert("Error: {}".format(err), color="danger", duration=5000), no_update
    # Refresh scoped to caller's tenant
    try:
        _rtid = (int(current_user.tenant_id)
                 if current_user.is_authenticated and current_user.is_tenant_user()
                 and current_user.tenant_id else None)
    except Exception:
        _rtid = None
    fresh = list_users(tenant_id=_rtid).to_dict("records")
    tenant_label = f" → Tenant: {tenant_name}" if tenant_name else ""
    return dbc.Alert(
        "User '{}' created as {}{}.".format(username.strip(), role, tenant_label),
        color="success", duration=4000
    ), fresh

# ══════════════════════════════════════════════════════════════
# DOWNLOAD CALLBACKS
# ══════════════════════════════════════════════════════════════
def _filt_s(branch, sd, ed):
    s = sales_df.copy()
    if branch != "All": s = s[s["branch"]==branch]
    s = apply_date_filter(s, sd, ed, "bill_date")
    for c in s.select_dtypes("datetime64[ns]").columns: s[c]=s[c].dt.strftime("%Y-%m-%d")
    return s

def _filt_p(branch, sd, ed):
    p = purchase_df.copy()
    if branch != "All": p = p[p["branch"]==branch]
    p = apply_date_filter(p, sd, ed, "grn_date")
    for c in p.select_dtypes("datetime64[ns]").columns: p[c]=p[c].dt.strftime("%Y-%m-%d")
    return p

@app.callback(Output("dl-sales-csv","data"),Input("btn-dl-sales-csv","n_clicks"),
    State("filter-branch","value"),State("filter-date","start_date"),State("filter-date","end_date"),
    prevent_initial_call=True)
def dl_s_csv(_,b,sd,ed):
    return dcc.send_data_frame(_filt_s(b,sd,ed).to_csv,"sales_export.csv",index=False)

@app.callback(Output("dl-sales-xlsx","data"),Input("btn-dl-sales-xlsx","n_clicks"),
    State("filter-branch","value"),State("filter-date","start_date"),State("filter-date","end_date"),
    prevent_initial_call=True)
def dl_s_xlsx(_,b,sd,ed):
    return dcc.send_data_frame(_filt_s(b,sd,ed).to_excel,"sales_export.xlsx",index=False,sheet_name="Sales")

@app.callback(Output("dl-purch-csv","data"),Input("btn-dl-purch-csv","n_clicks"),
    State("filter-branch","value"),State("filter-date","start_date"),State("filter-date","end_date"),
    prevent_initial_call=True)
def dl_p_csv(_,b,sd,ed):
    return dcc.send_data_frame(_filt_p(b,sd,ed).to_csv,"purchases_export.csv",index=False)

@app.callback(Output("dl-purch-xlsx","data"),Input("btn-dl-purch-xlsx","n_clicks"),
    State("filter-branch","value"),State("filter-date","start_date"),State("filter-date","end_date"),
    prevent_initial_call=True)
def dl_p_xlsx(_,b,sd,ed):
    return dcc.send_data_frame(_filt_p(b,sd,ed).to_excel,"purchases_export.xlsx",index=False,sheet_name="Purchases")

@app.callback(Output("dl-pdf","data"),Input("btn-dl-pdf","n_clicks"),
    State("filter-branch","value"),State("filter-date","start_date"),State("filter-date","end_date"),
    prevent_initial_call=True)
def dl_pdf(_,branch,sd,ed):
    s=sales_df.copy(); p=purchase_df.copy()
    if branch!="All": s=s[s["branch"]==branch]; p=p[p["branch"]==branch]
    s=apply_date_filter(s,sd,ed,"bill_date"); p=apply_date_filter(p,sd,ed,"grn_date")
    pdf_bytes = generate_pdf(s,p,sd,ed,branch,fmt_inr)
    return dcc.send_bytes(pdf_bytes,"insighthub_report_{}.pdf".format(date.today().strftime("%Y%m%d")))

# ══════════════════════════════════════════════════════════════
# UPLOAD CALLBACKS
# ══════════════════════════════════════════════════════════════
@app.callback(
    Output("upload-detect-result","children"),
    Output("upload-config-card","style"),
    Output("upload-type-badge","children"),
    Output("upload-raw-store","data"),
    Input("upload-file","contents"),
    State("upload-file","filename"),
    prevent_initial_call=True,
)
def handle_file_drop(contents, filename):
    if not contents:
        return no_update, {"display":"none"}, no_update, no_update

    # Pass the tenant's domain so generic detection kicks in for non-pharmacy
    try:
        _drop_domain = (getattr(current_user, "tenant_domain", "pharmacy")
                        if current_user.is_authenticated and current_user.is_tenant_user()
                        else "pharmacy")
    except Exception:
        _drop_domain = "pharmacy"

    df_raw, rtype, err = parse_upload(contents, filename, domain=_drop_domain)
    if err:
        return (dbc.Alert("Error: {}".format(err), color="danger", dismissable=True),
                {"display":"none"}, no_update, no_update)

    # Human-readable type labels
    _type_labels = {
        "sales":             "Sales Report (POS)",
        "purchase":          "Purchase / GRN Report",
        "generic_sales":     "Revenue / Sales Data",
        "generic_purchases": "Cost / Expense Data",
        "square_sales":      "Square POS Export ✓",
        "shopify_sales":     "Shopify Orders Export ✓",
    }
    bl = _type_labels.get(rtype, rtype.replace("_", " ").title())
    bc = "success" if "sales" in rtype else "primary"

    result = dbc.Alert(
        [html.Strong("✅ Detected: {}  ".format(bl)),
         dbc.Badge(filename, color="secondary")],
        color="success", style={"fontSize":"0.85rem","padding":"0.5rem 1rem"},
    )
    return (result, {"display":"block"},
            dbc.Badge(bl, color=bc, style={"fontSize":"0.85rem","padding":"0.4rem 0.8rem"}),
            {"report_type": rtype, "filename": filename, "df_raw_json": df_raw.to_json()})

@app.callback(
    Output("upload-preview-container","children"),
    Output("upload-prev-store","data"),
    Output("upload-confirm-btn","disabled"),
    Input("upload-branch","value"),Input("upload-month","value"),
    State("upload-raw-store","data"),
    prevent_initial_call=True,
)
def update_preview(branch,month,raw_store):
    if not raw_store or not branch or not month: return no_update,no_update,True
    try:
        df_raw = pd.read_json(io.StringIO(raw_store["df_raw_json"]))
        _rtype = raw_store["report_type"]
        # Pharmacy POS reports use numeric column indices; generic reports keep named headers
        if _rtype in ("sales", "purchase"):
            df_raw.columns = range(len(df_raw.columns))
        sd = build_preview(df_raw, _rtype, branch.strip(), month.strip())
        sd["filename"] = raw_store["filename"]
        preview = html.Div([
            html.Div("Preview -- first 5 rows of {} total:".format(sd["row_count"]),
                     style={"fontSize":"0.8rem","color":"#555","marginBottom":"0.4rem"}),
            dash_table.DataTable(columns=[{"name":c,"id":c} for c in sd["columns"][:8]],
                data=sd["preview"],style_table={"overflowX":"auto"},
                style_cell={"fontSize":"0.75rem","padding":"5px 8px","maxWidth":"150px",
                             "overflow":"hidden","textOverflow":"ellipsis"},
                style_header={"backgroundColor":"#e8f5e9","fontWeight":"bold"})])
        return preview,sd,False
    except Exception as e:
        return dbc.Alert("Preview error: {}".format(e),color="warning"),no_update,True

@app.callback(
    Output("data-version","data"),
    Output("upload-status-msg","children"),
    Output("upload-history-container","children"),
    Output("upload-raw-store","data",allow_duplicate=True),
    Output("upload-prev-store","data",allow_duplicate=True),
    Output("upload-config-card","style",allow_duplicate=True),
    Output("upload-detect-result","children",allow_duplicate=True),
    Output("main-tabs","value",allow_duplicate=True),
    Input("upload-confirm-btn","n_clicks"),
    State("upload-prev-store","data"),State("data-version","data"),
    State("admin-tenant-select","value"),
    prevent_initial_call=True,
)
def confirm_upload(n,store_data,version,admin_tenant=0):
    global sales_df,purchase_df
    if not store_data:
        return no_update,dbc.Alert("Nothing to upload.",color="warning"),no_update,no_update,no_update,no_update,no_update,no_update

    # ── Admin upload-on-behalf → write to the SELECTED customer's Neon tenant ──
    try:
        _is_int_admin = (current_user.is_authenticated and current_user.is_admin()
                         and not current_user.is_tenant_user())
    except Exception:
        _is_int_admin = False
    try:
        _sel_tid = int(admin_tenant) if admin_tenant not in (None, "", 0, "0", "medstar") else None
    except (TypeError, ValueError):
        _sel_tid = None
    if _is_int_admin and _sel_tid:
        _dom = _neon_tenant_domain(_sel_tid)
        _rows, _err = _append_upload_neon(store_data, _sel_tid, _dom)
        if _err:
            return (no_update, dbc.Alert(f"Upload to customer failed: {_err}",
                    color="danger", dismissable=True),
                    no_update, no_update, no_update, no_update, no_update, no_update)
        _cust = _tenant_name_by_id(_sel_tid) or f"customer #{_sel_tid}"
        _msg = dbc.Alert([
            html.Strong(f"✅  {_rows} rows uploaded for {_cust}."),
            html.Br(),
            html.Span("It's now in the cloud warehouse — the customer app and the "
                      "Customer selector above will show it.", style={"fontSize": "0.8rem"}),
        ], color="success", dismissable=True)
        return (version + 1, _msg, no_update, None, None, {"display": "none"},
                html.Div(), "overview")

    # Tag the upload with the uploader's tenant_id (None for internal admins)
    try:
        _upload_tid = (int(current_user.tenant_id)
                       if current_user.is_authenticated and current_user.is_tenant_user()
                       and current_user.tenant_id else None)
    except Exception:
        _upload_tid = None
    row_count,duplicate,error = append_upload_to_db(store_data, engine, tenant_id=_upload_tid)
    if error:
        return no_update,dbc.Alert("Upload failed: {}".format(error),color="danger",dismissable=True),no_update,no_update,no_update,no_update,no_update,no_update
    sales_df,purchase_df = load_from_db(engine)

    # ── Domain auto-detection from uploaded column names ──────────
    try:
        from domain_config import detect_domain_from_columns as _detect_dom
        _raw_df = pd.read_json(store_data["df"], orient="split") if "df" in store_data else pd.DataFrame()
        if not _raw_df.empty:
            _detected_domain = _detect_dom(list(_raw_df.columns))
            if _upload_tid and _agent_memory is not None and _detected_domain != "generic":
                _agent_memory.save_preferences(_upload_tid, {"detected_domain": _detected_domain})
                logger.info("[upload] domain detected: %s for tenant %s", _detected_domain, _upload_tid)
    except Exception as _de:
        logger.debug("[upload] domain detection skipped: %s", _de)

    # ── Trigger the pipeline orchestrator (non-blocking, never breaks upload) ──
    # validate → transform-load (star schema + marts) → verify parity, with run
    # history recorded. Runs in the background so the upload UI is never delayed.
    try:
        _wh_domain = None
        try:
            _wh_domain = locals().get("_detected_domain")
        except Exception:
            _wh_domain = None
        _wh_domain = _wh_domain or getattr(current_user, "tenant_domain", "generic") or "generic"
        def _bg_pipeline(_eng, _tid, _dom):
            try:
                import orchestrator as _orch
                _res = _orch.run_pipeline(_eng, _tid, _dom, trigger="upload")
                logger.info("[orchestrator] upload pipeline: %s", _res)
            except Exception as _oe:
                logger.warning("[orchestrator] upload pipeline failed (non-fatal): %s", _oe)
        threading.Thread(target=_bg_pipeline,
                         args=(engine, _upload_tid, _wh_domain), daemon=True).start()
    except Exception as _oe:
        logger.debug("[orchestrator] upload trigger skipped: %s", _oe)

    # ── Load tenant-scoped data for narrative ─────────────────────
    try:
        if _upload_tid:
            _narr_s = pd.read_sql_query(
                "SELECT * FROM sales WHERE tenant_id=?", engine, params=(_upload_tid,)
            )
            _narr_p = pd.read_sql_query(
                "SELECT * FROM purchases WHERE tenant_id=?", engine, params=(_upload_tid,)
            )
        else:
            _narr_s, _narr_p = sales_df.copy(), purchase_df.copy()
    except Exception:
        _narr_s, _narr_p = sales_df.copy(), purchase_df.copy()

    # Determine currency for narrative
    _narr_cur = "USD"
    try:
        _narr_domain = getattr(current_user, "tenant_domain", "generic") or "generic"
        _narr_cur = "INR" if _narr_domain == "pharmacy" else "USD"
    except Exception:
        pass

    warn = " Duplicate data detected — rows merged." if duplicate else ""
    msg = dbc.Alert([
        html.Strong("✅  {} rows loaded!".format(row_count)),
        html.Span("  Branch: {} | {}{}".format(store_data.get("branch","—"), store_data.get("month_label",""), warn)),
    ], color="warning" if duplicate else "success", dismissable=True)

    hist = get_upload_history(engine, tenant_id=_upload_tid)
    hcols=[{"name":"File","id":"filename"},{"name":"Type","id":"report_type"},
           {"name":"Branch","id":"branch"},{"name":"Month","id":"month_label"},
           {"name":"Rows","id":"row_count"},{"name":"Uploaded At","id":"uploaded_at"},
           {"name":"Duplicate?","id":"duplicate_warning"}]
    new_tbl = dash_table.DataTable(id="upload-history-table",columns=hcols,
        data=hist.to_dict("records"),page_size=10,style_table={"overflowX":"auto"},
        style_cell={"fontSize":"0.78rem","padding":"6px 10px","textAlign":"left"},
        style_header={"backgroundColor":"#EFF6FF","fontWeight":"bold","color":C_NAVY},
        style_data_conditional=[{"if":{"filter_query":"{duplicate_warning} = 1"},
            "backgroundColor":"#fff3cd","color":"#856404"}])

    # ── US-307: Auto-generate narrative + auto-navigate ───────────
    narrative = _generate_upload_narrative(store_data, _narr_s, _narr_p, row_count, duplicate, _narr_cur)
    return version+1, msg, new_tbl, None, None, {"display":"none"}, narrative, "overview"


def _generate_upload_narrative(store_data: dict, sales_df, purchase_df,
                                row_count: int, duplicate: bool,
                                currency: str = "USD") -> object:
    """US-307: Build a plain-language insight card after a successful upload."""
    from ai.groq_client import _make_fmt
    _fmt = _make_fmt(currency)
    try:
        rtype      = store_data.get("report_type", "")
        branch     = store_data.get("branch", "All")
        month_lbl  = store_data.get("month_label", "")
        is_sales   = "sales" in rtype.lower() if rtype else True

        stats_lines = []

        # ── Sales stats ───────────────────────────────────────────
        if sales_df is not None and not sales_df.empty:
            try:
                s = sales_df.copy()
                if branch and branch != "All" and "branch" in s.columns:
                    s = s[s["branch"] == branch]
                if "net_amount" in s.columns:
                    total = s["net_amount"].sum()
                    stats_lines.append(f"💰 Total sales: {_fmt(total)}")
                if "margin_pct" in s.columns:
                    avg_m = s["margin_pct"].mean()
                    stats_lines.append(f"📈 Avg margin: {avg_m:.1f}%")
                for col in ("drug_name", "item_name", "product_name", "description"):
                    if col in s.columns and "net_amount" in s.columns:
                        top = s.groupby(col)["net_amount"].sum().idxmax()
                        stats_lines.append(f"🏆 Top item: {top}")
                        break
            except Exception:
                pass

        # ── Purchase stats ────────────────────────────────────────
        if purchase_df is not None and not purchase_df.empty:
            try:
                p = purchase_df.copy()
                amt_col = next((c for c in ("total_amount","net_amount","amount") if c in p.columns), None)
                if amt_col:
                    spend = p[amt_col].sum()
                    stats_lines.append(f"🛒 Total purchases: {_fmt(spend)}")
                for col in ("supplier_name","vendor_name","supplier"):
                    if col in p.columns and amt_col:
                        top_sup = p.groupby(col)[amt_col].sum().idxmax()
                        stats_lines.append(f"📦 Top supplier: {top_sup}")
                        break
            except Exception:
                pass

        dup_note = " (duplicates merged)" if duplicate else ""
        headline = (
            f"{'Sales' if is_sales else 'Purchase'} data loaded — "
            f"{row_count:,} rows · {branch} · {month_lbl}{dup_note}."
        )

        lines = [html.P(headline, style={"fontWeight": 600, "marginBottom": "6px",
                                          "color": "#1E293B", "fontSize": "0.88rem"})]
        for stat in stats_lines:
            lines.append(html.Div(stat, style={"fontSize": "0.82rem", "color": "#475569",
                                                "marginBottom": "3px"}))
        lines.append(html.P("Redirecting to Overview for your fresh analysis…",
                             style={"fontSize": "0.78rem", "color": "#059669",
                                    "marginTop": "8px", "marginBottom": 0, "fontStyle": "italic"}))

        return html.Div([
            html.Div("📊 Upload Summary", style={
                "fontSize": "0.72rem", "fontWeight": 700, "textTransform": "uppercase",
                "letterSpacing": "0.05em", "color": "#2563EB", "marginBottom": "8px",
            }),
            *lines,
        ], style={
            "background": "#EFF6FF", "border": "1px solid #BFDBFE",
            "borderRadius": "10px", "padding": "12px 16px", "marginTop": "0.75rem",
        })
    except Exception:
        return ""

# ════════════════════════════════════════════════════════════════
# TENANT PORTAL CALLBACKS  (Day 5)
# ════════════════════════════════════════════════════════════════

# ── Store selected tenant when row clicked ────────────────────
@app.callback(
    Output("selected-tenant-store", "data"),
    Output("module-toggle-panel",   "style"),
    Output("mapping-panel",         "style"),
    Output("module-tenant-label",   "children"),
    Output("module-toggles-grid",   "children"),
    Input("tenants-table",          "selected_rows"),
    State("tenants-table",          "data"),
    prevent_initial_call=True,
)
def on_tenant_selected(selected_rows, table_data):
    hidden  = {"display": "none"}
    visible = {"display": "block"}
    if not selected_rows or not table_data:
        return None, hidden, hidden, "", []

    row      = table_data[selected_rows[0]]
    row_id   = row.get("id")
    row_name = row.get("Name", "")

    # Fetch modules from API
    status, mods = call_api("GET", f"/tenants/{row_id}/modules")
    mod_list = mods if isinstance(mods, list) else []

    toggles = []
    for m in mod_list:
        label   = MODULE_LABELS.get(m["module_name"], m["module_name"])
        enabled = m["is_enabled"]
        toggles.append(
            dbc.Card([
                dbc.CardBody([
                    dbc.Switch(
                        id={"type": "module-switch", "module": m["module_name"]},
                        label=label,
                        value=enabled,
                        style={"fontSize": "0.82rem"},
                    )
                ], style={"padding": "10px 14px"}),
            ], style={"border": "1px solid #dee2e6", "borderRadius": "8px"}),
        )

    label_text = f"— {row_name}"
    return (
        {"tenant_id": row_id, "tenant_name": row_name, "domain": row.get("Domain", "").lower()},
        visible,
        visible,
        label_text,
        toggles,
    )


# ── Create tenant ─────────────────────────────────────────────
@app.callback(
    Output("tenant-action-result", "children"),
    Output("tenants-table",        "data"),
    Input("btn-create-tenant",     "n_clicks"),
    State("new-tenant-name",       "value"),
    State("new-tenant-slug",       "value"),
    State("new-tenant-domain",     "value"),
    State("new-tenant-plan",       "value"),
    State("new-tenant-email",      "value"),
    prevent_initial_call=True,
)
def create_tenant(n, name, slug, domain, plan, email):
    if not name or not slug:
        return dbc.Alert("Name and Slug are required.", color="warning", dismissable=True), no_update

    _name   = name.strip()
    _slug   = slug.strip().lower().replace(" ", "-")
    _domain = domain or "pharmacy"
    _plan   = plan or "basic"
    _email  = email or ""

    # Try FastAPI first
    status, data = call_api("POST", "/tenants", json_body={
        "name":          _name,
        "slug":          _slug,
        "domain_type":   _domain,
        "plan":          _plan,
        "contact_email": _email,
    })

    if status == 201:
        msg = dbc.Alert(f"✅ Tenant '{_name}' created successfully.", color="success", dismissable=True)
        _, tenants = call_api("GET", "/tenants")
        rows = [
            {
                "id":      t.get("id", ""),
                "Name":    t.get("name", ""),
                "Slug":    t.get("slug", ""),
                "Domain":  t.get("domain_type", "").capitalize(),
                "Plan":    t.get("plan", "").capitalize(),
                "Status":  "Active" if t.get("is_active") else "Inactive",
                "Contact": t.get("contact_email", ""),
                "Created": (t.get("created_at", "") or "")[:10],
            }
            for t in (tenants if isinstance(tenants, list) else [])
        ]
        return msg, rows

    elif status == 503:
        # FastAPI offline — save directly to local SQLite
        ok, message = _create_tenant_local(_name, _slug, _domain, _plan, _email)
        if ok:
            rows = _list_tenants_local()
            msg  = dbc.Alert(
                [html.Strong("✅ Tenant saved locally. "),
                 html.Span(f"'{_name}' ({_domain}) added to local database. "
                           "Start the FastAPI service (uvicorn api.main:app --port 8000) "
                           "to sync to the cloud database.")],
                color="success", dismissable=True,
            )
            return msg, rows
        else:
            return dbc.Alert(f"❌ Local save failed: {message}", color="danger", dismissable=True), no_update

    else:
        detail = data.get("detail", "Unknown error") if isinstance(data, dict) else str(data)
        return dbc.Alert(f"❌ Error: {detail}", color="danger", dismissable=True), no_update


# ── Deactivate selected tenant ─────────────────────────────────
@app.callback(
    Output("tenant-action-result", "children", allow_duplicate=True),
    Output("tenants-table",        "data",     allow_duplicate=True),
    Input("btn-deactivate-tenant", "n_clicks"),
    State("selected-tenant-store", "data"),
    prevent_initial_call=True,
)
def deactivate_tenant(n, store):
    if not store or not store.get("tenant_id"):
        return dbc.Alert("Select a tenant row first.", color="warning", dismissable=True), no_update

    tid  = store["tenant_id"]
    name = store.get("tenant_name", "")
    status, data = call_api("DELETE", f"/tenants/{tid}")

    if status == 200:
        msg = dbc.Alert(f"Tenant '{name}' deactivated.", color="warning", dismissable=True)
    else:
        detail = data.get("detail", "Unknown error") if isinstance(data, dict) else str(data)
        return dbc.Alert(f"Error: {detail}", color="danger", dismissable=True), no_update

    _, tenants = call_api("GET", "/tenants")
    rows = [
        {
            "id":      t.get("id", ""),
            "Name":    t.get("name", ""),
            "Slug":    t.get("slug", ""),
            "Domain":  t.get("domain_type", "").capitalize(),
            "Plan":    t.get("plan", "").capitalize(),
            "Status":  "Active" if t.get("is_active") else "Inactive",
            "Contact": t.get("contact_email", ""),
                  "Created": (t.get("created_at", "") or "")[:10],
        }
        for t in (tenants if isinstance(tenants, list) else [])
    ]
    return msg, rows


# -- Save module toggles -------------------------------------------------------
@app.callback(
    Output("module-save-result", "children"),
    Input("btn-save-modules",    "n_clicks"),
    State("selected-tenant-store", "data"),
    State({"type": "module-switch", "module": dash.ALL}, "value"),
    State({"type": "module-switch", "module": dash.ALL}, "id"),
    prevent_initial_call=True,
)
def save_modules(n, store, values, ids):
    if not store or not store.get("tenant_id"):
        return dbc.Alert("No tenant selected.", color="warning", dismissable=True)

    tid     = store["tenant_id"]
    modules = [
        {"module_name": id_dict["module"], "is_enabled": bool(val)}
        for id_dict, val in zip(ids, values)
    ]
    status, data = call_api("PUT", f"/tenants/{tid}/modules", json_body={"modules": modules})

    if status == 200:
        return dbc.Alert("Module settings saved.", color="success", dismissable=True)
    detail = data.get("detail", "Error") if isinstance(data, dict) else str(data)
    return dbc.Alert(f"Error: {detail}", color="danger", dismissable=True)


# -- Load schema mapping rows --------------------------------------------------
@app.callback(
    Output("mapping-rows", "children"),
    Input("mapping-entity-select",   "value"),
    State("selected-tenant-store",   "data"),
    prevent_initial_call=True,
)
def load_mapping_rows(entity, store):
    if not store:
        return html.P("Select a tenant first.", style={"color": "#6c757d", "fontSize": "0.8rem"})

    domain = store.get("domain", "pharmacy")
    tid    = store.get("tenant_id")

    _, schema_data = call_api("GET", f"/domains/{domain}/{entity}")
    fields = schema_data.get("fields", []) if isinstance(schema_data, dict) else []

    _, mappings_data = call_api("GET", f"/tenants/{tid}/mappings", params={"entity": entity})
    existing = {m["canonical_column"]: m["source_column"]
                for m in (mappings_data if isinstance(mappings_data, list) else [])}

    if not fields:
        return html.P(f"No schema defined for {domain}/{entity}.",
                      style={"color": "#6c757d", "fontSize": "0.8rem"})

    rows = []
    rows.append(dbc.Row([
        dbc.Col(html.Strong("Canonical Column", style={"fontSize": "0.72rem", "color": "#495057"}), width=5),
        dbc.Col(html.Strong("Your Source Column", style={"fontSize": "0.72rem", "color": "#495057"}), width=5),
        dbc.Col(html.Strong("Req", style={"fontSize": "0.72rem", "color": "#495057"}), width=2),
    ], className="mb-1"))

    for f in fields:
        cname   = f["canonical_name"]
        src_val = existing.get(cname, cname)
        required = "✱" if f["is_required"] else ""
        rows.append(dbc.Row([
            dbc.Col(html.Span(f["display_name"],
                              title=f["description"],
                              style={"fontSize": "0.8rem", "cursor": "help"}), width=5),
            dbc.Col(dbc.Input(
                id={"type": "mapping-input", "canonical": cname},
                value=src_val,
                size="sm",
                style={"fontSize": "0.78rem"},
            ), width=5),
            dbc.Col(html.Span(required, style={"color": C_GREEN, "fontWeight": "700"}), width=2),
        ], className="mb-1", align="center"))

    return html.Div(rows)


# -- Save schema mappings ------------------------------------------------------
@app.callback(
    Output("mapping-save-result",  "children"),
    Input("btn-save-mappings",     "n_clicks"),
    State("selected-tenant-store", "data"),
    State("mapping-entity-select",  "value"),
    State({"type": "mapping-input", "canonical": dash.ALL}, "value"),
    State({"type": "mapping-input", "canonical": dash.ALL}, "id"),
    prevent_initial_call=True,
)
def save_mappings(n, store, entity, values, ids):
    if not store or not store.get("tenant_id"):
        return dbc.Alert("No tenant selected.", color="warning", dismissable=True)

    tid    = store["tenant_id"]
    domain = store.get("domain", "pharmacy")

    mappings = [
        {
            "canonical_column": id_dict["canonical"],
            "source_column":    (val or "").strip() or id_dict["canonical"],
        }
        for id_dict, val in zip(ids, values)
    ]

    status, data = call_api(
        "POST",
        f"/tenants/{tid}/mappings",
        json_body={"domain_type": domain, "entity": entity, "mappings": mappings},
    )

    if status == 200:
        return dbc.Alert(
            f"Saved {len(mappings)} column mapping{'s' if len(mappings) != 1 else ''}.",
            color="success", dismissable=True,
        )
    detail = data.get("detail", "Error") if isinstance(data, dict) else str(data)
    return dbc.Alert(f"Error: {detail}", color="danger", dismissable=True)


# ══════════════════════════════════════════════════════════════
# ALERTS SETTINGS CALLBACKS  (US-205)
# ══════════════════════════════════════════════════════════════

@app.callback(
    Output("alerts-channels-display", "children"),
    Output("alerts-status-msg",       "children"),
    Input("alerts-add-btn",           "n_clicks"),
    State("alerts-channel-select",    "value"),
    State("alerts-recipient-input",   "value"),
    State("alerts-label-input",       "value"),
    prevent_initial_call=True,
)
def alerts_add_channel(n_clicks, channel, recipient, label):
    """Callback: Add new alert channel for the current tenant."""
    if not n_clicks:
        return dash.no_update, dash.no_update
    try:
        tid = int(current_user.tenant_id) if (
            current_user.is_authenticated and current_user.is_tenant_user()
            and current_user.tenant_id
        ) else (1 if current_user.is_admin() else None)
        if not tid:
            return dash.no_update, dbc.Alert("No tenant context.", color="danger", dismissable=True)
        ok, msg = _alert_add_channel(auth_engine, tid, channel or "", recipient or "", label or "")
        channels = _alert_get_channels(auth_engine, tid)
        status = dbc.Alert(msg, color="success" if ok else "danger", dismissable=True,
                           style={"fontSize": "0.82rem", "padding": "8px 14px"})
        return _render_alert_channel_table(channels), status
    except Exception as exc:
        return dash.no_update, dbc.Alert(str(exc), color="danger", dismissable=True)


@app.callback(
    Output("alerts-channels-display", "children", allow_duplicate=True),
    Output("alerts-status-msg",       "children", allow_duplicate=True),
    Input({"type": "alert-delete-btn", "index": dash.ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def alerts_delete_channel(n_clicks_list):
    """Callback: Delete an alert channel via pattern-match button."""
    if not any(n for n in n_clicks_list if n):
        return dash.no_update, dash.no_update
    try:
        tid = int(current_user.tenant_id) if (
            current_user.is_authenticated and current_user.is_tenant_user()
            and current_user.tenant_id
        ) else (1 if current_user.is_admin() else None)
        if not tid:
            return dash.no_update, dbc.Alert("No tenant context.", color="danger", dismissable=True)
        triggered = ctx.triggered_id
        channel_id = triggered["index"] if isinstance(triggered, dict) else None
        if channel_id is None:
            return dash.no_update, dash.no_update
        ok, msg = _alert_delete_channel(auth_engine, channel_id, tid)
        channels = _alert_get_channels(auth_engine, tid)
        status = dbc.Alert(msg, color="success" if ok else "danger", dismissable=True,
                           style={"fontSize": "0.82rem", "padding": "8px 14px"})
        return _render_alert_channel_table(channels), status
    except Exception as exc:
        return dash.no_update, dbc.Alert(str(exc), color="danger", dismissable=True)


@app.callback(
    Output("alerts-channels-display", "children", allow_duplicate=True),
    Output("alerts-status-msg",       "children", allow_duplicate=True),
    Input({"type": "alert-toggle-btn", "index": dash.ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def alerts_toggle_channel(n_clicks_list):
    """Callback: Pause / resume an alert channel."""
    if not any(n for n in n_clicks_list if n):
        return dash.no_update, dash.no_update
    try:
        tid = int(current_user.tenant_id) if (
            current_user.is_authenticated and current_user.is_tenant_user()
            and current_user.tenant_id
        ) else (1 if current_user.is_admin() else None)
        if not tid:
            return dash.no_update, dbc.Alert("No tenant context.", color="danger", dismissable=True)
        triggered = ctx.triggered_id
        channel_id = triggered["index"] if isinstance(triggered, dict) else None
        if channel_id is None:
            return dash.no_update, dash.no_update
        # Determine current state from channels list then toggle
        channels = _alert_get_channels(auth_engine, tid)
        current_ch = next((c for c in channels if c["id"] == channel_id), None)
        new_active = not current_ch["is_active"] if current_ch else True
        ok, msg = _alert_toggle_channel(auth_engine, channel_id, tid, new_active)
        channels = _alert_get_channels(auth_engine, tid)
        status = dbc.Alert(msg, color="success" if ok else "danger", dismissable=True,
                           style={"fontSize": "0.82rem", "padding": "8px 14px"})
        return _render_alert_channel_table(channels), status
    except Exception as exc:
        return dash.no_update, dbc.Alert(str(exc), color="danger", dismissable=True)


# ══════════════════════════════════════════════════════════════
# BILLING TAB
# ══════════════════════════════════════════════════════════════
def _render_billing_tab() -> html.Div:
    """Render the billing / plan management tab for super-admins."""
    try:
        currency = "INR"
        plans    = BillingEngine(currency).get_plan_display()
    except Exception:
        plans = []

    plan_cards = []
    for plan in plans:
        plan_cards.append(
            html.Div([
                html.Div(plan["name"],
                         style={"fontWeight":700,"fontSize":"0.95rem","color":"#1a1a2e",
                                "marginBottom":"0.4rem"}),
                html.Div(plan["monthly"],
                         style={"fontSize":"1.2rem","fontWeight":700,"color":C_BLUE,
                                "marginBottom":"0.4rem"}),
                html.Ul([html.Li(f, style={"fontSize":"0.74rem","color":"#6b7280"})
                         for f in plan["features"]],
                        style={"paddingLeft":"1rem","margin":"0 0 0.75rem"}),
                dbc.Button(
                    "Upgrade" if plan["plan_id"] != "starter" else "Current",
                    href=f"/billing/checkout?plan={plan['plan_id']}&currency={currency}",
                    color="success" if plan["plan_id"] == "growth" else "outline-success",
                    size="sm", style={"width":"100%","fontWeight":600,"fontSize":"0.8rem"},
                ),
            ], style={"background":"#fff","border":"1.5px solid #e2e8f0","borderRadius":"12px",
                      "padding":"1.2rem","boxShadow":"0 1px 4px rgba(0,0,0,0.05)"})
        )

    return html.Div([
        html.Div([
            html.H4("Billing & Plans", style={"margin":0,"fontWeight":700,"color":C_NAVY}),
            html.Span("Manage your InsightHub subscription",
                      style={"fontSize":"0.78rem","color":"#6b7280"}),
        ], style={"marginBottom":"1.2rem"}),
        html.Div(plan_cards, style={"display":"grid",
                                    "gridTemplateColumns":"repeat(auto-fill,minmax(220px,1fr))",
                                    "gap":"0.75rem","marginBottom":"1.5rem"}),
        html.Div([
            html.Span("💡 ", style={"fontSize":"1rem"}),
            html.Span("Need a custom plan for your chain or franchise? ",
                                   style={"fontSize":"0.82rem","color":"#374151"}),
            html.A("Contact us", href="mailto:sales@insighthub.ai",
                   style={"fontSize":"0.82rem","color":C_BLUE,"fontWeight":600}),
        ], style={"background":"#f0f9ff","borderRadius":"8px","padding":"0.75rem 1rem",
                  "border":"1px solid #bfdbfe"}),
    ])


# ══════════════════════════════════════════════════════════════
# AI CHAT CALLBACKS
# ══════════════════════════════════════════════════════════════
@app.callback(
    Output("ai-chat-messages", "children"),
    Output("ai-chat-history",  "data"),
    Output("ai-chat-input",    "value"),
    Output("ai-agent-trace",   "children"),
    Input("ai-chat-send",      "n_clicks"),
    Input({"type":"ai-suggest-btn","idx":dash.ALL}, "n_clicks"),
    State("ai-chat-input",    "value"),
    State("ai-chat-history",  "data"),
    State("ai-language-select","value"),
    State("ai-kpi-context",   "data"),
    prevent_initial_call=True,
)
def handle_ai_chat(n_send, suggest_clicks, user_input, history, language, kpi_ctx):
    triggered = ctx.triggered_id
    text      = user_input or ""

    if isinstance(triggered, dict) and triggered.get("type") == "ai-suggest-btn":
        try:
            tid_int = int(current_user.id) if current_user.is_authenticated else 0
            dom     = _get_active_domain(tid_int)
            from domain_config import get_domain_config
            suggested = get_domain_config(dom).get("suggested_questions", [
                "How did we perform last month?",
                "Which branch had the highest margin?",
                "Are there any anomalies in our sales data?",
                "What are our top suppliers by purchase value?",
                "What's our average cash vs credit ratio?",
                "Compare this year vs last year sales",
            ])
        except Exception:
            suggested = [
                "How did we perform last month?",
                "Which branch had the highest margin?",
                "Are there any anomalies in our sales data?",
                "What are our top suppliers by purchase value?",
                "What's our average cash vs credit ratio?",
                "Compare this year vs last year sales",
            ]
        idx  = triggered.get("idx", 0)
        text = suggested[idx] if idx < len(suggested) else text

    if not text.strip():
        raise dash.exceptions.PreventUpdate

    try:
        tid = current_user.id if current_user.is_authenticated else None
    except Exception:
        tid = None

    existing_messages = []
    for turn in (history or []):
        if turn["role"] == "user":
            existing_messages.append(render_user_message(turn["content"]))
        else:
            existing_messages.append(render_assistant_message(turn["content"]))
    existing_messages.append(render_user_message(text))

    # ── Load tenant data from DB so AI has real numbers ──────────────────
    try:
        _is_tenant = (current_user.is_authenticated and
                      hasattr(current_user, "is_tenant_user") and
                      current_user.is_tenant_user())
        _tenant_id = int(current_user.tenant_id) if _is_tenant and current_user.tenant_id else None
        # Route this request's LLM calls to the tenant's BYO provider/key if configured.
        try:
            from ai import llm_gateway as _llm
            _llm.set_tenant_context(_tenant_id, engine)
        except Exception:
            pass
        if _tenant_id:
            _s = pd.read_sql_query(
                "SELECT * FROM sales WHERE tenant_id=?",
                engine, params=(_tenant_id,)
            )
            _p = pd.read_sql_query(
                "SELECT * FROM purchases WHERE tenant_id=?",
                engine, params=(_tenant_id,)
            )
        else:
            # Admin / global user — load all data (same as global sales_df)
            _s, _p = load_from_db(engine)
        _s["bill_date"] = pd.to_datetime(_s["bill_date"], errors="coerce")
        if not _p.empty and "grn_date" in _p.columns:
            _p["grn_date"] = pd.to_datetime(_p["grn_date"], errors="coerce")
    except Exception as _de:
        logger.warning("[ai_chat] data load failed: %s", _de)
        _s, _p = load_from_db(engine)   # fallback to all data

    # Detect currency: pharmacy domain = India (INR), all others = USD
    try:
        _ai_domain = getattr(current_user, "tenant_domain", "saas") or "saas"
        _currency  = "INR" if _ai_domain == "pharmacy" else "USD"
    except Exception:
        _currency = "USD"

    # Build KPI snapshot from real data if kpi_ctx is empty
    kpi_data = kpi_ctx or {}
    if not _s.empty:
        try:
            _sales_total = float(_s["net_amount"].sum()) if "net_amount" in _s.columns else 0
            _purch_total = float(_p["net_amount"].sum()) if "net_amount" in _p.columns and not _p.empty else 0
            _margin      = ((_sales_total - _purch_total) / _sales_total * 100) if _sales_total > 0 else 0
            _bills       = int(len(_s))
            _top_branch  = (_s.groupby("branch")["net_amount"].sum().idxmax()
                            if "branch" in _s.columns and "net_amount" in _s.columns else "N/A")
            kpi_data = {
                "sales": _sales_total, "purchases": _purch_total,
                "margin": _margin, "bills": _bills, "top_branch": _top_branch,
                "currency": _currency,
            }
        except Exception:
            pass

    trace_cards = []
    try:
        answer, new_history, trace_cards = rag_answer(
            question=text,
            tenant_id=tid or 0,
            kpi_data=kpi_data,
            history=history or [],
            sales_df=_s,
            purchase_df=_p,
        )
    except Exception as _e:
        answer      = f"AI error: {_e}"
        new_history = (history or []) + [
            {"role": "user",      "content": text},
                  {"role": "assistant", "content": answer},
        ]

    existing_messages.append(render_assistant_message(answer))

    trace_section = []
    if trace_cards:
        trace_section = [
            html.Div("Agent Reasoning Trace", className="sidebar-label",
                     style={"marginTop":"1rem","marginBottom":"0.4rem"}),
            *trace_cards,
        ]

    return existing_messages, new_history, "", trace_section


@app.callback(
    Output("ai-chat-history",  "data",     allow_duplicate=True),
    Output("ai-chat-messages", "children", allow_duplicate=True),
    Input("ai-chat-clear",     "n_clicks"),
    prevent_initial_call=True,
)
def clear_ai_chat(_n):
    from ai.rag import _system_message
    try:
        tname = (current_user.tenant_name if
                 current_user.is_authenticated and current_user.is_tenant_user()
                 else "InsightHub")
    except Exception:
        tname = "InsightHub"
    return [], [_system_message(
        f"Hello! I'm your InsightHub AI assistant for {tname}. "
        "Ask me anything about your sales, purchases, margins, or inventory."
    )]


@app.callback(
    Output("ai-anomaly-results", "children"),
    Input("ai-anomaly-btn",      "n_clicks"),
    prevent_initial_call=True,
)
def run_anomaly_detection(_n):
    try:
        _is_t = (current_user.is_authenticated and
                 hasattr(current_user, "is_tenant_user") and
                 current_user.is_tenant_user())
        tname = getattr(current_user, "tenant_name", "InsightHub") if _is_t else "InsightHub"
        _tid  = int(current_user.tenant_id) if _is_t and current_user.tenant_id else None
        if _tid:
            _anom_s = pd.read_sql_query(
                "SELECT * FROM sales WHERE tenant_id=?", engine, params=(_tid,)
            )
        else:
            _anom_s = sales_df
        anomalies = get_anomaly_report(_anom_s, tenant_id=_tid or 0, tenant_name=tname)
        return render_anomaly_results(anomalies)
    except Exception as _ae:
        logger.warning("[anomaly] detection failed: %s", _ae)
        return html.Div()


@app.callback(
    Output("fix-duplicates-feedback",  "children"),
    Output("upload-history-container", "children", allow_duplicate=True),
    Input("fix-duplicates-btn", "n_clicks"),
    prevent_initial_call=True,
)
def handle_fix_duplicates(n_clicks):
    if not n_clicks:
        raise dash.exceptions.PreventUpdate
    try:
        from data_loader import cleanup_duplicate_uploads, get_upload_history
        _tid = None
        try:
            if current_user.is_authenticated and current_user.is_tenant_user():
                _tid = int(current_user.tenant_id)
        except Exception:
            pass
        del_rows, del_hist, err = cleanup_duplicate_uploads(auth_engine, tenant_id=_tid)
        if err:
            return dbc.Alert(f"Cleanup error: {err}", color="danger", dismissable=True), dash.no_update
        # Rebuild history table with fresh data
        hist = get_upload_history(auth_engine, tenant_id=_tid)
        hcols = [{"name":"File","id":"filename"},{"name":"Type","id":"report_type"},
                 {"name":"Branch","id":"branch"},{"name":"Month","id":"month_label"},
                 {"name":"Rows","id":"row_count"},{"name":"Uploaded At","id":"uploaded_at"},
                 {"name":"Duplicate?","id":"duplicate_warning"}]
        new_tbl = dash_table.DataTable(
            id="upload-history-table", columns=hcols,
            data=hist.to_dict("records") if not hist.empty else [],
            page_size=10, style_table={"overflowX":"auto"},
            style_cell={"fontSize":"0.78rem","padding":"6px 10px","textAlign":"left"},
            style_header={"backgroundColor":"#EFF6FF","fontWeight":"bold","color":C_NAVY},
            style_data_conditional=[{"if":{"filter_query":"{duplicate_warning} = 1"},
                "backgroundColor":"#fff3cd","color":"#856404"}],
        ) if not hist.empty else html.Div("No uploads yet.", style={"color":"#888","fontSize":"0.85rem"})
        if del_hist == 0:
            msg = dbc.Alert("\u2705 No duplicates found \u2014 your data is already clean.",
                            color="success", dismissable=True)
        else:
            msg = dbc.Alert(
                [html.Strong("\u2705 Done! "),
                 html.Span(f"Removed {del_rows:,} duplicate data rows across "
                           f"{del_hist} superseded upload(s).")],
                color="success", dismissable=True,
            )
        return msg, new_tbl
    except Exception as _e:
        return dbc.Alert(f"Cleanup error: {_e}", color="danger", dismissable=True), dash.no_update


@app.callback(
    Output("rollback-feedback", "children"),
    Input({"type":"rollback-btn","uid":dash.ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def handle_rollback(n_clicks_list):
    triggered = ctx.triggered_id
    if not triggered or not any(n for n in n_clicks_list if n):
        raise dash.exceptions.PreventUpdate
    uid = triggered.get("uid", "")
    try:
        from data_loader import rollback_upload
        msg = rollback_upload(auth_engine, uid)
    except Exception as _e:
        msg = f"Rollback error: {_e}"
    return html.Div(msg, style={"color":"#1e7e4b","fontSize":"0.82rem","marginTop":"6px"})

# ── Sidebar adaptive labels callback ─────────────────────────
# filter-date start/end are also written by apply_quick_select — allow_duplicate=True
# lets both callbacks coexist: this one sets initial range from data, quick-select overrides.
@app.callback(
    Output("sidebar-filter-label",      "children"),
    Output("sidebar-logo-icon",         "children"),
    Output("sidebar-data-status-label", "style"),
    Output("sidebar-sources-label",     "style"),
    Output("filter-date", "start_date", allow_duplicate=True),
    Output("filter-date", "end_date",   allow_duplicate=True),
    Input("data-version", "data"),
    prevent_initial_call="initial_duplicate",
)
def update_sidebar_labels(_v):
    _hide = {"display": "none"}
    _show = {}
    try:
        is_tenant = current_user.is_authenticated and current_user.is_tenant_user()
    except Exception:
        is_tenant = False

    if not is_tenant:
        # Admin/MedStar: pharmacy icon, Branch label, full sidebar
        dm, dx = _data_date_bounds()
        return "Branch", "🏪", _show, _show, str(dm), str(dx)

    # Tenant user: detect domain to pick right filter label
    try:
        _tid = int(current_user.tenant_id) if current_user.tenant_id else None
        _domain = _get_active_domain(_tid) if _tid else "generic"
    except Exception:
        _domain = "generic"

    _label_map = {
        "pharmacy":             "Branch",
        "retail":               "Location",
        "fnb":                  "Location",
        "manufacturing":        "Plant",
        "finance":              "Entity",
        "professional_services":"Client",
        "generic":              "Filter",
    }
    _icon_map = {
        "pharmacy":             "💊",
        "retail":               "🛍️",
        "fnb":                  "🍽️",
        "manufacturing":        "🏭",
        "finance":              "💼",
        "professional_services":"💼",
        "generic":              "📊",
    }
    filter_label = _label_map.get(_domain, "Filter")
    domain_icon  = _icon_map.get(_domain, "📊")

    # Get actual date range from tenant's data
    try:
        _all_s, _all_p = load_tenant_df(engine, _tid)
        _dates = []
        for _df in (_all_s, _all_p):
            if not _df.empty and "bill_date" in _df.columns:
                _valid = pd.to_datetime(_df["bill_date"], errors="coerce").dropna()
                if not _valid.empty:
                    _dates += [_valid.min(), _valid.max()]
        if _dates:
            _start = min(_dates).date()
            _end   = max(_dates).date()
        else:
            _today = date.today()
            _start = date(_today.year, 1, 1)
            _end   = _today
    except Exception:
        _today = date.today()
        _start = date(_today.year, 1, 1)
        _end   = _today

    # Hide Data Status / Sources for non-pharmacy tenants
    _ds_style  = _show if _domain == "pharmacy" else _hide
    _src_style = _show if _domain == "pharmacy" else _hide

    return filter_label, domain_icon, _ds_style, _src_style, str(_start), str(_end)


# ── Temporary debug endpoint (remove after diagnosis) ─────────
@app.server.route("/debug/fix-tenant-data")
def debug_fix_tenant_data():
    """One-time fix: tag sales/purchases rows with correct tenant_id and upload_id."""
    from flask import jsonify
    from sqlalchemy import text as _text
    try:
        with engine.connect() as conn:
            # Find all active uploads and patch the sales/purchases rows
            uploads = conn.execute(_text(
                "SELECT id, tenant_id, report_type FROM upload_history WHERE status='active'"
            )).fetchall()
            results = []
            for uid, tid, rtype in uploads:
                table = "purchases" if rtype in ("purchase","generic_purchases") else "sales"
                # Patch rows that have NULL tenant_id/upload_id
                r1 = conn.execute(_text(
                    f"UPDATE {table} SET tenant_id=:tid, upload_id=:uid "
                    f"WHERE (tenant_id IS NULL OR upload_id IS NULL)"
                ), {"tid": tid, "uid": uid})
                results.append({"upload_id": uid, "tenant_id": tid, "table": table,
                                "rows_patched": r1.rowcount})
            conn.commit()
            # Verify
            verify = [dict(tenant_id=r[0], upload_id=r[1], cnt=r[2])
                      for r in conn.execute(_text(
                "SELECT tenant_id, upload_id, COUNT(*) FROM sales GROUP BY tenant_id, upload_id"
            )).fetchall()]
        return jsonify({"patched": results, "sales_dist_after": verify})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.server.route("/debug/tenant")
def debug_tenant():
    from flask import jsonify
    from sqlalchemy import text as _text
    out = {}
    # engine — local_tenants table
    try:
        with engine.connect() as conn:
            lt_cols = [c[1] for c in conn.execute(_text("PRAGMA table_info(local_tenants)")).fetchall()]
            out["local_tenants_cols"] = lt_cols
            out["local_tenants"] = [dict(zip(lt_cols, r))
                                    for r in conn.execute(_text("SELECT * FROM local_tenants")).fetchall()]
    except Exception as e:
        out["local_tenants_error"] = str(e)
    # engine — sales / purchases / upload_history
    try:
        with engine.connect() as conn:
            out["data_db_path"] = str(engine.url)
            out["data_tables"] = [r[0] for r in conn.execute(
                _text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()]
            out["uploads"] = [dict(id=r[0], tenant_id=r[1], status=r[2], rtype=r[3])
                              for r in conn.execute(_text(
                "SELECT id, tenant_id, status, report_type FROM upload_history ORDER BY id"
            )).fetchall()]
            out["sales_dist"] = [dict(tenant_id=r[0], upload_id=r[1], cnt=r[2])
                                 for r in conn.execute(_text(
                "SELECT tenant_id, upload_id, COUNT(*) FROM sales GROUP BY tenant_id, upload_id"
            )).fetchall()]
            sales_cols = [c[1] for c in conn.execute(_text("PRAGMA table_info(sales)")).fetchall()]
            out["sales_cols"] = sales_cols
            out["sales_sample"] = [dict(zip(sales_cols, r))
                                   for r in conn.execute(_text("SELECT * FROM sales LIMIT 2")).fetchall()]
    except Exception as e:
        out["data_engine_error"] = str(e)
    return jsonify(out)


@app.server.route("/debug/set-currency/<int:tenant_id>/<currency>")
def debug_set_currency(tenant_id, currency):
    from flask import jsonify
    from sqlalchemy import text as _text
    allowed = {"USD","INR","EUR","GBP","CAD","AUD","SGD"}
    if currency.upper() not in allowed:
        return jsonify({"error": f"Currency must be one of {allowed}"}), 400
    try:
        with engine.connect() as conn:
            conn.execute(_text(
                "UPDATE local_tenants SET currency=:cur, country=:ctr WHERE id=:tid"
            ), {"cur": currency.upper(),
                "ctr": "US" if currency.upper()=="USD" else "IN",
                "tid": tenant_id})
            conn.commit()
            row = conn.execute(_text(
                "SELECT id, name, currency, country FROM local_tenants WHERE id=:tid"),
                {"tid": tenant_id}).fetchone()
        return jsonify({"updated": dict(zip(["id","name","currency","country"], row)) if row else None})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Weekly email report routes ────────────────────────────────
_send_report = None
try:
    from email_reports import send_report_now as _send_report, start_scheduler as _start_email_scheduler
    _start_email_scheduler(engine)
    print("[app] Weekly email report scheduler started")
except Exception as _email_err:
    print(f"[app] Email report init warning: {_email_err}")


@app.server.route("/report/preview/<int:tenant_id>")
def report_preview(tenant_id):
    """Preview the weekly HTML email report in-browser."""
    if _send_report is None:
        return "<p>Email reports module not loaded.</p>", 503
    result = _send_report(engine, tenant_id=tenant_id)
    if result.get("preview_html"):
        from flask import make_response
        r = make_response(result["preview_html"])
        r.headers["Content-Type"] = "text/html; charset=utf-8"
        return r
    return f"<p>Error: {result.get('reason', 'no data')}</p>", 400


@app.server.route("/report/send/<int:tenant_id>")
def report_send(tenant_id):
    from flask import jsonify, request as _freq
    if _send_report is None:
        return jsonify({"error": "email_reports not loaded"}), 503
    to_email = _freq.args.get("email")
    result = _send_report(engine, tenant_id=tenant_id, to_email=to_email)
    return jsonify(result)


# Stripe billing routes
try:
    from stripe_billing import register_stripe_routes
    register_stripe_routes(app.server, engine)
except Exception as _stripe_err:
    print(f"[app] Stripe init warning: {_stripe_err}")

# Self-serve signup routes
try:
    from signup import register_signup_routes
    import os as _os2
    _app_base_url = _os2.environ.get("APP_BASE_URL", "http://localhost:8050")
    register_signup_routes(app.server, engine, base_url=_app_base_url)
except Exception as _signup_err:
    print(f"[app] Signup init warning: {_signup_err}")


# Marketing landing page
try:
    from landing_page import render_landing as _render_landing

    @app.server.route("/landing")
    def landing_page():
        from flask import make_response
        r = make_response(_render_landing())
        r.headers["Content-Type"] = "text/html; charset=utf-8"
        return r

    @app.server.route("/")
    def root_redirect():
        from flask import redirect
        return redirect("/landing")

except Exception as _landing_err:
    print(f"[app] Landing page init warning: {_landing_err}")


# ── AI Settings: save / test / reset BYO LLM config ───────────────────────────
@app.callback(
    Output("llm-status-msg",      "children"),
    Output("llm-current-display", "children"),
    Input("llm-save-btn",  "n_clicks"),
    Input("llm-test-btn",  "n_clicks"),
    Input("llm-clear-btn", "n_clicks"),
    State("llm-provider-select", "value"),
    State("llm-model-input",     "value"),
    State("llm-apikey-input",    "value"),
    State("llm-baseurl-input",   "value"),
    prevent_initial_call=True,
)
def manage_llm_config(_save, _test, _clear, provider, model, api_key, base_url):
    from llm_settings import render_current_config
    from ai import llm_gateway as _g
    trig = dash.callback_context.triggered[0]["prop_id"].split(".")[0] if dash.callback_context.triggered else ""
    try:
        _tid = int(current_user.tenant_id) if (
            current_user.is_authenticated and current_user.is_tenant_user()
            and current_user.tenant_id) else (1 if (current_user.is_authenticated
            and getattr(current_user, "is_admin", lambda: False)()) else None)
    except Exception:
        _tid = None
    if not _tid:
        return dbc.Alert("No tenant context.", color="danger", dismissable=True), no_update

    def _ok(msg, color="success"):
        return dbc.Alert(msg, color=color, dismissable=True), render_current_config(_tid, engine)

    if trig == "llm-clear-btn":
        try:
            _g.clear_tenant_llm(engine, _tid)
            return _ok("Reset to the platform default model (Groq / Llama).", "warning")
        except Exception as e:
            return dbc.Alert(f"Reset failed: {e}", color="danger", dismissable=True), no_update

    if trig == "llm-test-btn":
        # test the currently saved config (or the platform default)
        try:
            _g.set_tenant_context(_tid, engine)
            h = _g.health(tenant_id=_tid, engine=engine)
            _g.clear_tenant_context()
            if h.get("ok"):
                return dbc.Alert(f"✅ Connected to {h['provider']} ({h['model']}). "
                                 f"Response: {h.get('detail','')}", color="success",
                                 dismissable=True), no_update
            return dbc.Alert(f"⚠️ Could not reach {h.get('provider')} "
                             f"({h.get('detail','no response')}). Check the key/model, then save and retry.",
                             color="warning", dismissable=True), no_update
        except Exception as e:
            return dbc.Alert(f"Test failed: {e}", color="danger", dismissable=True), no_update

    # save
    if trig == "llm-save-btn":
        if not provider:
            return dbc.Alert("Choose a provider.", color="danger", dismissable=True), no_update
        if not api_key:
            return dbc.Alert("Enter an API key (or use Reset to return to the default model).",
                             color="danger", dismissable=True), no_update
        try:
            _g.set_tenant_llm(engine, _tid, provider, api_key.strip(),
                              model=(model or "").strip() or None,
                              base_url=(base_url or "").strip() or None)
            return _ok(f"Saved. Your AI now uses {provider}"
                       f"{f' ({model})' if model else ''}. Click Test connection to verify.")
        except Exception as e:
            return dbc.Alert(f"Save failed: {e}", color="danger", dismissable=True), no_update

    return no_update, no_update


# Expose Flask server for gunicorn
server = app.server

if __name__ == "__main__":
    import os as _os
    debug = _os.environ.get("DASH_DEBUG", "true").lower() == "true"
    port  = int(_os.environ.get("PORT", 8050))
    print(f"[app] Starting InsightHub on http://127.0.0.1:{port}  (debug={debug})")
    app.run(debug=debug, host="0.0.0.0", port=port)
