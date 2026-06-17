"""
expiry_dashboard.py  —  InsightHub Expiry + Stock + Cash/Credit Tabs
=====================================================================
Three Dash tab layouts:

  render_expiry_tab(engine, tenant_id)     → Expiry alerts view
  render_stock_tab(engine, tenant_id)      → Low-stock / inventory view
  render_cash_credit_tab(sales_df, ...)    → Cash vs credit split analytics

For Phase 0 (MedStar internal), data is read from the tenant_inventory table.
For tenant users, a data-upload flow will populate the same table.
"""

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from dash import html, dcc, dash_table
import dash_bootstrap_components as dbc
from datetime import date, timedelta
from typing import Any, Optional

C_GREEN  = "#1e7e4b"
C_BLUE   = "#0d6efd"
C_ORANGE = "#fd7e14"
C_RED    = "#dc3545"
C_TEAL   = "#17a2b8"
C_PURPLE = "#6f42c1"
C_GRAY   = "#6b7280"
C_YELLOW = "#ffc107"

CHART_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor ="rgba(0,0,0,0)",
    font=dict(family="Inter,Arial,sans-serif", size=11),
    margin=dict(l=40, r=20, t=30, b=40),
)


def _fmt(v: float, currency: str = "INR") -> str:
    if currency == "USD":
        if v >= 1e6: return f"${v/1e6:.2f}M"
        if v >= 1e3: return f"${v/1e3:.1f}K"
        return f"${v:.0f}"
    if v >= 1e7: return f"₹{v/1e7:.2f}Cr"
    if v >= 1e5: return f"₹{v/1e5:.2f}L"
    if v >= 1e3: return f"₹{v/1e3:.1f}K"
    return f"₹{v:.0f}"


def _status_badge(status: str) -> html.Span:
    colors = {
        "expired":  (C_RED,    "#fff"),
        "critical": (C_ORANGE, "#fff"),
        "warning":  (C_YELLOW, "#333"),
        "ok":       (C_GREEN,  "#fff"),
    }
    bg, fg = colors.get(status, ("#e2e8f0", "#333"))
    return html.Span(
        status.upper(),
        style={"background": bg, "color": fg, "borderRadius": "4px",
               "padding": "2px 8px", "fontSize": "0.68rem", "fontWeight": 700},
    )


# ═════════════════════════════════════════════════════════════════════════════
# EXPIRY DASHBOARD
# ═════════════════════════════════════════════════════════════════════════════

def _load_inventory(engine: Any, tenant_id: Optional[int] = None) -> pd.DataFrame:
    """Load tenant_inventory from DB."""
    if engine is None:
        return pd.DataFrame()
    try:
        from sqlalchemy import text
        query = "SELECT * FROM tenant_inventory"
        params = {}
        if tenant_id:
            query += " WHERE tenant_id = :tid"
            params = {"tid": tenant_id}
        with engine.connect() as conn:
            df = pd.read_sql_query(text(query), conn, params=params)
        if "expiry_date" in df.columns:
            df["expiry_date"] = pd.to_datetime(df["expiry_date"], errors="coerce")
        return df
    except Exception:
        return pd.DataFrame()


def _classify_expiry(row: pd.Series, today: date) -> str:
    exp = row.get("expiry_date")
    if pd.isna(exp): return "ok"
    exp_date = exp.date() if hasattr(exp, "date") else exp
    days_left = (exp_date - today).days
    if days_left < 0:   return "expired"
    if days_left <= 30: return "critical"
    if days_left <= 90: return "warning"
    return "ok"


def render_expiry_tab(
    engine:    Any,
    tenant_id: Optional[int] = None,
    currency:  str = "INR",
) -> html.Div:
    """Render the Expiry Dashboard."""
    today = date.today()
    inv   = _load_inventory(engine, tenant_id)

    if inv.empty:
        return _empty_inventory_state("Expiry Dashboard",
            "Upload your inventory with expiry dates to see alerts.")

    inv["_status"] = inv.apply(lambda r: _classify_expiry(r, today), axis=1)

    expired  = inv[inv["_status"] == "expired"]
    critical = inv[inv["_status"] == "critical"]   # ≤ 30 days
    warning  = inv[inv["_status"] == "warning"]    # ≤ 90 days

    # ── Summary KPIs ──
    exp_val  = float(expired["stock_value"].fillna(0).sum())
    crit_val = float(critical["stock_value"].fillna(0).sum())
    warn_val = float(warning["stock_value"].fillna(0).sum())

    kpi_row = html.Div([
        _exp_kpi("Already Expired",       len(expired),  exp_val,  C_RED,    currency),
        _exp_kpi("Expiring ≤ 30 Days",    len(critical), crit_val, C_ORANGE, currency),
        _exp_kpi("Expiring ≤ 90 Days",    len(warning),  warn_val, C_YELLOW, currency),
        _exp_kpi("Total Items Tracked",   len(inv),
                 float(inv["stock_value"].fillna(0).sum()), C_GREEN, currency),
    ], style={"display":"grid",
              "gridTemplateColumns":"repeat(auto-fill, minmax(180px, 1fr))",
              "gap":"0.75rem","marginBottom":"1.2rem"})

    # ── Expiry bucket bar chart ──
    buckets = [
        ("Expired",      len(expired),  C_RED),
        ("< 30 days",    len(critical), C_ORANGE),
        ("30–90 days",   len(warning),  C_YELLOW),
        ("90+ days",
         len(inv[inv["_status"]=="ok"]), C_GREEN),
    ]
    fig_bucket = go.Figure(go.Bar(
        x=[b[0] for b in buckets],
        y=[b[1] for b in buckets],
        marker_color=[b[2] for b in buckets],
        text=[b[1] for b in buckets],
        textposition="outside",
    ))
    fig_bucket.update_layout(**CHART_LAYOUT,
                              title_text="Inventory by Expiry Status",
                              title_font_size=13)

    bucket_chart = html.Div([
        dcc.Graph(figure=fig_bucket, config={"displayModeBar":False}),
    ], style={"background":"#fff","borderRadius":"10px",
              "padding":"0.75rem","boxShadow":"0 1px 4px rgba(0,0,0,0.07)",
              "marginBottom":"1.2rem"})

    # ── Critical items table (expiring ≤ 30 days) ──
    critical_display = pd.concat([expired, critical]).copy()
    if not critical_display.empty:
        critical_display["days_left"] = critical_display["expiry_date"].apply(
            lambda x: (x.date() - today).days if pd.notna(x) else None
        )
        critical_display = critical_display.sort_values("days_left", na_position="first")

    crit_table = _expiry_table(critical_display, currency,
                               title="⚠️ Critical — Expired & Expiring ≤ 30 Days")

    # ── Warning items table (31–90 days) ──
    warn_table = _expiry_table(warning.copy(), currency,
                               title="📅 Warning — Expiring 31–90 Days", collapsed=True)

    return html.Div([
        html.Div([
            html.H4("Expiry Dashboard", style={"margin":0,"fontWeight":700,"color":C_RED}),
            html.Span(f"As of {today.strftime('%d %b %Y')} · {len(inv):,} items tracked",
                      style={"fontSize":"0.78rem","color":C_GRAY}),
        ], style={"marginBottom":"1rem"}),
        kpi_row,
        bucket_chart,
        crit_table,
        warn_table,
    ])


def _exp_kpi(label: str, count: int, value: float, color: str, currency: str) -> html.Div:
    return html.Div([
        html.Div(label, style={"fontSize":"0.7rem","color":C_GRAY,"fontWeight":600,
                               "textTransform":"uppercase","letterSpacing":"0.04em"}),
        html.Div(str(count), style={"fontSize":"1.5rem","fontWeight":700,"color":color}),
        html.Div(f"Value: {_fmt(value, currency)}",
                 style={"fontSize":"0.75rem","color":C_GRAY,"marginTop":"2px"}),
    ], style={"background":"#fff","borderRadius":"10px",
              "padding":"1rem 1.2rem","boxShadow":"0 1px 4px rgba(0,0,0,0.07)",
              "borderLeft":f"4px solid {color}"})


def _expiry_table(df: pd.DataFrame, currency: str,
                  title: str = "", collapsed: bool = False) -> html.Div:
    if df.empty:
        return html.Div(
            html.Div([html.Span(title), html.Span(" — None", style={"color":C_GREEN,"marginLeft":"4px"})],
                     style={"fontWeight":600,"fontSize":"0.88rem","padding":"0.75rem 1.2rem",
                            "background":"#fff","borderRadius":"10px",
                            "boxShadow":"0 1px 4px rgba(0,0,0,0.07)","marginBottom":"0.75rem"}))

    rows = []
    for _, r in df.head(30).iterrows():
        exp     = r.get("expiry_date")
        exp_str = exp.strftime("%d %b %Y") if pd.notna(exp) else "—"
        days    = r.get("days_left")
        if days is not None:
            days_str = f"{int(days)}d" if days >= 0 else f"EXPIRED {abs(int(days))}d ago"
        else:
            days_str = "—"
        rows.append(html.Tr([
            html.Td(str(r.get("item_name",""))[:35], style={"fontSize":"0.78rem"}),
            html.Td(str(r.get("batch_no","—")),      style={"fontSize":"0.78rem"}),
            html.Td(exp_str,                          style={"fontSize":"0.78rem"}),
            html.Td(days_str,                         style={"fontSize":"0.78rem","fontWeight":600,
                                                             "color":C_RED if (days is not None and days<0) else C_ORANGE}),
            html.Td(str(int(r.get("quantity",0))),    style={"textAlign":"right","fontSize":"0.78rem"}),
            html.Td(_fmt(float(r.get("stock_value",0)), currency),
                    style={"textAlign":"right","fontSize":"0.78rem"}),
        ]))

    content = html.Div([
        html.Div([
            html.Table([
                html.Thead(html.Tr([
                    html.Th("Item"),html.Th("Batch"),html.Th("Expiry Date"),
                    html.Th("Days Left"),
                    html.Th("Qty",  style={"textAlign":"right"}),
                    html.Th("Value",style={"textAlign":"right"}),
                ])),
                html.Tbody(rows),
            ], style={"width":"100%","borderCollapse":"collapse","fontSize":"0.8rem"}),
        ], style={"overflowX":"auto"}),
    ])

    return html.Div([
        html.Div(title, style={"fontWeight":700,"fontSize":"0.88rem",
                               "marginBottom":"0.75rem"}),
        content,
    ], style={"background":"#fff","borderRadius":"10px",
              "padding":"1.2rem","boxShadow":"0 1px 4px rgba(0,0,0,0.07)",
              "marginBottom":"1rem"})


# ═════════════════════════════════════════════════════════════════════════════
# STOCK LEVEL DASHBOARD
# ═════════════════════════════════════════════════════════════════════════════

def render_stock_tab(
    engine:    Any,
    tenant_id: Optional[int] = None,
    currency:  str = "INR",
) -> html.Div:
    """Render the Stock Level / Inventory dashboard."""
    inv = _load_inventory(engine, tenant_id)

    if inv.empty:
        return _empty_inventory_state("Stock Level Dashboard",
            "Upload your inventory with reorder levels to see low-stock alerts.")

    has_reorder = "reorder_level" in inv.columns

    low_stock = pd.DataFrame()
    if has_reorder:
        inv["reorder_level"] = pd.to_numeric(inv["reorder_level"], errors="coerce").fillna(0)
        inv["quantity"]      = pd.to_numeric(inv["quantity"],      errors="coerce").fillna(0)
        low_stock = inv[inv["quantity"] < inv["reorder_level"]].copy()
        low_stock["deficit"] = low_stock["reorder_level"] - low_stock["quantity"]
        low_stock = low_stock.sort_values("deficit", ascending=False)

    total_items = len(inv)
    total_value = float(inv["stock_value"].fillna(0).sum()) if "stock_value" in inv.columns else 0
    low_count   = len(low_stock)
    low_value   = float(low_stock["stock_value"].fillna(0).sum()) if not low_stock.empty else 0

    kpi_row = html.Div([
        _stock_kpi("Total Items",         total_items,   "",                        C_GREEN),
        _stock_kpi("Total Stock Value",   "",            _fmt(total_value, currency), C_BLUE),
        _stock_kpi("Below Reorder Level", low_count,    "",                        C_RED),
        _stock_kpi("At-Risk Value",       "",            _fmt(low_value, currency), C_ORANGE),
    ], style={"display":"grid",
              "gridTemplateColumns":"repeat(auto-fill, minmax(175px, 1fr))",
              "gap":"0.75rem","marginBottom":"1.2rem"})

    # ── Top low-stock items bar chart ──
    fig_low = html.Div()
    if not low_stock.empty:
        top = low_stock.head(15)
        fig = go.Figure(go.Bar(
            x=top.get("item_name", top.index),
            y=top.get("deficit", []),
            marker_color=C_RED,
            text=[f"Need {int(d)}" for d in top.get("deficit",[])],
            textposition="outside",
        ))
        fig.update_layout(
            **CHART_LAYOUT,
            title_text="Top 15 Items — Units Needed to Reach Reorder Level",
            title_font_size=13,
            xaxis_tickangle=-35,
        )
        fig_low = html.Div([dcc.Graph(figure=fig, config={"displayModeBar":False})],
                           style={"background":"#fff","borderRadius":"10px",
                                  "padding":"0.75rem","boxShadow":"0 1px 4px rgba(0,0,0,0.07)",
                                  "marginBottom":"1.2rem"})

    # ── Low stock table ──
    stock_table = html.Div()
    if not low_stock.empty:
        rows = []
        for _, r in low_stock.head(30).iterrows():
            curr   = float(r.get("quantity", 0))
            reord  = float(r.get("reorder_level", 0))
            defict = float(r.get("deficit", 0))
            pct    = curr / reord * 100 if reord > 0 else 0
            rows.append(html.Tr([
                html.Td(str(r.get("item_name",""))[:40], style={"fontSize":"0.78rem"}),
                html.Td(str(r.get("unit","units")),      style={"fontSize":"0.78rem"}),
                html.Td(f"{curr:.0f}",   style={"textAlign":"right","fontSize":"0.78rem",
                                                 "color":C_RED,"fontWeight":600}),
                html.Td(f"{reord:.0f}",  style={"textAlign":"right","fontSize":"0.78rem"}),
                html.Td(f"{defict:.0f}", style={"textAlign":"right","fontSize":"0.78rem",
                                                 "color":C_ORANGE,"fontWeight":600}),
                html.Td(f"{pct:.0f}%",   style={"textAlign":"right","fontSize":"0.78rem",
                                                  "color":C_RED if pct<50 else C_ORANGE}),
            ]))

        stock_table = html.Div([
            html.Div("📦 Items Below Reorder Level", style={
                "fontWeight":700,"fontSize":"0.88rem","marginBottom":"0.75rem"}),
            html.Div([
                html.Table([
                    html.Thead(html.Tr([
                        html.Th("Item"),html.Th("Unit"),
                        html.Th("Current", style={"textAlign":"right"}),
                        html.Th("Reorder",  style={"textAlign":"right"}),
                        html.Th("Deficit",  style={"textAlign":"right"}),
                        html.Th("% of Reorder", style={"textAlign":"right"}),
                    ])),
                    html.Tbody(rows),
                ], style={"width":"100%","borderCollapse":"collapse","fontSize":"0.8rem"}),
            ], style={"overflowX":"auto"}),
        ], style={"background":"#fff","borderRadius":"10px",
                  "padding":"1.2rem","boxShadow":"0 1px 4px rgba(0,0,0,0.07)"})

    return html.Div([
        html.Div([
            html.H4("Stock Level Dashboard", style={"margin":0,"fontWeight":700,"color":C_BLUE}),
            html.Span(f"{total_items:,} items · {low_count} below reorder level",
                      style={"fontSize":"0.78rem","color":C_GRAY}),
        ], style={"marginBottom":"1rem"}),
        kpi_row,
        fig_low,
        stock_table,
    ])


def _stock_kpi(label: str, count: Any, fmt_val: str, color: str) -> html.Div:
    display = str(count) if count != "" else fmt_val
    return html.Div([
        html.Div(label, style={"fontSize":"0.7rem","color":C_GRAY,"fontWeight":600,
                               "textTransform":"uppercase","letterSpacing":"0.04em"}),
        html.Div(display, style={"fontSize":"1.4rem","fontWeight":700,"color":color}),
    ], style={"background":"#fff","borderRadius":"10px",
              "padding":"1rem 1.2rem","boxShadow":"0 1px 4px rgba(0,0,0,0.07)",
              "borderTop":f"3px solid {color}"})


# ═════════════════════════════════════════════════════════════════════════════
# CASH vs CREDIT SPLIT ANALYTICS
# ═════════════════════════════════════════════════════════════════════════════

def render_cash_credit_tab(
    sales_df:   pd.DataFrame,
    branch:     str = "All",
    start_date        = None,
    end_date          = None,
    currency:   str = "INR",
) -> html.Div:
    """Render Cash vs Credit split analytics."""
    s = sales_df.copy()
    if branch and branch != "All" and "branch" in s.columns:
        s = s[s["branch"] == branch]
    if start_date and "bill_date" in s.columns:
        s = s[s["bill_date"] >= pd.to_datetime(start_date)]
    if end_date   and "bill_date" in s.columns:
        s = s[s["bill_date"] <= pd.to_datetime(end_date)]

    required = ["cash_sales","credit_sales","cash_bill_count","credit_bill_count"]
    has_data = not s.empty and all(c in s.columns for c in ["cash_sales","credit_sales"])

    if not has_data:
        return _empty_inventory_state("Cash vs Credit Split",
            "Upload sales data with cash_sales and credit_sales columns to see the split.")

    # Totals
    cash_amt   = float(s["cash_sales"].fillna(0).sum())
    credit_amt = float(s["credit_sales"].fillna(0).sum())
    card_amt   = float(s["card_sales"].fillna(0).sum()) if "card_sales" in s.columns else 0
    total_amt  = cash_amt + credit_amt + card_amt or 1

    cash_cnt   = int(s["cash_bill_count"].fillna(0).sum())   if "cash_bill_count"   in s.columns else 0
    credit_cnt = int(s["credit_bill_count"].fillna(0).sum()) if "credit_bill_count" in s.columns else 0
    card_cnt   = int(s["card_bill_count"].fillna(0).sum())   if "card_bill_count"   in s.columns else 0
    total_cnt  = cash_cnt + credit_cnt + card_cnt or 1

    # KPI row
    kpi_row = html.Div([
        _cash_kpi("Cash Sales",   cash_amt,   cash_cnt,   cash_amt/total_amt*100,   C_GREEN,  currency),
        _cash_kpi("Credit Sales", credit_amt, credit_cnt, credit_amt/total_amt*100, C_ORANGE, currency),
        _cash_kpi("Card Sales",   card_amt,   card_cnt,   card_amt/total_amt*100,   C_BLUE,   currency),
    ], style={"display":"grid",
              "gridTemplateColumns":"repeat(auto-fill, minmax(200px,1fr))",
              "gap":"0.75rem","marginBottom":"1.2rem"})

    # ── Donut chart ──
    labels  = ["Cash", "Credit", "Card"]
    values  = [cash_amt, credit_amt, card_amt]
    colors  = [C_GREEN, C_ORANGE, C_BLUE]
    fig_donut = go.Figure(go.Pie(
        labels=labels, values=values,
        hole=0.55,
        marker_colors=colors,
        textinfo="label+percent",
        hovertemplate="%{label}: " + ("₹" if currency=="INR" else "$") + "%{value:,.0f}<extra></extra>",
    ))
    fig_donut.update_layout(**CHART_LAYOUT, title_text="Sales by Payment Mode",
                             title_font_size=13,
                             showlegend=True,
                             legend=dict(orientation="v", x=1.0, y=0.5))

    # ── Monthly trend ──
    fig_trend = html.Div()
    if "bill_date" in s.columns:
        s_m = s.copy()
        s_m["month"] = pd.to_datetime(s_m["bill_date"], errors="coerce").dt.to_period("M").astype(str)
        cols = [c for c in ["cash_sales","credit_sales","card_sales","month"] if c in s_m.columns]
        grp  = s_m[cols].groupby("month").sum(numeric_only=True).reset_index().sort_values("month")

        fig = go.Figure()
        for col, color, name in [("cash_sales",C_GREEN,"Cash"),
                                  ("credit_sales",C_ORANGE,"Credit"),
                                  ("card_sales",C_BLUE,"Card")]:
            if col in grp.columns:
                fig.add_trace(go.Bar(x=grp["month"], y=grp[col],
                                     name=name, marker_color=color))
        fig.update_layout(**CHART_LAYOUT, barmode="stack",
                          title_text="Monthly Cash / Credit / Card Trend",
                          title_font_size=13,
                          legend=dict(orientation="h", y=-0.18))
        fig.update_yaxes(tickprefix="₹" if currency=="INR" else "$", tickformat=".2s")
        fig_trend = html.Div([dcc.Graph(figure=fig, config={"displayModeBar":False})],
                             style={"background":"#fff","borderRadius":"10px",
                                    "padding":"0.75rem","boxShadow":"0 1px 4px rgba(0,0,0,0.07)"})

    charts = html.Div([
        html.Div([dcc.Graph(figure=fig_donut, config={"displayModeBar":False})],
                 style={"flex":"0 0 340px","background":"#fff","borderRadius":"10px",
                        "padding":"0.75rem","boxShadow":"0 1px 4px rgba(0,0,0,0.07)"}),
        html.Div([fig_trend], style={"flex":1}),
    ], style={"display":"flex","gap":"1rem","marginBottom":"1.2rem"})

    # ── Cash-in-hand trend ──
    cih_chart = html.Div()
    if "cash_in_hand" in s.columns and "bill_date" in s.columns:
        s_c = s.copy()
        s_c["bill_date"] = pd.to_datetime(s_c["bill_date"], errors="coerce")
        s_c = s_c.sort_values("bill_date")
        fig_cih = go.Figure(go.Scatter(
            x=s_c["bill_date"], y=s_c["cash_in_hand"].fillna(0),
            mode="lines+markers",
            line=dict(color=C_GREEN, width=2),
            fill="tozeroy", fillcolor="rgba(30,126,75,0.08)",
            name="Cash in Hand",
        ))
        fig_cih.update_layout(**CHART_LAYOUT,
                               title_text="Daily Cash in Hand",
                               title_font_size=13)
        fig_cih.update_yaxes(tickprefix="₹" if currency=="INR" else "$", tickformat=".2s")
        cih_chart = html.Div([dcc.Graph(figure=fig_cih, config={"displayModeBar":False})],
                              style={"background":"#fff","borderRadius":"10px",
                                     "padding":"0.75rem","boxShadow":"0 1px 4px rgba(0,0,0,0.07)"})

    return html.Div([
        html.Div([
            html.H4("Cash vs Credit Split", style={"margin":0,"fontWeight":700,"color":C_GREEN}),
            html.Span("Payment mode analysis across all transactions",
                      style={"fontSize":"0.78rem","color":C_GRAY}),
        ], style={"marginBottom":"1rem"}),
        kpi_row,
        charts,
        cih_chart,
    ])


def _cash_kpi(label: str, amount: float, count: int, pct: float,
              color: str, currency: str) -> html.Div:
    return html.Div([
        html.Div(label, style={"fontSize":"0.7rem","color":C_GRAY,"fontWeight":600,
                               "textTransform":"uppercase","letterSpacing":"0.04em"}),
        html.Div(_fmt(amount, currency),
                 style={"fontSize":"1.4rem","fontWeight":700,"color":color}),
        html.Div([
            html.Span(f"{count:,} bills",  style={"fontSize":"0.75rem","color":C_GRAY}),
            html.Span(f" · {pct:.1f}%",   style={"fontSize":"0.75rem","color":color,
                                                   "fontWeight":600,"marginLeft":"4px"}),
        ], style={"marginTop":"2px"}),
    ], style={"background":"#fff","borderRadius":"10px",
              "padding":"1rem 1.2rem","boxShadow":"0 1px 4px rgba(0,0,0,0.07)",
              "borderLeft":f"4px solid {color}"})


# ═════════════════════════════════════════════════════════════════════════════
# SHARED EMPTY STATE
# ═════════════════════════════════════════════════════════════════════════════

def _empty_inventory_state(title: str, subtitle: str) -> html.Div:
    return html.Div([
        html.Div([
            html.Div("📦", style={"fontSize":"3rem","marginBottom":"0.5rem"}),
            html.H4(title, style={"color":C_BLUE,"fontWeight":700}),
            html.P(subtitle, style={"color":C_GRAY,"maxWidth":"400px",
                                    "margin":"0 auto","lineHeight":1.6,"fontSize":"0.88rem"}),
            html.Div([
                html.Div("Upload inventory CSV/Excel from the Upload Data tab.",
                         style={"fontSize":"0.8rem","color":"#94a3b8","marginTop":"1rem"}),
                html.Div([
                    html.Strong("Required columns: "),
                    html.Span("item_name, batch_no, expiry_date, quantity, reorder_level, stock_value",
                              style={"fontFamily":"monospace","fontSize":"0.78rem","color":C_BLUE}),
                ], style={"marginTop":"0.5rem","fontSize":"0.8rem","color":C_GRAY}),
            ]),
        ], style={"textAlign":"center","padding":"4rem 2rem"}),
    ], style={"minHeight":"50vh","display":"flex","alignItems":"center",
              "justifyContent":"center"})
