"""
app.py - MedStar Pharmacy Analytics Dashboard
Tabs: Overview | Sales | Purchases | Branch Compare | Upload Data
Run:  python app.py   then open  http://127.0.0.1:8050
"""

import io
from io import BytesIO
from datetime import date, timedelta

import dash
from dash import dcc, html, Input, Output, State, dash_table, no_update, ctx
import dash_bootstrap_components as dbc
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd

from data_loader import (
    get_data, get_upload_history, parse_upload,
    build_preview, append_upload_to_db, load_from_db
)
from pdf_report import generate_pdf

# ── Load data at startup ──────────────────────────────────────
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
# Edit these values to match your pharmacy's targets
THRESHOLDS = {
    "margin_pct_min":      20.0,   # avg margin below this % → danger
    "daily_sales_min":  50_000,    # avg daily sales below this (Rs.) → warning
    "return_pct_max":       5.0,   # returns / net_sales > this % → warning
    "purchase_ratio_max":  85.0,   # purchase / sales > this % → warning
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
    cls   = "up"  if delta >= 0 else "dn"
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

# ── Threshold alert helpers ───────────────────────────────────
def check_alerts(s, p):
    """Compare current-period DataFrames against THRESHOLDS. Returns list of dicts."""
    alerts = []
    if not s.empty:
        if "margin_pct" in s.columns:
            avg_m = s["margin_pct"].mean()
            if avg_m < THRESHOLDS["margin_pct_min"]:
                alerts.append({
                    "level": "danger",
                    "msg": "Avg Margin {:.1f}% is below the minimum threshold of {:.0f}%. Review pricing or supplier discounts.".format(
                        avg_m, THRESHOLDS["margin_pct_min"]),
                })
        if "net_amount" in s.columns:
            avg_d = s["net_amount"].mean()
            if avg_d < THRESHOLDS["daily_sales_min"]:
                alerts.append({
                    "level": "warning",
                    "msg": "Avg daily sales {} is below the target of {}. Consider running promotions.".format(
                        fmt_inr(avg_d), fmt_inr(THRESHOLDS["daily_sales_min"])),
                })
        if "cash_return" in s.columns and "net_amount" in s.columns:
            ret = s["cash_return"].sum()
            sal = s["net_amount"].sum()
            if sal > 0 and (ret / sal * 100) > THRESHOLDS["return_pct_max"]:
                alerts.append({
                    "level": "warning",
                    "msg": "Returns are {:.1f}% of sales — above the {:.0f}% threshold. Investigate high-return SKUs.".format(
                        ret / sal * 100, THRESHOLDS["return_pct_max"]),
                })
    if not s.empty and not p.empty:
        sal = s["net_amount"].sum() if "net_amount" in s.columns else 0
        pur = p["net_amount"].sum() if "net_amount" in p.columns else 0
        if sal > 0 and (pur / sal * 100) > THRESHOLDS["purchase_ratio_max"]:
            alerts.append({
                "level": "warning",
                "msg": "Purchase/Sales ratio is {:.1f}% — above {:.0f}% threshold. Cash-flow risk: review order quantities.".format(
                    pur / sal * 100, THRESHOLDS["purchase_ratio_max"]),
            })
    return alerts

def render_alert_banners(alerts):
    if not alerts:
        return html.Div(
            html.Span("✅  All KPIs within thresholds for the selected period.",
                      style={"fontSize":"0.78rem","color":C_GREEN,"fontWeight":600}),
            style={"background":"#e8f5e9","border":"1px solid #a8d5a8","borderRadius":"8px",
                   "padding":"0.45rem 0.9rem","marginBottom":"0.8rem"},
        )
    icon_map = {"danger": "⚠️", "warning": "\U0001f4c9", "info": "ℹ️"}
    return html.Div([
        dbc.Alert(
            [html.Strong("{} ".format(icon_map.get(a["level"], "⚠"))),
             html.Span(a["msg"])],
            color=a["level"], dismissable=True,
            style={"fontSize":"0.8rem","padding":"0.45rem 0.9rem","marginBottom":"0.35rem"},
        )
        for a in alerts
    ], style={"marginBottom":"0.6rem"})

# ── UI helpers ────────────────────────────────────────────────
def fmt_inr(val):
    try:
        val = float(val)
    except (TypeError, ValueError):
        return str(val)
    if val >= 100000: return "Rs.{:.2f}L".format(val / 100000)
    if val >= 1000:   return "Rs.{:.1f}K".format(val / 1000)
    return "Rs.{:.0f}".format(val)

def kpi_card(label, value, sub="", color_class="", icon="*", delta=None):
    return html.Div(
        [html.Div(icon, className="kpi-icon"),
         html.Div(label, className="kpi-label"),
         html.Div(value, className="kpi-value")]
        + delta_el(delta)
        + [html.Div(sub, className="kpi-sub")],
        className="kpi-card {}".format(color_class),
    )

def chart_card(title, figure, height=None):
    style = {"height": "{}px".format(height)} if height else {}
    return html.Div([
        html.Div(title, className="chart-card-title"),
        dcc.Graph(figure=figure, config={"displayModeBar": False}, style=style),
    ], className="chart-card")

def get_filter_options():
    s_branches = sales_df["branch"].unique().tolist()    if not sales_df.empty    else []
    p_branches = purchase_df["branch"].unique().tolist() if not purchase_df.empty else []
    return ["All"] + sorted(set(s_branches + p_branches))

def _hex_to_rgb(hex_color):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

# ── Download button bar ───────────────────────────────────────
def download_bar():
    btn = lambda label, bid, col: dbc.Button(
        label, id=bid, color=col, size="sm", outline=True,
        style={"fontSize":"0.71rem","fontWeight":600},
    )
    return html.Div([
        html.Div("Export", className="sidebar-label",
                 style={"marginBottom":"0.3rem"}),
        html.Div([
            btn("Sales CSV",     "btn-dl-sales-csv",  "success"),
            btn("Sales Excel",   "btn-dl-sales-xlsx", "success"),
            btn("Purchase CSV",  "btn-dl-purch-csv",  "primary"),
            btn("Purchase Excel","btn-dl-purch-xlsx",  "primary"),
        ], style={"display":"grid","gridTemplateColumns":"1fr 1fr","gap":"4px",
                  "marginBottom":"4px"}),
        dbc.Button("📄 PDF Report", id="btn-dl-pdf", color="dark", size="sm",
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
                start_date=str(data_min),
                end_date=str(data_max),
                min_date_allowed="2020-01-01",
                max_date_allowed="2030-12-31",
                display_format="DD MMM YYYY",
                style={"width":"100%"},
            ),
            className="date-picker-wrap",
            style={"marginBottom":"0.75rem"},
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
navbar = dbc.Navbar(
    dbc.Container([
        html.Div([
            html.Span("\U0001f3e5 "),
            html.Span("MedStar Pharmacy", style={"fontWeight":700,"fontSize":"1.15rem"}),
            html.Span("  Multi-Branch Analytics",
                      style={"fontSize":"0.78rem","opacity":"0.8","marginLeft":"0.5rem"}),
        ]),
        html.Div(id="navbar-period-label",
                 style={"fontSize":"0.72rem","opacity":"0.85","marginLeft":"auto"}),
    ], fluid=True),
    color=C_GREEN, dark=True,
    style={"padding":"0 1.25rem","height":"62px"},
)

# ── App ───────────────────────────────────────────────────────
app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP],
    suppress_callback_exceptions=True,
    title="MedStar Analytics",
)

app.layout = html.Div([
    navbar,
    # Stores
    dcc.Store(id="data-version",      data=0),
    dcc.Store(id="upload-raw-store",  data=None),
    dcc.Store(id="upload-prev-store", data=None),
    # Download targets
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
                dcc.Tab(label="Overview",        value="overview",
                        className="custom-tab", selected_className="custom-tab--selected"),
                dcc.Tab(label="Sales",           value="sales",
                        className="custom-tab", selected_className="custom-tab--selected"),
                dcc.Tab(label="Purchases",       value="purchases",
                        className="custom-tab", selected_className="custom-tab--selected"),
                dcc.Tab(label="Branch Compare",  value="compare",
                        className="custom-tab", selected_className="custom-tab--selected"),
                dcc.Tab(label="Upload Data",     value="upload",
                        className="custom-tab", selected_className="custom-tab--selected"),
            ]),
            html.Div(id="tab-content", style={"padding":"1rem 0.5rem"}),
        ], style={"flex":1,"padding":"0.5rem 1rem","overflowY":"auto"}),
    ], style={"display":"flex","height":"calc(100vh - 62px)","overflow":"hidden"}),
])

# ── Quick-select ──────────────────────────────────────────────
@app.callback(
    Output("filter-date", "start_date"),
    Output("filter-date", "end_date"),
    Input("qs-this", "n_clicks"),
    Input("qs-last", "n_clicks"),
    Input("qs-3m",   "n_clicks"),
    Input("qs-all",  "n_clicks"),
    prevent_initial_call=True,
)
def apply_quick_select(n_this, n_last, n_3m, n_all):
    today     = date.today()
    triggered = ctx.triggered_id
    if triggered == "qs-this":
        s = date(today.year, today.month, 1)
        e = today
    elif triggered == "qs-last":
        first_this = date(today.year, today.month, 1)
        e = first_this - timedelta(days=1)
        s = date(e.year, e.month, 1)
    elif triggered == "qs-3m":
        s = (today.replace(day=1) - timedelta(days=60)).replace(day=1)
        e = today
    else:
        s, e = _data_date_bounds()
    return str(s), str(e)

# ── Tab router ────────────────────────────────────────────────
@app.callback(
    Output("tab-content",          "children"),
    Output("filter-branch",        "options"),
    Output("sidebar-sources",      "children"),
    Output("sidebar-data-status",  "children"),
    Output("navbar-period-label",  "children"),
    Input("main-tabs",    "value"),
    Input("filter-branch","value"),
    Input("filter-date",  "start_date"),
    Input("filter-date",  "end_date"),
    Input("data-version", "data"),
)
def render_tab(tab, branch, start_date, end_date, _version):
    branches = get_filter_options()
    b_opts   = [{"label": b, "value": b} for b in branches]
    BCM      = get_branch_color_map()

    s_branches = sorted(sales_df["branch"].unique())    if not sales_df.empty    else []
    p_branches = sorted(purchase_df["branch"].unique()) if not purchase_df.empty else []
    all_b = sorted(set(s_branches + p_branches))

    sources = []
    for b in all_b:
        color = BCM.get(b, C_TEAL)
        sources.append(html.Div([
            html.Span(className="source-dot", style={"background": color}),
            html.Span(b, style={"fontSize":"0.72rem","color":"#555"}),
        ], className="source-item"))

    s_rows = len(sales_df)    if not sales_df.empty    else 0
    p_rows = len(purchase_df) if not purchase_df.empty else 0
    data_min, data_max = _data_date_bounds()
    status = html.Div([
        html.Div([html.Span("Sales rows"),
                  html.Span("{:,}".format(s_rows), className="stat-val")], className="stat-row"),
        html.Div([html.Span("Purchase rows"),
                  html.Span("{:,}".format(p_rows), className="stat-val")], className="stat-row"),
        html.Div([html.Span("Span"),
                  html.Span("{} - {}".format(data_min.strftime("%b %y"),
                                             data_max.strftime("%b %y")),
                            className="stat-val")], className="stat-row"),
    ], className="data-status")

    try:
        s_str = pd.to_datetime(start_date).strftime("%d %b %Y") if start_date else "All"
        e_str = pd.to_datetime(end_date).strftime("%d %b %Y")   if end_date   else "All"
        period_label = "Period: {}  to  {}".format(s_str, e_str)
    except Exception:
        period_label = ""

    if tab == "overview":    content = render_overview(branch, start_date, end_date)
    elif tab == "sales":     content = render_sales(branch, start_date, end_date)
    elif tab == "purchases": content = render_purchases(branch, start_date, end_date)
    elif tab == "compare":   content = render_compare(start_date, end_date)
    elif tab == "upload":    content = render_upload_tab()
    else:                    content = html.Div()

    return content, b_opts, sources, status, period_label

# ══════════════════════════════════════════════════════════════
# TAB 1 — OVERVIEW  (+ alerts + downloads)
# ══════════════════════════════════════════════════════════════
def render_overview(branch, start_date, end_date):
    s = sales_df.copy()
    p = purchase_df.copy()
    if branch != "All":
        s = s[s["branch"] == branch]
        p = p[p["branch"] == branch]

    s = apply_date_filter(s, start_date, end_date, "bill_date")
    p = apply_date_filter(p, start_date, end_date, "grn_date")

    base_s = sales_df    if branch == "All" else sales_df[sales_df["branch"]==branch]
    base_p = purchase_df if branch == "All" else purchase_df[purchase_df["branch"]==branch]
    s_prev = get_prev_period(base_s, start_date, end_date, "bill_date")
    p_prev = get_prev_period(base_p, start_date, end_date, "grn_date")
    BCM    = get_branch_color_map()

    sales_val   = s["net_amount"].sum()   if not s.empty else 0
    purch_val   = p["net_amount"].sum()   if not p.empty else 0
    avg_daily   = s["net_amount"].mean()  if not s.empty else 0
    avg_margin  = s["margin_pct"].mean()  if (not s.empty and "margin_pct" in s.columns) else None
    total_bills = int(s["total_bills"].sum()) if (not s.empty and "total_bills" in s.columns) else 0
    total_gst   = p["total_gst"].sum()   if (not p.empty and "total_gst" in p.columns) else 0

    sp_sales  = s_prev["net_amount"].sum()   if not s_prev.empty else None
    sp_purch  = p_prev["net_amount"].sum()   if not p_prev.empty else None
    sp_daily  = s_prev["net_amount"].mean()  if not s_prev.empty else None
    sp_margin = s_prev["margin_pct"].mean()  if (not s_prev.empty and "margin_pct" in s_prev.columns) else None
    sp_bills  = int(s_prev["total_bills"].sum()) if (not s_prev.empty and "total_bills" in s_prev.columns) else None
    sp_gst    = p_prev["total_gst"].sum()   if (not p_prev.empty and "total_gst" in p_prev.columns) else None

    kpis = dbc.Row([
        dbc.Col(kpi_card("Total Sales",
            fmt_inr(sales_val) if not s.empty else "--",
            "{} days".format(len(s)), "", "\U0001f4b0",
            delta=pct_delta(sales_val, sp_sales)), md=2),
        dbc.Col(kpi_card("Total Purchase",
            fmt_inr(purch_val) if not p.empty else "--",
            "{} invoices".format(len(p)), "purchase", "\U0001f6d2",
            delta=pct_delta(purch_val, sp_purch)), md=2),
        dbc.Col(kpi_card("Avg Daily Sales",
            fmt_inr(avg_daily) if not s.empty else "--",
            "", "", "\U0001f4c5",
            delta=pct_delta(avg_daily, sp_daily)), md=2),
        dbc.Col(kpi_card("Avg Margin %",
            "{:.1f}%".format(avg_margin) if avg_margin is not None else "N/A",
            "", "margin", "\U0001f4ca",
            delta=pct_delta(avg_margin, sp_margin)), md=2),
        dbc.Col(kpi_card("Total Bills",
            "{:,}".format(total_bills), "", "bills", "\U0001f9fe",
            delta=pct_delta(total_bills, sp_bills)), md=2),
        dbc.Col(kpi_card("GST Paid",
            fmt_inr(total_gst), "On purchases", "returns", "\U0001f3db️",
            delta=pct_delta(total_gst, sp_gst)), md=2),
    ], className="g-3 mb-3")

    # Period badge
    try:
        s_str = pd.to_datetime(start_date).strftime("%d %b %Y") if start_date else "All"
        e_str = pd.to_datetime(end_date).strftime("%d %b %Y")   if end_date   else "All"
        badge_text = "{} - {}".format(s_str, e_str)
    except Exception:
        badge_text = "All data"

    # Trend chart
    fig_trend = go.Figure()
    if not s.empty:
        for b, grp in s.groupby("branch"):
            grp = grp.sort_values("bill_date")
            rgb = _hex_to_rgb(BCM.get(b, C_TEAL))
            fig_trend.add_trace(go.Scatter(
                x=grp["bill_date"], y=grp["net_amount"],
                name=b, mode="lines+markers",
                line=dict(color=BCM.get(b, C_TEAL), width=2.5),
                fill="tozeroy",
                fillcolor="rgba({},{},{},0.07)".format(*rgb),
            ))
    fig_trend.update_layout(**CHART_LAYOUT, title="Daily Net Sales Trend")

    # Sales mix donut
    fig_donut = go.Figure()
    if not s.empty and "pharma_sales" in s.columns and "non_pharma_sales" in s.columns:
        ph  = s["pharma_sales"].sum()
        np_ = s["non_pharma_sales"].sum()
        tot = ph + np_ or 1
        fig_donut = go.Figure(go.Pie(
            labels=["Pharma","Non-Pharma"], values=[ph, np_], hole=0.55,
            marker_colors=[C_GREEN, C_ORANGE]))
        fig_donut.update_layout(**CHART_LAYOUT, title="Sales Mix",
            annotations=[dict(text="{:.0f}%<br>Pharma".format(ph/tot*100),
                              x=0.5, y=0.5, font_size=13, showarrow=False)])
    else:
        fig_donut.update_layout(**CHART_LAYOUT, title="Sales Mix")

    # Purchase by month/branch
    if not p.empty and "grn_date" in p.columns:
        _p = p.copy()
        _p["_month"] = _p["grn_date"].dt.to_period("M").astype(str)
        p_monthly = _p.groupby(["_month","branch"])["net_amount"].sum().reset_index()
        fig_purch = px.bar(p_monthly, x="_month", y="net_amount", color="branch",
            color_discrete_map=BCM, barmode="group", text_auto=".2s",
            labels={"_month":"Month","net_amount":"Purchase (Rs.)","branch":"Branch"},
            title="Monthly Purchase by Branch")
    else:
        fig_purch = go.Figure()
    fig_purch.update_layout(**CHART_LAYOUT)

    return html.Div([
        html.Div([
            html.Span(className="accent"),
            html.Span("Overview"),
            html.Span(badge_text, className="period-badge"),
        ], className="section-heading"),
        # Threshold alert banners
        render_alert_banners(check_alerts(s, p)),
        kpis,
        dbc.Row([
            dbc.Col(chart_card("Daily Sales Trend", fig_trend), md=8),
            dbc.Col(chart_card("Sales Mix",         fig_donut), md=4),
        ], className="g-3"),
        dbc.Row([
            dbc.Col(chart_card("Monthly Purchase by Branch", fig_purch), md=12),
        ], className="g-3"),
    ])

# ══════════════════════════════════════════════════════════════
# TAB 2 — SALES
# ══════════════════════════════════════════════════════════════
def render_sales(branch, start_date, end_date):
    s = sales_df.copy()
    if branch != "All":
        s = s[s["branch"] == branch]
    s = apply_date_filter(s, start_date, end_date, "bill_date")

    if s.empty:
        return html.Div([
            html.Div([html.Span(className="accent"), html.Span("Sales Analysis")],
                     className="section-heading"),
            empty_state("📈", "No sales data", "Adjust the date range or branch filter."),
        ])
    BCM = get_branch_color_map()

    fig_daily = px.area(s.sort_values("bill_date"), x="bill_date", y="net_amount",
        color="branch", color_discrete_map=BCM,
        labels={"bill_date":"Date","net_amount":"Net Sales (Rs.)"}, title="Daily Net Sales")
    fig_daily.update_layout(**CHART_LAYOUT)

    fig_bills = px.bar(s.sort_values("bill_date"), x="bill_date", y="total_bills",
        color="branch", color_discrete_map=BCM,
        labels={"bill_date":"Date","total_bills":"Bills"}, title="Daily Bill Count")
    fig_bills.update_layout(**CHART_LAYOUT)

    fig_margin = px.line(s.sort_values("bill_date"), x="bill_date", y="margin_pct",
        color="branch", color_discrete_map=BCM, markers=True,
        labels={"bill_date":"Date","margin_pct":"Margin %"}, title="Gross Margin % Trend")
    if not s.empty:
        fig_margin.add_hline(y=s["margin_pct"].mean(), line_dash="dash", line_color="#aaa",
            annotation_text="Avg {:.1f}%".format(s["margin_pct"].mean()))
    # Threshold line
    fig_margin.add_hline(y=THRESHOLDS["margin_pct_min"], line_dash="dot",
                         line_color="#ef4444",
                         annotation_text="Min {:.0f}%".format(THRESHOLDS["margin_pct_min"]),
                         annotation_font_color="#ef4444")
    fig_margin.update_layout(**CHART_LAYOUT)

    cash   = s["cash_sales"].sum()   if "cash_sales"   in s.columns else 0
    credit = s["credit_sales"].sum() if "credit_sales" in s.columns else 0
    card   = s["card_sales"].sum()   if "card_sales"   in s.columns else 0
    fig_pay = go.Figure(go.Pie(
        labels=["Cash","Credit","Card"], values=[cash, credit, card],
        hole=0.5, marker_colors=[C_GREEN, C_ORANGE, C_BLUE], textinfo="label+percent"))
    fig_pay.update_layout(**CHART_LAYOUT, title="Payment Mode")

    s = s.copy()
    s["_month"] = s["bill_date"].dt.to_period("M").astype(str)
    pm = s.groupby(["_month","branch"])[["pharma_sales","non_pharma_sales"]].sum().reset_index()
    pm = pm.melt(id_vars=["_month","branch"], value_vars=["pharma_sales","non_pharma_sales"],
                 var_name="cat", value_name="amount")
    pm["cat"] = pm["cat"].map({"pharma_sales":"Pharma","non_pharma_sales":"Non-Pharma"})
    fig_pharma = px.bar(pm, x="_month", y="amount", color="cat", barmode="group",
        color_discrete_map={"Pharma":C_GREEN,"Non-Pharma":C_ORANGE}, text_auto=".2s",
        labels={"_month":"Month","amount":"Sales (Rs.)","cat":"Category"},
        title="Pharma vs Non-Pharma")
    fig_pharma.update_layout(**CHART_LAYOUT)

    ret = s[s["cash_return"] > 0].sort_values("bill_date") if "cash_return" in s.columns else pd.DataFrame()
    fig_ret = (px.bar(ret, x="bill_date", y="cash_return", color="branch",
        color_discrete_map=BCM,
        labels={"bill_date":"Date","cash_return":"Return (Rs.)"}, title="Daily Returns")
        if not ret.empty else go.Figure())
    fig_ret.update_layout(**CHART_LAYOUT)

    return html.Div([
        html.Div([html.Span(className="accent"), html.Span("Sales Analysis")],
                 className="section-heading"),
        dbc.Row([
            dbc.Col(chart_card("Daily Net Sales", fig_daily), md=8),
            dbc.Col(chart_card("Payment Mode",    fig_pay),   md=4),
        ], className="g-3"),
        dbc.Row([
            dbc.Col(chart_card("Daily Bill Count",     fig_bills),  md=4),
            dbc.Col(chart_card("Margin % Trend",       fig_margin), md=4),
            dbc.Col(chart_card("Pharma vs Non-Pharma", fig_pharma), md=4),
        ], className="g-3"),
        dbc.Row([dbc.Col(chart_card("Daily Returns", fig_ret), md=6)], className="g-3"),
    ])

# ══════════════════════════════════════════════════════════════
# TAB 3 — PURCHASES
# ══════════════════════════════════════════════════════════════
def render_purchases(branch, start_date, end_date):
    p = purchase_df.copy()
    if branch != "All":
        p = p[p["branch"] == branch]
    p = apply_date_filter(p, start_date, end_date, "grn_date")

    if p.empty:
        return html.Div([
            html.Div([html.Span(className="accent"), html.Span("Purchase Analysis")],
                     className="section-heading"),
            empty_state("🛒", "No purchase data", "Adjust the date range or branch filter."),
        ])
    BCM = get_branch_color_map()

    top_sup = p.groupby("supplier_name")["net_amount"].sum().sort_values(ascending=True).tail(15).reset_index()
    fig_sup = px.bar(top_sup, x="net_amount", y="supplier_name", orientation="h",
        color="net_amount", color_continuous_scale=[[0,"#c8e6c9"],[1,C_GREEN]],
        text_auto=".2s", title="Top 15 Suppliers by Value",
        labels={"net_amount":"Purchase (Rs.)","supplier_name":"Supplier"})
    fig_sup.update_coloraxes(showscale=False)
    fig_sup.update_layout(**CHART_LAYOUT, height=420)

    p_daily = p.dropna(subset=["grn_date"]).groupby(["grn_date","branch"])["net_amount"].sum().reset_index()
    fig_daily = (px.line(p_daily.sort_values("grn_date"), x="grn_date", y="net_amount",
        color="branch", color_discrete_map=BCM, markers=True,
        labels={"grn_date":"Date","net_amount":"Purchase (Rs.)"}, title="Daily Purchase Trend")
        if not p_daily.empty else go.Figure())
    fig_daily.update_layout(**CHART_LAYOUT)

    sg = p["sgst"].sum() if "sgst" in p.columns else 0
    cg = p["cgst"].sum() if "cgst" in p.columns else 0
    ig = p["igst"].sum() if "igst" in p.columns else 0
    gst_total = p["total_gst"].sum() if "total_gst" in p.columns else 0
    fig_gst = go.Figure(go.Pie(
        labels=["SGST","CGST","IGST"], values=[sg, cg, ig],
        hole=0.5, marker_colors=[C_GREEN, C_BLUE, C_ORANGE], textinfo="label+percent"))
    fig_gst.update_layout(**CHART_LAYOUT,
                          title="GST Split -- Total {}".format(fmt_inr(gst_total)))

    sf = p.groupby("supplier_name")["grn_number"].count().sort_values(ascending=False).head(10).reset_index()
    sf.columns = ["supplier_name","grn_count"]
    fig_freq = px.bar(sf, x="grn_count", y="supplier_name", orientation="h",
        color="grn_count", color_continuous_scale=[[0,"#bbdefb"],[1,C_BLUE]],
        text_auto=True, title="Top 10 by Delivery Frequency",
        labels={"grn_count":"GRNs","supplier_name":""})
    fig_freq.update_coloraxes(showscale=False)
    fig_freq.update_layout(**CHART_LAYOUT, height=320)

    return html.Div([
        html.Div([html.Span(className="accent"), html.Span("Purchase Analysis")],
                 className="section-heading"),
        dbc.Row([
            dbc.Col(chart_card("Top 15 Suppliers", fig_sup), md=7),
            dbc.Col([
                chart_card("GST Breakdown",      fig_gst),
                chart_card("Delivery Frequency", fig_freq),
            ], md=5),
        ], className="g-3"),
        dbc.Row([dbc.Col(chart_card("Daily Purchase Trend", fig_daily), md=12)],
                className="g-3"),
    ])

# ══════════════════════════════════════════════════════════════
# TAB 4 — BRANCH COMPARE
# ══════════════════════════════════════════════════════════════
def render_compare(start_date, end_date):
    s = apply_date_filter(sales_df.copy(),    start_date, end_date, "bill_date")
    p = apply_date_filter(purchase_df.copy(), start_date, end_date, "grn_date")
    BCM = get_branch_color_map()

    s_branches   = sorted(s["branch"].unique().tolist()) if not s.empty else []
    p_branches   = sorted(p["branch"].unique().tolist()) if not p.empty else []
    all_branches = sorted(set(s_branches + p_branches))

    if len(all_branches) < 2:
        msg = ("No data loaded yet." if not all_branches
               else "Only one branch ({}).  Upload a second branch to compare.".format(all_branches[0]))
        return html.Div([
            html.Div([html.Span(className="accent"), html.Span("Branch Comparison")],
                     className="section-heading"),
            html.Div("ℹ️  {}".format(msg), className="info-banner"),
        ])

    def branch_kpis(b):
        color = BCM.get(b, C_TEAL)
        bs = s[s["branch"]==b] if not s.empty else pd.DataFrame()
        bp = p[p["branch"]==b] if not p.empty else pd.DataFrame()
        return dbc.Card([
            dbc.CardHeader(html.Span(b, style={"fontWeight":700,"color":color})),
            dbc.CardBody([
                dbc.Row([
                    dbc.Col(kpi_card("Total Sales",
                        fmt_inr(bs["net_amount"].sum()) if not bs.empty else "--",
                        "{} days".format(len(bs)) if not bs.empty else "No data"), md=6),
                    dbc.Col(kpi_card("Avg Daily",
                        fmt_inr(bs["net_amount"].mean()) if not bs.empty else "--"), md=6),
                ], className="g-2 mb-2"),
                dbc.Row([
                    dbc.Col(kpi_card("Total Purchase",
                        fmt_inr(bp["net_amount"].sum()) if not bp.empty else "--",
                        "{} invoices".format(len(bp)) if not bp.empty else "No data", "purchase"), md=6),
                    dbc.Col(kpi_card("Avg Margin %",
                        "{:.1f}%".format(bs["margin_pct"].mean())
                        if (not bs.empty and "margin_pct" in bs.columns) else "--",
                        "", "margin"), md=6),
                ], className="g-2"),
            ]),
        ], style={"border":"2px solid {}".format(color),"borderRadius":"12px"})

    col_width = max(3, 12 // len(all_branches))
    kpi_row   = dbc.Row([dbc.Col(branch_kpis(b), md=col_width) for b in all_branches],
                        className="g-3 mb-3")

    if not p.empty and "grn_date" in p.columns:
        _p = p.copy()
        _p["_month"] = _p["grn_date"].dt.to_period("M").astype(str)
        p_cmp = _p.groupby(["_month","branch"])["net_amount"].sum().reset_index()
        fig_pc = px.bar(p_cmp, x="branch", y="net_amount", color="_month", barmode="group",
            color_discrete_sequence=[C_GREEN,"#a8d5a8","#ffd59e","#c9b8f5"], text_auto=".2s",
            title="Purchase by Branch and Month",
            labels={"net_amount":"Purchase (Rs.)","branch":"Branch","_month":"Month"})
    else:
        fig_pc = go.Figure()
    fig_pc.update_layout(**CHART_LAYOUT)

    sections = [
        html.Div([html.Span(className="accent"), html.Span("Branch Comparison")],
                 className="section-heading"),
        kpi_row,
        dbc.Row([dbc.Col(chart_card("Purchase by Branch and Month", fig_pc), md=12)],
                className="g-3"),
    ]

    if len(p_branches) >= 2 and "supplier_name" in p.columns:
        b1, b2 = p_branches[0], p_branches[1]
        s1 = set(p[p["branch"]==b1]["supplier_name"].dropna().unique())
        s2 = set(p[p["branch"]==b2]["supplier_name"].dropna().unique())
        shared = s1 & s2
        venn = pd.DataFrame({
            "Category": ["Only {}".format(b1), "Shared", "Only {}".format(b2)],
            "Count":    [len(s1-s2), len(shared), len(s2-s1)],
        })
        fig_venn = px.bar(venn, x="Category", y="Count", color="Category",
            color_discrete_map={
                "Only {}".format(b1): BCM.get(b1, C_GREEN),
                "Shared":             C_TEAL,
                "Only {}".format(b2): BCM.get(b2, C_BLUE),
            },
            text_auto=True, title="Supplier Overlap ({} shared)".format(len(shared)))
        fig_venn.update_layout(**CHART_LAYOUT)
        sections.append(dbc.Row([dbc.Col(chart_card("Supplier Overlap", fig_venn), md=12)],
                                className="g-3"))

    if not p.empty and "supplier_name" in p.columns:
        top_cols = []
        for b in p_branches:
            bp = p[p["branch"]==b]
            if bp.empty:
                continue
            ts = bp.groupby("supplier_name")["net_amount"].sum().sort_values(ascending=True).tail(8).reset_index()
            fig_ts = px.bar(ts, x="net_amount", y="supplier_name", orientation="h",
                title="Top Suppliers -- {}".format(b),
                color_discrete_sequence=[BCM.get(b, C_TEAL)],
                text_auto=".2s", labels={"net_amount":"Rs.","supplier_name":""})
            fig_ts.update_layout(**CHART_LAYOUT, height=280)
            top_cols.append(dbc.Col(chart_card("Top Suppliers -- {}".format(b), fig_ts),
                                    md=col_width))
        if top_cols:
            sections.append(dbc.Row(top_cols, className="g-3"))

    return html.Div(sections)

# ══════════════════════════════════════════════════════════════
# TAB 5 — UPLOAD DATA
# ══════════════════════════════════════════════════════════════
def render_upload_tab():
    hist = get_upload_history(engine)
    hist_cols = [
        {"name":"File",        "id":"filename"},
        {"name":"Type",        "id":"report_type"},
        {"name":"Branch",      "id":"branch"},
        {"name":"Month",       "id":"month_label"},
        {"name":"Rows",        "id":"row_count"},
        {"name":"Uploaded At", "id":"uploaded_at"},
        {"name":"Duplicate?",  "id":"duplicate_warning"},
    ]
    hist_data = hist.to_dict("records") if not hist.empty else []

    return html.Div([
        html.Div([html.Span(className="accent"), html.Span("Upload New Data")],
                 className="section-heading"),
        html.Div([
            "Upload Sales or Purchase reports exported from your POS system. ",
            "The app auto-detects the report type from column headers.",
        ], className="info-banner"),

        html.Div(className="chart-card", children=[
            html.Div("Step 1 -- Drop your Excel file", className="chart-card-title"),
            dcc.Upload(
                id="upload-file",
                children=html.Div([
                    html.Div("\U0001f4c2", style={"fontSize":"2.5rem","marginBottom":"0.3rem"}),
                    html.Div("Drag and Drop Excel file here", style={"fontWeight":600}),
                    html.Div("or click to browse",
                             style={"color":"#888","fontSize":"0.8rem"}),
                    html.Div("Supports: .xlsx  .xls  (Sales and Purchase reports from POS)",
                             style={"fontSize":"0.72rem","color":"#aaa","marginTop":"0.4rem"}),
                ]),
                className="upload-area", accept=".xlsx,.xls", multiple=False,
            ),
            html.Div(id="upload-detect-result", style={"marginTop":"0.8rem"}),
        ]),

        html.Div(id="upload-config-card", style={"display":"none"}, children=[
            html.Div(className="chart-card", children=[
                html.Div("Step 2 -- Confirm details and Load", className="chart-card-title"),
                dbc.Row([
                    dbc.Col([
                        html.Div("Detected Type", className="sidebar-label"),
                        html.Div(id="upload-type-badge"),
                    ], md=3),
                    dbc.Col([
                        html.Div("Branch Name", className="sidebar-label"),
                        dbc.Input(id="upload-branch", placeholder="e.g. Keelkattalai",
                                  debounce=True, style={"fontSize":"0.85rem"}),
                    ], md=3),
                    dbc.Col([
                        html.Div("Month and Year", className="sidebar-label"),
                        dbc.Input(id="upload-month", placeholder="e.g. Apr 2026",
                                  debounce=True, style={"fontSize":"0.85rem"}),
                    ], md=3),
                    dbc.Col([
                        html.Div(" ", className="sidebar-label"),
                        dbc.Button("Load into Dashboard", id="upload-confirm-btn",
                                   color="success", className="w-100",
                                   disabled=True, style={"fontWeight":600}),
                    ], md=3),
                ], className="g-3 mb-3"),
                html.Div(id="upload-preview-container"),
            ]),
        ]),

        html.Div(id="upload-status-msg", style={"marginBottom":"0.8rem"}),

        html.Div(className="chart-card", children=[
            html.Div("Upload History", className="chart-card-title"),
            html.Div(id="upload-history-container", children=[
                dash_table.DataTable(
                    id="upload-history-table",
                    columns=hist_cols, data=hist_data, page_size=10,
                    style_table={"overflowX":"auto"},
                    style_cell={"fontSize":"0.78rem","padding":"6px 10px","textAlign":"left"},
                    style_header={"backgroundColor":"#f0f8f4","fontWeight":"bold","color":C_GREEN},
                    style_data_conditional=[
                        {"if":{"filter_query":"{duplicate_warning} = 1"},
                         "backgroundColor":"#fff3cd","color":"#856404"},
                    ],
                ) if hist_data else html.Div("No uploads yet.",
                                             style={"color":"#888","fontSize":"0.85rem"}),
            ]),
        ]),
    ])

# ══════════════════════════════════════════════════════════════
# DOWNLOAD CALLBACKS
# ══════════════════════════════════════════════════════════════
def _filtered_sales(branch, start_date, end_date):
    s = sales_df.copy()
    if branch != "All":
        s = s[s["branch"] == branch]
    s = apply_date_filter(s, start_date, end_date, "bill_date")
    for col in s.select_dtypes(include=["datetime64[ns]"]).columns:
        s[col] = s[col].dt.strftime("%Y-%m-%d")
    return s

def _filtered_purch(branch, start_date, end_date):
    p = purchase_df.copy()
    if branch != "All":
        p = p[p["branch"] == branch]
    p = apply_date_filter(p, start_date, end_date, "grn_date")
    for col in p.select_dtypes(include=["datetime64[ns]"]).columns:
        p[col] = p[col].dt.strftime("%Y-%m-%d")
    return p

@app.callback(
    Output("dl-sales-csv", "data"),
    Input("btn-dl-sales-csv", "n_clicks"),
    State("filter-branch",   "value"),
    State("filter-date",     "start_date"),
    State("filter-date",     "end_date"),
    prevent_initial_call=True,
)
def dl_sales_csv(_, branch, sd, ed):
    return dcc.send_data_frame(_filtered_sales(branch, sd, ed).to_csv,
                               "medstar_sales.csv", index=False)

@app.callback(
    Output("dl-sales-xlsx", "data"),
    Input("btn-dl-sales-xlsx", "n_clicks"),
    State("filter-branch",     "value"),
    State("filter-date",       "start_date"),
    State("filter-date",       "end_date"),
    prevent_initial_call=True,
)
def dl_sales_xlsx(_, branch, sd, ed):
    return dcc.send_data_frame(_filtered_sales(branch, sd, ed).to_excel,
                               "medstar_sales.xlsx", index=False, sheet_name="Sales")

@app.callback(
    Output("dl-purch-csv", "data"),
    Input("btn-dl-purch-csv", "n_clicks"),
    State("filter-branch",    "value"),
    State("filter-date",      "start_date"),
    State("filter-date",      "end_date"),
    prevent_initial_call=True,
)
def dl_purch_csv(_, branch, sd, ed):
    return dcc.send_data_frame(_filtered_purch(branch, sd, ed).to_csv,
                               "medstar_purchases.csv", index=False)

@app.callback(
    Output("dl-purch-xlsx", "data"),
    Input("btn-dl-purch-xlsx", "n_clicks"),
    State("filter-branch",     "value"),
    State("filter-date",       "start_date"),
    State("filter-date",       "end_date"),
    prevent_initial_call=True,
)
def dl_purch_xlsx(_, branch, sd, ed):
    return dcc.send_data_frame(_filtered_purch(branch, sd, ed).to_excel,
                               "medstar_purchases.xlsx", index=False, sheet_name="Purchases")

@app.callback(
    Output("dl-pdf", "data"),
    Input("btn-dl-pdf",    "n_clicks"),
    State("filter-branch", "value"),
    State("filter-date",   "start_date"),
    State("filter-date",   "end_date"),
    prevent_initial_call=True,
)
def dl_pdf(_, branch, sd, ed):
    s = sales_df.copy()
    p = purchase_df.copy()
    if branch != "All":
        s = s[s["branch"] == branch]
        p = p[p["branch"] == branch]
    s = apply_date_filter(s, sd, ed, "bill_date")
    p = apply_date_filter(p, sd, ed, "grn_date")
    pdf_bytes = generate_pdf(s, p, sd, ed, branch, fmt_inr)
    filename  = "medstar_report_{}.pdf".format(date.today().strftime("%Y%m%d"))
    return dcc.send_bytes(pdf_bytes, filename)

# ══════════════════════════════════════════════════════════════
# UPLOAD CALLBACKS
# ══════════════════════════════════════════════════════════════
@app.callback(
    Output("upload-detect-result", "children"),
    Output("upload-config-card",   "style"),
    Output("upload-type-badge",    "children"),
    Output("upload-raw-store",     "data"),
    Input("upload-file",           "contents"),
    State("upload-file",           "filename"),
    prevent_initial_call=True,
)
def handle_file_drop(contents, filename):
    if not contents:
        return no_update, {"display":"none"}, no_update, no_update
    df_raw, report_type, error = parse_upload(contents, filename)
    if error:
        return (dbc.Alert("Error: {}".format(error), color="danger", dismissable=True),
                {"display":"none"}, no_update, no_update)
    badge_color = "success" if report_type == "sales" else "primary"
    badge_label = "Sales Report" if report_type == "sales" else "Purchase Report"
    result = dbc.Alert([
        html.Strong("Detected: {}  ".format(badge_label)),
        dbc.Badge(filename, color="secondary"),
    ], color="success", style={"fontSize":"0.85rem","padding":"0.5rem 1rem"})
    type_badge = dbc.Badge(badge_label, color=badge_color,
                           style={"fontSize":"0.85rem","padding":"0.4rem 0.8rem"})
    raw_store = {"report_type":report_type, "filename":filename, "df_raw_json":df_raw.to_json()}
    return result, {"display":"block"}, type_badge, raw_store


@app.callback(
    Output("upload-preview-container", "children"),
    Output("upload-prev-store",        "data"),
    Output("upload-confirm-btn",       "disabled"),
    Input("upload-branch",  "value"),
    Input("upload-month",   "value"),
    State("upload-raw-store","data"),
    prevent_initial_call=True,
)
def update_preview(branch, month, raw_store):
    if not raw_store or not branch or not month:
        return no_update, no_update, True
    try:
        df_raw = pd.read_json(io.StringIO(raw_store["df_raw_json"]))
        df_raw.columns = range(len(df_raw.columns))
        store_data = build_preview(df_raw, raw_store["report_type"],
                                   branch.strip(), month.strip())
        store_data["filename"] = raw_store["filename"]
        prev_cols = [{"name":c,"id":c} for c in store_data["columns"][:8]]
        preview = html.Div([
            html.Div("Preview -- first 5 rows of {} total rows:".format(store_data["row_count"]),
                     style={"fontSize":"0.8rem","color":"#555","marginBottom":"0.4rem"}),
            dash_table.DataTable(
                columns=prev_cols, data=store_data["preview"],
                style_table={"overflowX":"auto"},
                style_cell={"fontSize":"0.75rem","padding":"5px 8px","maxWidth":"150px",
                             "overflow":"hidden","textOverflow":"ellipsis"},
                style_header={"backgroundColor":"#e8f5e9","fontWeight":"bold"},
            ),
        ])
        return preview, store_data, False
    except Exception as e:
        return dbc.Alert("Preview error: {}".format(e), color="warning"), no_update, True


@app.callback(
    Output("data-version",             "data"),
    Output("upload-status-msg",        "children"),
    Output("upload-history-container", "children"),
    Output("upload-raw-store",         "data",     allow_duplicate=True),
    Output("upload-prev-store",        "data",     allow_duplicate=True),
    Output("upload-config-card",       "style",    allow_duplicate=True),
    Output("upload-detect-result",     "children", allow_duplicate=True),
    Input("upload-confirm-btn",        "n_clicks"),
    State("upload-prev-store",         "data"),
    State("data-version",              "data"),
    prevent_initial_call=True,
)
def confirm_upload(n_clicks, store_data, version):
    global sales_df, purchase_df
    if not store_data:
        return (no_update,
                dbc.Alert("Nothing to upload.", color="warning"),
                no_update, no_update, no_update, no_update, no_update)

    row_count, duplicate, error = append_upload_to_db(store_data, engine)
    if error:
        return (no_update,
                dbc.Alert("Upload failed: {}".format(error), color="danger", dismissable=True),
                no_update, no_update, no_update, no_update, no_update)

    sales_df, purchase_df = load_from_db(engine)

    warn = (" Warning: data already existed -- rows appended (may duplicate)."
            if duplicate else "")
    msg = dbc.Alert([
        html.Strong("Loaded {} rows successfully!".format(row_count)),
        html.Span("  Branch: {} | Month: {}".format(store_data["branch"],
                                                     store_data["month_label"])),
        html.Span(warn, style={"color":"#856404"}),
        html.Br(),
        html.Span("Switch tabs to see updated data.",
                  style={"fontSize":"0.8rem","color":"#555"}),
    ], color="warning" if duplicate else "success", dismissable=True)

    hist = get_upload_history(engine)
    hist_cols = [
        {"name":"File","id":"filename"},{"name":"Type","id":"report_type"},
        {"name":"Branch","id":"branch"},{"name":"Month","id":"month_label"},
        {"name":"Rows","id":"row_count"},{"name":"Uploaded At","id":"uploaded_at"},
        {"name":"Duplicate?","id":"duplicate_warning"},
    ]
    new_table = dash_table.DataTable(
        id="upload-history-table",
        columns=hist_cols, data=hist.to_dict("records"), page_size=10,
        style_table={"overflowX":"auto"},
        style_cell={"fontSize":"0.78rem","padding":"6px 10px","textAlign":"left"},
        style_header={"backgroundColor":"#f0f8f4","fontWeight":"bold","color":C_GREEN},
        style_data_conditional=[{"if":{"filter_query":"{duplicate_warning} = 1"},
            "backgroundColor":"#fff3cd","color":"#856404"}],
    )
    return version + 1, msg, new_table, None, None, {"display":"none"}, ""


# ── Run ───────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n  MedStar Pharmacy Analytics")
    print("   Open: http://127.0.0.1:8050\n")
    app.run(debug=True, host="127.0.0.1", port=8050)
