"""
app.py - MedStar Pharmacy Analytics Dashboard  (Day 3: Auth + RBAC)
Tabs: Overview | Sales | Purchases | Branch Compare | Upload (admin) | Users (admin)
Run:  python app.py   then open  http://127.0.0.1:8050
"""

import io
import threading
from io import BytesIO
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
    build_preview, append_upload_to_db, load_from_db
)
from pdf_report  import generate_pdf
from auth        import init_auth, authenticate, list_users, create_user, \
                        update_user_role, deactivate_user, reactivate_user, reset_password
from login_page     import render_login
from tenant_portal  import render_tenants_tab, call_api, MODULE_LABELS, API_BASE

# ── Load pharmacy data ────────────────────────────────────────
sales_df, purchase_df, engine = get_data()

# ── Colours ───────────────────────────────────────────────────
C_GREEN  = "#1e7e4b"
C_BLUE   = "#0d6efd"
C_ORANGE = "#fd7e14"
C_PURPLE = "#6f42c1"
C_TEAL   = "#0dcaf0"
_BRANCH_PALETTE = [C_GREEN, C_BLUE, C_ORANGE, C_PURPLE, C_TEAL]

def get_branch_color_map():
    branches = sorted(set(
        (sales_df["branch"].unique().tolist()    if not sales_df.empty    else []) +
        (purchase_df["branch"].unique().tolist() if not purchase_df.empty else [])
    ))
    return {b: _BRANCH_PALETTE[i % len(_BRANCH_PALETTE)] for i, b in enumerate(branches)}

CHART_LAYOUT = dict(
    plot_bgcolor="white", paper_bgcolor="white",
    font=dict(family="Segoe UI, system-ui", size=11, color="#333"),
    margin=dict(l=10, r=10, t=30, b=10),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
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

def chart_card(title, figure, height=None):
    style = {"height":"{}px".format(height)} if height else {}
    return html.Div([
        html.Div(title, className="chart-card-title"),
        dcc.Graph(figure=figure, config={"displayModeBar":False}, style=style),
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
        ], style={"display":"grid","gridTemplateColumns":"1fr 1fr","gap":"4px",
                  "marginBottom":"4px"}),
        dbc.Button("\U0001f4c4 PDF Report", id="btn-dl-pdf", color="dark", size="sm",
                   style={"width":"100%","fontSize":"0.71rem","fontWeight":600}),
    ])

# ── Sidebar ───────────────────────────────────────────────────
def make_sidebar():
    branches = get_filter_options()
    data_min, data_max = _data_date_bounds()
    return html.Div([
        html.Div("\U0001f3ea", style={"fontSize":"2rem","textAlign":"center","marginBottom":"0.4rem"}),
        html.Div("InsightHub", style={"fontWeight":700,"textAlign":"center",
                                      "fontSize":"1rem","color":C_GREEN}),
        html.Div("Analytics",  style={"fontWeight":600,"textAlign":"center",
                                      "fontSize":"0.75rem","color":"#6b7c6b",
                                      "marginBottom":"1rem"}),
        html.Div(className="s-divider"),
        html.Div("Branch", className="sidebar-label"),
        dcc.Dropdown(id="filter-branch",
                     options=[{"label":b,"value":b} for b in branches],
                     value="All", clearable=False,
                     style={"fontSize":"0.83rem","marginBottom":"0.75rem"}),
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
        html.Div("Data Status", className="sidebar-label"),
        html.Div(id="sidebar-data-status"),
        html.Div(className="s-divider"),
        html.Div("Sources", className="sidebar-label"),
        html.Div(id="sidebar-sources"),
    ], className="sidebar", style={"width":"210px","minWidth":"210px"})

# ── Navbar ────────────────────────────────────────────────────
def make_navbar():
    return dbc.Navbar(
        dbc.Container([
            html.Div([
                html.Span("\U0001f3e5 "),
                html.Span("MedStar Pharmacy",
                          style={"fontWeight":700,"fontSize":"1.15rem"}),
                html.Span("  Multi-Branch Analytics",
                          style={"fontSize":"0.78rem","opacity":"0.8","marginLeft":"0.5rem"}),
            ]),
            html.Div([
                html.Span(id="navbar-period-label",
                          style={"fontSize":"0.72rem","opacity":"0.8","marginRight":"1.2rem"}),
                html.Div(id="navbar-user-info",
                         style={"display":"flex","alignItems":"center","gap":"8px"}),
            ], style={"display":"flex","alignItems":"center","marginLeft":"auto"}),
        ], fluid=True),
        color=C_GREEN, dark=True,
        style={"padding":"0 1.25rem","height":"62px"},
    )

# ── Dash app ──────────────────────────────────────────────────
app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP],
    suppress_callback_exceptions=True,
    title="MedStar Analytics",
)

# ── Init auth (must happen after app is created) ──────────────
from data_loader import DB_PATH
auth_engine = init_auth(app.server, DB_PATH)
import os as _os
app.server.secret_key = _os.environ.get("FLASK_SECRET_KEY", "insighthub-secret-change-in-prod-2026")

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
                dcc.Tab(label="Overview",       value="overview",
                        className="custom-tab", selected_className="custom-tab--selected"),
                dcc.Tab(label="Sales",          value="sales",
                        className="custom-tab", selected_className="custom-tab--selected"),
                dcc.Tab(label="Purchases",      value="purchases",
                        className="custom-tab", selected_className="custom-tab--selected"),
                dcc.Tab(label="Branch Compare", value="compare",
                        className="custom-tab", selected_className="custom-tab--selected"),
                dcc.Tab(label="Upload Data",    value="upload",
                        id="tab-upload",
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
            ]),
            html.Div(id="tab-content", style={"padding":"1rem 0.5rem"}),
        ], style={"flex":1,"padding":"0.5rem 1rem","overflowY":"auto"}),
    ], style={"display":"flex","height":"calc(100vh - 62px)","overflow":"hidden"}),
])

# ── Navbar user info callback ─────────────────────────────────
_TAB_SHOW = {}               # visible
_TAB_HIDE = {"display":"none"}  # hidden

@app.callback(
    Output("navbar-user-info", "children"),
    Output("tab-upload",       "style"),
    Output("tab-users",        "style"),
    Output("tab-tenants",      "style"),
    Input("data-version",      "data"),
)
def update_navbar_user(_v):
    try:
        u           = current_user
        authed      = u.is_authenticated
        is_admin    = authed and u.is_admin()
        is_tenant   = authed and u.is_tenant_user()
        display     = u.display_name if authed else "Guest"
        role        = u.role_label   if authed else ""
        role_col    = u.role_color   if authed else C_BLUE
        tenant_name = u.tenant_name  if (authed and is_tenant) else None
    except Exception:
        is_admin  = False
        is_tenant = False
        display   = "Guest"
        role      = ""
        role_col  = C_BLUE
        tenant_name = None

    # Subtitle for tenant users
    subtitle = []
    if tenant_name:
        subtitle = [html.Span(f"│ {tenant_name}",
                              style={"fontSize":"0.72rem","color":"rgba(255,255,255,0.65)",
                                     "marginLeft":"4px"})]

    user_info = html.Div([
        html.Span(display, style={"fontSize":"0.8rem","fontWeight":600,"color":"white"}),
        *subtitle,
        html.Span(role,
                  style={"fontSize":"0.65rem","fontWeight":700,"padding":"2px 7px",
                         "borderRadius":"20px","background":"rgba(255,255,255,0.2)",
                         "color":"white"}),
        html.A("Sign Out", href="/logout",
               style={"fontSize":"0.72rem","color":"rgba(255,255,255,0.8)",
                      "textDecoration":"none","borderLeft":"1px solid rgba(255,255,255,0.3)",
                      "paddingLeft":"10px","marginLeft":"2px"}),
    ], style={"display":"flex","alignItems":"center","gap":"8px"})

    # Tab visibility rules:
    # Upload Data → MedStar internal admin only
    # Users       → any admin (including tenant admin)
    # Tenants     → MedStar internal admin only
    upload_style  = _TAB_SHOW if (is_admin and not is_tenant) else _TAB_HIDE
    users_style   = _TAB_SHOW if is_admin else _TAB_HIDE
    tenants_style = _TAB_SHOW if (is_admin and not is_tenant) else _TAB_HIDE

    return user_info, upload_style, users_style, tenants_style

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
                    html.Span("🔒", style={"fontSize":"1.4rem"}),
                    html.P("Analytics data is restricted to MedStar internal users.",
                           style={"fontSize":"0.8rem","color":"#94a3b8","margin":"4px 0 0"}),
                ], style={"textAlign":"center","padding":"1rem",
                          "background":"#f8fafc","borderRadius":"12px",
                          "border":"1px solid #e2e8f0","maxWidth":"320px","margin":"0 auto"}),
            ]),
        ], style={"textAlign":"center","padding":"4rem 2rem"}),
    ], style={"minHeight":"60vh","display":"flex","alignItems":"center","justifyContent":"center"})


# ── Tab router ────────────────────────────────────────────────
@app.callback(
    Output("tab-content",         "children"),
    Output("filter-branch",       "options"),
    Output("sidebar-sources",     "children"),
    Output("sidebar-data-status", "children"),
    Output("navbar-period-label", "children"),
    Input("main-tabs",    "value"),
    Input("filter-branch","value"),
    Input("filter-date",  "start_date"),
    Input("filter-date",  "end_date"),
    Input("data-version", "data"),
)
def render_tab(tab, branch, start_date, end_date, _version):
    # Role guard — redirect viewers away from admin-only tabs
    try:
        is_admin    = current_user.is_authenticated and current_user.is_admin()
        is_tenant   = current_user.is_authenticated and current_user.is_tenant_user()
        tenant_name = current_user.tenant_name if is_tenant else None
    except Exception:
        is_admin    = False
        is_tenant   = False
        tenant_name = None

    if tab in ("upload","users") and not is_admin:
        tab = "overview"

    # ── Tenant data fence ────────────────────────────────────────
    # External tenant users (e.g. Right Pharmacy) must never see MedStar's
    # internal sales/purchase analytics. Show them a dedicated welcome page.
    _analytics_tabs = ("overview", "sales", "purchases", "compare", "upload")
    if is_tenant and tab in _analytics_tabs:
        welcome = _render_tenant_welcome(tenant_name)
        # Return early with safe empty values for sidebar/navbar outputs
        branches = get_filter_options()
        b_opts   = [{"label":b,"value":b} for b in branches]
        return welcome, b_opts, [], html.Div(), ""

    branches = get_filter_options()
    b_opts   = [{"label":b,"value":b} for b in branches]
    BCM      = get_branch_color_map()

    all_b = sorted(set(
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

    if   tab == "overview":   content = render_overview(branch, start_date, end_date)
    elif tab == "sales":      content = render_sales(branch, start_date, end_date)
    elif tab == "purchases":  content = render_purchases(branch, start_date, end_date)
    elif tab == "compare":    content = render_compare(start_date, end_date)
    elif tab == "upload":     content = render_upload_tab()
    elif tab == "users":      content = render_users_tab()
    elif tab == "tenants":    content = render_tenants_tab()
    else:                     content = html.Div()

    return content, b_opts, sources, status, plabel

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
            hole=0.55, marker_colors=[C_GREEN,C_ORANGE]))
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

    return html.Div([
        html.Div([html.Span(className="accent"),html.Span("Overview"),
                  html.Span(badge_text,className="period-badge")], className="section-heading"),
        render_alert_banners(check_alerts(s, p)),
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
        hole=0.5,marker_colors=[C_GREEN,C_ORANGE,C_BLUE],textinfo="label+percent"))
    fig_pay.update_layout(**CHART_LAYOUT, title="Payment Mode")

    s = s.copy(); s["_month"] = s["bill_date"].dt.to_period("M").astype(str)
    pm = s.groupby(["_month","branch"])[["pharma_sales","non_pharma_sales"]].sum().reset_index()
    pm = pm.melt(id_vars=["_month","branch"],value_vars=["pharma_sales","non_pharma_sales"],
                 var_name="cat",value_name="amount")
    pm["cat"] = pm["cat"].map({"pharma_sales":"Pharma","non_pharma_sales":"Non-Pharma"})
    fig_ph = px.bar(pm,x="_month",y="amount",color="cat",barmode="group",
        color_discrete_map={"Pharma":C_GREEN,"Non-Pharma":C_ORANGE},text_auto=".2s",
        labels={"_month":"Month","amount":"Sales (Rs.)","cat":"Category"},title="Pharma vs Non-Pharma")
    fig_ph.update_layout(**CHART_LAYOUT)

    ret = s[s["cash_return"] > 0].sort_values("bill_date") if "cash_return" in s.columns else pd.DataFrame()
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
        color="net_amount",color_continuous_scale=[[0,"#c8e6c9"],[1,C_GREEN]],
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
        hole=0.5,marker_colors=[C_GREEN,C_BLUE,C_ORANGE],textinfo="label+percent"))
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
            color_discrete_sequence=[C_GREEN,"#a8d5a8","#ffd59e","#c9b8f5"],text_auto=".2s",
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
                                 "Shared":C_TEAL,"Only {}".format(b2):BCM.get(b2,C_BLUE)},
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
    hist = get_upload_history(engine)
    hist_cols = [{"name":"File","id":"filename"},{"name":"Type","id":"report_type"},
                 {"name":"Branch","id":"branch"},{"name":"Month","id":"month_label"},
                 {"name":"Rows","id":"row_count"},{"name":"Uploaded At","id":"uploaded_at"},
                 {"name":"Duplicate?","id":"duplicate_warning"}]
    hist_data = hist.to_dict("records") if not hist.empty else []
    return html.Div([
        html.Div([html.Span(className="accent"),html.Span("Upload New Data")],className="section-heading"),
        html.Div(["Upload Sales or Purchase reports from POS. App auto-detects report type."],className="info-banner"),
        html.Div(className="chart-card",children=[
            html.Div("Step 1 -- Drop your Excel file",className="chart-card-title"),
            dcc.Upload(id="upload-file",accept=".xlsx,.xls",multiple=False,
                children=html.Div([html.Div("\U0001f4c2",style={"fontSize":"2.5rem","marginBottom":"0.3rem"}),
                    html.Div("Drag and Drop or Click to Browse",style={"fontWeight":600}),
                    html.Div("Supports: .xlsx  .xls",style={"fontSize":"0.72rem","color":"#aaa","marginTop":"0.4rem"})]),
                className="upload-area"),
            html.Div(id="upload-detect-result",style={"marginTop":"0.8rem"}),
        ]),
        html.Div(id="upload-config-card",style={"display":"none"},children=[
            html.Div(className="chart-card",children=[
                html.Div("Step 2 -- Confirm details and Load",className="chart-card-title"),
                dbc.Row([
                    dbc.Col([html.Div("Detected Type",className="sidebar-label"),html.Div(id="upload-type-badge")],md=3),
                    dbc.Col([html.Div("Branch Name",className="sidebar-label"),
                             dbc.Input(id="upload-branch",placeholder="e.g. Keelkattalai",debounce=True,style={"fontSize":"0.85rem"})],md=3),
                    dbc.Col([html.Div("Month and Year",className="sidebar-label"),
                             dbc.Input(id="upload-month",placeholder="e.g. Apr 2026",debounce=True,style={"fontSize":"0.85rem"})],md=3),
                    dbc.Col([html.Div(" ",className="sidebar-label"),
                             dbc.Button("Load into Dashboard",id="upload-confirm-btn",color="success",className="w-100",disabled=True,style={"fontWeight":600})],md=3),
                ],className="g-3 mb-3"),
                html.Div(id="upload-preview-container"),
            ]),
        ]),
        html.Div(id="upload-status-msg",style={"marginBottom":"0.8rem"}),
        html.Div(className="chart-card",children=[
            html.Div("Upload History",className="chart-card-title"),
            html.Div(id="upload-history-container",children=[
                dash_table.DataTable(id="upload-history-table",columns=hist_cols,data=hist_data,
                    page_size=10,style_table={"overflowX":"auto"},
                    style_cell={"fontSize":"0.78rem","padding":"6px 10px","textAlign":"left"},
                    style_header={"backgroundColor":"#f0f8f4","fontWeight":"bold","color":C_GREEN},
                    style_data_conditional=[{"if":{"filter_query":"{duplicate_warning} = 1"},
                        "backgroundColor":"#fff3cd","color":"#856404"}]
                ) if hist_data else html.Div("No uploads yet.",style={"color":"#888","fontSize":"0.85rem"}),
            ]),
        ]),
    ])

# ══════════════════════════════════════════════════════════════
# TAB 6 — USER MANAGEMENT  (Admin only)
# ══════════════════════════════════════════════════════════════
def render_users_tab():
    users = list_users()
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

    # Fetch tenants from FastAPI for the tenant dropdown
    _, tenants_resp = call_api("GET", "/tenants")
    tenant_options = [{"label": "— None —", "value": ""}]
    if isinstance(tenants_resp, list):
        tenant_options += [
            {"label": t.get("name", ""), "value": f"{t.get('id','')}|{t.get('name','')}"}
            for t in tenants_resp
        ]

    return html.Div([
        html.Div([html.Span(className="accent"),html.Span("User Management")],className="section-heading"),
        html.Div("\U0001f6e1️  Only Admins can access this page. Viewer accounts cannot upload data or manage users.",
                 className="info-banner"),

        # Existing users table
        html.Div(className="chart-card",children=[
            html.Div("Active Users",className="chart-card-title"),
            dash_table.DataTable(
                id="users-table",
                columns=user_cols, data=user_rows,
                page_size=10,
                style_table={"overflowX":"auto"},
                style_cell={"fontSize":"0.82rem","padding":"7px 12px","textAlign":"left"},
                style_header={"backgroundColor":"#f0f8f4","fontWeight":"bold","color":C_GREEN},
                style_data_conditional=[
                    {"if":{"filter_query":"{role} = admin"},
                     "fontWeight":"600","color":C_GREEN},
                    {"if":{"filter_query":'{active} = "No"'},
                     "color":"#94a3b8","fontStyle":"italic"},
                ],
                row_selectable="single",
                selected_rows=[],
            ),
            html.Div(id="user-action-result",style={"marginTop":"0.6rem"}),
            html.Hr(style={"borderColor":"#e2e8f0","margin":"0.8rem 0"}),
            html.Div("Selected user actions:", className="sidebar-label",
                     style={"marginBottom":"0.4rem"}),
            dbc.Row([
                dbc.Col(dbc.Button("Toggle Role (Admin⇔Viewer)",id="btn-toggle-role",
                                   color="warning",size="sm",outline=True), md=3),
                dbc.Col(dbc.Button("Deactivate User",id="btn-deactivate",
                                   color="danger",size="sm",outline=True), md=2),
                dbc.Col(dbc.Button("Reactivate User",id="btn-reactivate",
                                   color="success",size="sm",outline=True), md=2),
            ],className="g-2"),
        ]),

        # Add new user
        html.Div(className="chart-card",style={"marginTop":"1rem"},children=[
            html.Div("Add New User",className="chart-card-title"),
            dbc.Row([
                dbc.Col([html.Div("Username",className="sidebar-label"),
                         dbc.Input(id="new-username",placeholder="username",debounce=True,style={"fontSize":"0.85rem"})],md=2),
                dbc.Col([html.Div("Display Name",className="sidebar-label"),
                         dbc.Input(id="new-display",placeholder="Full Name",debounce=True,style={"fontSize":"0.85rem"})],md=2),
                dbc.Col([html.Div("Password",className="sidebar-label"),
                         dbc.Input(id="new-password",placeholder="password",type="password",debounce=True,style={"fontSize":"0.85rem"})],md=2),
                dbc.Col([html.Div("Role",className="sidebar-label"),
                         dcc.Dropdown(id="new-role",
                             options=[{"label":"Admin","value":"admin"},
                                      {"label":"Viewer","value":"viewer"}],
                             value="viewer",clearable=False,style={"fontSize":"0.85rem"})],md=2),
                dbc.Col([html.Div("Assign Tenant",className="sidebar-label"),
                         dcc.Dropdown(id="new-tenant",
                             options=tenant_options,
                             value="",clearable=True,
                             placeholder="— None —",
                             style={"fontSize":"0.85rem"})],md=2),
                dbc.Col([html.Div(" ",className="sidebar-label"),
                         dbc.Button("Add User",id="btn-add-user",color="success",
                                    style={"width":"100%","fontWeight":600})],md=2),
            ],className="g-3 mb-2"),
            html.Div(id="add-user-result"),
        ]),
    ])

# ══════════════════════════════════════════════════════════════
# USER MANAGEMENT CALLBACKS
# ══════════════════════════════════════════════════════════════
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

    fresh = list_users().to_dict("records")
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
    err = create_user(username.strip(), password, role or "viewer", display or "",
                      tenant_id=tenant_id, tenant_name=tenant_name)
    if err:
        return dbc.Alert("Error: {}".format(err), color="danger", duration=5000), no_update
    fresh = list_users().to_dict("records")
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
    return dcc.send_data_frame(_filt_s(b,sd,ed).to_csv,"medstar_sales.csv",index=False)

@app.callback(Output("dl-sales-xlsx","data"),Input("btn-dl-sales-xlsx","n_clicks"),
    State("filter-branch","value"),State("filter-date","start_date"),State("filter-date","end_date"),
    prevent_initial_call=True)
def dl_s_xlsx(_,b,sd,ed):
    return dcc.send_data_frame(_filt_s(b,sd,ed).to_excel,"medstar_sales.xlsx",index=False,sheet_name="Sales")

@app.callback(Output("dl-purch-csv","data"),Input("btn-dl-purch-csv","n_clicks"),
    State("filter-branch","value"),State("filter-date","start_date"),State("filter-date","end_date"),
    prevent_initial_call=True)
def dl_p_csv(_,b,sd,ed):
    return dcc.send_data_frame(_filt_p(b,sd,ed).to_csv,"medstar_purchases.csv",index=False)

@app.callback(Output("dl-purch-xlsx","data"),Input("btn-dl-purch-xlsx","n_clicks"),
    State("filter-branch","value"),State("filter-date","start_date"),State("filter-date","end_date"),
    prevent_initial_call=True)
def dl_p_xlsx(_,b,sd,ed):
    return dcc.send_data_frame(_filt_p(b,sd,ed).to_excel,"medstar_purchases.xlsx",index=False,sheet_name="Purchases")

@app.callback(Output("dl-pdf","data"),Input("btn-dl-pdf","n_clicks"),
    State("filter-branch","value"),State("filter-date","start_date"),State("filter-date","end_date"),
    prevent_initial_call=True)
def dl_pdf(_,branch,sd,ed):
    s=sales_df.copy(); p=purchase_df.copy()
    if branch!="All": s=s[s["branch"]==branch]; p=p[p["branch"]==branch]
    s=apply_date_filter(s,sd,ed,"bill_date"); p=apply_date_filter(p,sd,ed,"grn_date")
    pdf_bytes = generate_pdf(s,p,sd,ed,branch,fmt_inr)
    return dcc.send_bytes(pdf_bytes,"medstar_report_{}.pdf".format(date.today().strftime("%Y%m%d")))

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
    if not contents: return no_update,{"display":"none"},no_update,no_update
    df_raw,rtype,err = parse_upload(contents,filename)
    if err: return dbc.Alert("Error: {}".format(err),color="danger",dismissable=True),{"display":"none"},no_update,no_update
    bl = "Sales Report" if rtype=="sales" else "Purchase Report"
    bc = "success" if rtype=="sales" else "primary"
    result = dbc.Alert([html.Strong("Detected: {}  ".format(bl)),dbc.Badge(filename,color="secondary")],
                       color="success",style={"fontSize":"0.85rem","padding":"0.5rem 1rem"})
    return result,{"display":"block"},dbc.Badge(bl,color=bc,style={"fontSize":"0.85rem","padding":"0.4rem 0.8rem"}),\
           {"report_type":rtype,"filename":filename,"df_raw_json":df_raw.to_json()}

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
        df_raw.columns = range(len(df_raw.columns))
        sd = build_preview(df_raw,raw_store["report_type"],branch.strip(),month.strip())
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
    Input("upload-confirm-btn","n_clicks"),
    State("upload-prev-store","data"),State("data-version","data"),
    prevent_initial_call=True,
)
def confirm_upload(n,store_data,version):
    global sales_df,purchase_df
    if not store_data:
        return no_update,dbc.Alert("Nothing to upload.",color="warning"),no_update,no_update,no_update,no_update,no_update
    row_count,duplicate,error = append_upload_to_db(store_data,engine)
    if error:
        return no_update,dbc.Alert("Upload failed: {}".format(error),color="danger",dismissable=True),no_update,no_update,no_update,no_update,no_update
    sales_df,purchase_df = load_from_db(engine)
    warn = " Duplicate data detected -- rows appended." if duplicate else ""
    msg = dbc.Alert([html.Strong("Loaded {} rows!".format(row_count)),
                     html.Span("  Branch: {} | Month: {}{}".format(store_data["branch"],store_data["month_label"],warn)),
                     html.Br(),html.Span("Switch tabs to see updated data.",style={"fontSize":"0.8rem","color":"#555"})],
                    color="warning" if duplicate else "success",dismissable=True)
    hist = get_upload_history(engine)
    hcols=[{"name":"File","id":"filename"},{"name":"Type","id":"report_type"},
           {"name":"Branch","id":"branch"},{"name":"Month","id":"month_label"},
           {"name":"Rows","id":"row_count"},{"name":"Uploaded At","id":"uploaded_at"},
           {"name":"Duplicate?","id":"duplicate_warning"}]
    new_tbl = dash_table.DataTable(id="upload-history-table",columns=hcols,
        data=hist.to_dict("records"),page_size=10,style_table={"overflowX":"auto"},
        style_cell={"fontSize":"0.78rem","padding":"6px 10px","textAlign":"left"},
        style_header={"backgroundColor":"#f0f8f4","fontWeight":"bold","color":C_GREEN},
        style_data_conditional=[{"if":{"filter_query":"{duplicate_warning} = 1"},
            "backgroundColor":"#fff3cd","color":"#856404"}])
    return version+1,msg,new_tbl,None,None,{"display":"none"},""

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

    status, data = call_api("POST", "/tenants", json_body={
        "name":          name.strip(),
        "slug":          slug.strip().lower(),
        "domain_type":   domain or "pharmacy",
        "plan":          plan or "basic",
        "contact_email": email or "",
    })

    if status == 201:
        msg = dbc.Alert(f"Tenant '{name}' created successfully.", color="success", dismissable=True)
    else:
        detail = data.get("detail", "Unknown error") if isinstance(data, dict) else str(data)
        return dbc.Alert(f"Error: {detail}", color="danger", dismissable=True), no_update

    # Refresh table
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


# Expose Flask server for gunicorn (Render.com / production)
server = app.server

# ── Run ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=8050)
