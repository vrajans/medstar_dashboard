"""
domain_tabs.py  —  Domain-Adaptive Tab Content Renderers
=========================================================

Renders dashboard tab content for non-pharmacy domains (SaaS, Retail,
Accounting, Generic).  Pharmacy tabs are still served by the existing
gst_report.py / yoy_report.py / expiry_dashboard.py modules.

Each renderer receives:
    sales_df      — primary DataFrame (revenue / income / orders)
    purchase_df   — secondary DataFrame (costs / expenses / COGS)
    kpi_data      — dict of aggregated KPIs
    domain        — domain key string  ("saas" | "retail" | "accounting" | "generic")
    language      — "English" | "Tamil" | "Hindi"

Public functions
----------------
    render_domain_overview(sales_df, purchase_df, kpi_data, domain, language)
    render_domain_revenue(sales_df, kpi_data, domain, language)
    render_domain_costs(purchase_df, kpi_data, domain, language)
    render_domain_customers(sales_df, domain, language)
    render_domain_cashflow(sales_df, purchase_df, domain, language)
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from dash import html, dcc
import dash_bootstrap_components as dbc

from domain_config import get_domain_config, get_currency_symbol

# ── colour palette ──────────────────────────────────────────────────────────
_DOMAIN_COLORS = {
    "pharmacy":   "#1e7e4b",
    "saas":       "#0d6efd",
    "retail":     "#fd7e14",
    "accounting": "#6f42c1",
    "generic":    "#6b7280",
}


def _domain_color(domain: str) -> str:
    return _DOMAIN_COLORS.get(domain, "#6b7280")


def _fmt_currency(value: float, symbol: str = "₹") -> str:
    if value >= 1_000_000:
        return f"{symbol}{value/1_000_000:.2f}M"
    if value >= 1_000:
        return f"{symbol}{value/1_000:.1f}K"
    return f"{symbol}{value:.0f}"


def _card(title: str, value: str, subtitle: str = "", color: str = "#1e7e4b") -> dbc.Card:
    """Minimal KPI card."""
    return dbc.Card([
        dbc.CardBody([
            html.P(title, className="text-muted mb-1", style={"fontSize": "0.8rem"}),
            html.H4(value, style={"color": color, "fontWeight": "700"}),
            html.P(subtitle, className="text-muted mb-0", style={"fontSize": "0.75rem"}),
        ])
    ], className="shadow-sm mb-3")


def _no_data_placeholder(domain: str) -> html.Div:
    cfg = get_domain_config(domain)
    return html.Div([
        html.Div(cfg["icon"], style={"fontSize": "3rem", "textAlign": "center", "marginTop": "60px"}),
        html.P(
            f"No {cfg['label']} data loaded yet.",
            className="text-muted text-center mt-3",
        ),
        html.P(
            "Upload your data using the Upload Data tab to see your analytics here.",
            className="text-muted text-center",
            style={"fontSize": "0.9rem"},
        ),
    ])


# ---------------------------------------------------------------------------
# 1. OVERVIEW  (universal — works for any domain)
# ---------------------------------------------------------------------------

def render_domain_overview(
    sales_df: pd.DataFrame | None,
    purchase_df: pd.DataFrame | None,
    kpi_data: dict,
    domain: str = "generic",
    language: str = "English",
) -> html.Div:
    """
    Universal overview tab.  Shows KPI cards + revenue trend + cost breakdown.
    Adapts labels and colour to the detected domain.
    """
    cfg    = get_domain_config(domain)
    color  = _domain_color(domain)
    labels = cfg["kpi_labels"]
    symbol = get_currency_symbol(domain)

    if sales_df is None or sales_df.empty:
        return _no_data_placeholder(domain)

    # --- KPI cards -----------------------------------------------------------
    revenue      = kpi_data.get("sales", 0)
    cost         = kpi_data.get("purchases", 0)
    margin       = kpi_data.get("margin", 0)
    transactions = kpi_data.get("bills", 0)
    top_entity   = kpi_data.get("top_branch", "—")

    kpi_cards = dbc.Row([
        dbc.Col(_card(labels["revenue"],      _fmt_currency(revenue, symbol), color=color),  md=2),
        dbc.Col(_card(labels["cost"],         _fmt_currency(cost,    symbol), color="#dc3545"), md=2),
        dbc.Col(_card(labels["margin"],       f"{margin:.1f}%",               color=color),  md=2),
        dbc.Col(_card(labels["transactions"], f"{transactions:,}",            color="#0d6efd"), md=2),
        dbc.Col(_card(labels["top_entity"],   str(top_entity),                color=color),  md=2),
    ], className="mb-4")

    # --- Revenue trend chart -------------------------------------------------
    date_col   = cfg["date_col"]
    amount_col = cfg["amount_col"]

    trend_fig = go.Figure()
    amt_col_actual = None
    for candidate in [amount_col, "net_amount", "amount", "revenue", "total", "sales"]:
        if candidate in sales_df.columns:
            amt_col_actual = candidate
            break

    date_col_actual = None
    for candidate in [date_col, "bill_date", "date", "month", "period", "created_at"]:
        if candidate in sales_df.columns:
            date_col_actual = candidate
            break

    if date_col_actual and amt_col_actual:
        try:
            tmp = sales_df.copy()
            tmp[date_col_actual] = pd.to_datetime(tmp[date_col_actual], errors="coerce")
            monthly = (
                tmp.dropna(subset=[date_col_actual])
                   .set_index(date_col_actual)[amt_col_actual]
                   .resample("ME").sum()
                   .reset_index()
            )
            monthly.columns = ["month", "value"]
            trend_fig.add_trace(go.Bar(
                x=monthly["month"], y=monthly["value"],
                marker_color=color, name=labels["revenue"],
            ))
            trend_fig.add_trace(go.Scatter(
                x=monthly["month"], y=monthly["value"],
                mode="lines+markers", line=dict(color=color, width=2),
                showlegend=False,
            ))
        except Exception:
            pass

    trend_fig.update_layout(
        title=f"{labels['revenue']} — Monthly Trend",
        xaxis_title="Month", yaxis_title=labels["revenue"],
        plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=40, r=20, t=50, b=40),
        height=320,
    )

    # --- Revenue vs Cost comparison ------------------------------------------
    compare_fig = go.Figure()
    if amt_col_actual and date_col_actual:
        try:
            cost_col_actual = None
            if purchase_df is not None and not purchase_df.empty:
                for candidate in [cfg["cost_col"], "net_amount", "amount", "cost", "expense"]:
                    if candidate in purchase_df.columns:
                        cost_col_actual = candidate
                        break

            date_col_purch = None
            if purchase_df is not None:
                for candidate in ["grn_date", "date", "bill_date", "month", "period"]:
                    if candidate in purchase_df.columns:
                        date_col_purch = candidate
                        break

            rev_monthly = (
                sales_df.copy()
                .assign(**{date_col_actual: pd.to_datetime(sales_df[date_col_actual], errors="coerce")})
                .dropna(subset=[date_col_actual])
                .set_index(date_col_actual)[amt_col_actual]
                .resample("ME").sum()
                .reset_index()
            )
            rev_monthly.columns = ["month", "revenue"]

            compare_fig.add_trace(go.Bar(
                x=rev_monthly["month"], y=rev_monthly["revenue"],
                name=labels["revenue"], marker_color=color,
            ))

            if purchase_df is not None and cost_col_actual and date_col_purch:
                cost_monthly = (
                    purchase_df.copy()
                    .assign(**{date_col_purch: pd.to_datetime(purchase_df[date_col_purch], errors="coerce")})
                    .dropna(subset=[date_col_purch])
                    .set_index(date_col_purch)[cost_col_actual]
                    .resample("ME").sum()
                    .reset_index()
                )
                cost_monthly.columns = ["month", "cost"]
                compare_fig.add_trace(go.Bar(
                    x=cost_monthly["month"], y=cost_monthly["cost"],
                    name=labels["cost"], marker_color="#dc3545",
                ))
        except Exception:
            pass

    compare_fig.update_layout(
        title=f"{labels['revenue']} vs {labels['cost']}",
        barmode="group", plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=40, r=20, t=50, b=40), height=320,
    )

    return html.Div([
        # Domain badge
        html.Div([
            html.Span(f"{cfg['icon']} {cfg['label']}", style={
                "background": color, "color": "white",
                "padding": "4px 12px", "borderRadius": "12px",
                "fontSize": "0.8rem", "fontWeight": "600",
            }),
            html.Span(" Domain detected — dashboard adapted automatically",
                      className="text-muted ms-2", style={"fontSize": "0.85rem"}),
        ], className="mb-3"),

        kpi_cards,

        dbc.Row([
            dbc.Col(dcc.Graph(figure=trend_fig),   md=6),
            dbc.Col(dcc.Graph(figure=compare_fig), md=6),
        ]),
    ], style={"padding": "16px"})


# ---------------------------------------------------------------------------
# 2. REVENUE / INCOME
# ---------------------------------------------------------------------------

def render_domain_revenue(
    sales_df: pd.DataFrame | None,
    kpi_data: dict,
    domain: str = "generic",
    language: str = "English",
) -> html.Div:
    cfg    = get_domain_config(domain)
    color  = _domain_color(domain)
    labels = cfg["kpi_labels"]
    symbol = get_currency_symbol(domain)

    if sales_df is None or sales_df.empty:
        return _no_data_placeholder(domain)

    # find best columns
    amount_col = None
    for c in [cfg["amount_col"], "net_amount", "amount", "revenue", "total"]:
        if c in sales_df.columns:
            amount_col = c
            break

    date_col = None
    for c in [cfg["date_col"], "bill_date", "date", "month", "period"]:
        if c in sales_df.columns:
            date_col = c
            break

    group_col = None
    for c in [cfg["group_col"], "branch_name", "product", "category", "account"]:
        if c in sales_df.columns:
            group_col = c
            break

    figs = []

    # Monthly trend
    if date_col and amount_col:
        try:
            tmp = sales_df.copy()
            tmp[date_col] = pd.to_datetime(tmp[date_col], errors="coerce")
            monthly = tmp.dropna(subset=[date_col]).set_index(date_col)[amount_col].resample("ME").sum().reset_index()
            monthly.columns = ["month", "value"]
            fig = go.Figure([go.Bar(x=monthly["month"], y=monthly["value"], marker_color=color)])
            fig.update_layout(title=f"{labels['revenue']} by Month", height=300,
                              plot_bgcolor="white", paper_bgcolor="white",
                              margin=dict(l=40, r=20, t=50, b=40))
            figs.append(dcc.Graph(figure=fig))
        except Exception:
            pass

    # Top categories breakdown
    if group_col and amount_col:
        try:
            top = sales_df.groupby(group_col)[amount_col].sum().nlargest(10).reset_index()
            top.columns = ["group", "value"]
            fig2 = go.Figure([go.Bar(
                x=top["value"], y=top["group"], orientation="h", marker_color=color,
            )])
            fig2.update_layout(
                title=f"Top {labels['top_entity']} by {labels['revenue']}",
                height=320, plot_bgcolor="white", paper_bgcolor="white",
                margin=dict(l=120, r=20, t=50, b=40),
                yaxis=dict(autorange="reversed"),
            )
            figs.append(dcc.Graph(figure=fig2))
        except Exception:
            pass

    if not figs:
        return _no_data_placeholder(domain)

    return html.Div([
        html.H5(f"{cfg['icon']} {labels['revenue']} Analysis",
                style={"color": color, "fontWeight": "700", "marginBottom": "16px"}),
        dbc.Row([dbc.Col(f, md=6) for f in figs]),
    ], style={"padding": "16px"})


# ---------------------------------------------------------------------------
# 3. COSTS / EXPENSES
# ---------------------------------------------------------------------------

def render_domain_costs(
    purchase_df: pd.DataFrame | None,
    kpi_data: dict,
    domain: str = "generic",
    language: str = "English",
) -> html.Div:
    cfg    = get_domain_config(domain)
    color  = "#dc3545"
    labels = cfg["kpi_labels"]
    symbol = get_currency_symbol(domain)

    if purchase_df is None or purchase_df.empty:
        return _no_data_placeholder(domain)

    cost_col = None
    for c in [cfg["cost_col"], "net_amount", "amount", "cost", "expense", "total"]:
        if c in purchase_df.columns:
            cost_col = c
            break

    date_col = None
    for c in ["grn_date", cfg["date_col"], "date", "month", "period"]:
        if c in purchase_df.columns:
            date_col = c
            break

    group_col = None
    for c in ["supplier_name", "vendor", cfg["group_col"], "category", "account"]:
        if c in purchase_df.columns:
            group_col = c
            break

    figs = []

    if date_col and cost_col:
        try:
            tmp = purchase_df.copy()
            tmp[date_col] = pd.to_datetime(tmp[date_col], errors="coerce")
            monthly = tmp.dropna(subset=[date_col]).set_index(date_col)[cost_col].resample("ME").sum().reset_index()
            monthly.columns = ["month", "value"]
            fig = go.Figure([go.Bar(x=monthly["month"], y=monthly["value"], marker_color=color)])
            fig.update_layout(title=f"{labels['cost']} by Month", height=300,
                              plot_bgcolor="white", paper_bgcolor="white",
                              margin=dict(l=40, r=20, t=50, b=40))
            figs.append(dcc.Graph(figure=fig))
        except Exception:
            pass

    if group_col and cost_col:
        try:
            top = purchase_df.groupby(group_col)[cost_col].sum().nlargest(10).reset_index()
            top.columns = ["group", "value"]
            fig2 = go.Figure([go.Bar(
                x=top["value"], y=top["group"], orientation="h", marker_color=color,
            )])
            fig2.update_layout(
                title=f"Top Cost Sources",
                height=320, plot_bgcolor="white", paper_bgcolor="white",
                margin=dict(l=120, r=20, t=50, b=40),
                yaxis=dict(autorange="reversed"),
            )
            figs.append(dcc.Graph(figure=fig2))
        except Exception:
            pass

    if not figs:
        return _no_data_placeholder(domain)

    return html.Div([
        html.H5(f"💸 {labels['cost']} Analysis",
                style={"color": color, "fontWeight": "700", "marginBottom": "16px"}),
        dbc.Row([dbc.Col(f, md=6) for f in figs]),
    ], style={"padding": "16px"})


# ---------------------------------------------------------------------------
# 4. CUSTOMERS  (SaaS / Retail)
# ---------------------------------------------------------------------------

def render_domain_customers(
    sales_df: pd.DataFrame | None,
    domain: str = "generic",
    language: str = "English",
) -> html.Div:
    cfg   = get_domain_config(domain)
    color = _domain_color(domain)

    if sales_df is None or sales_df.empty:
        return _no_data_placeholder(domain)

    # Try to find customer / patient / entity column
    customer_col = None
    for c in ["customer_name", "customer", "client", "patient", "user", "account"]:
        if c in sales_df.columns:
            customer_col = c
            break

    amount_col = None
    for c in [cfg["amount_col"], "net_amount", "amount", "revenue", "total"]:
        if c in sales_df.columns:
            amount_col = c
            break

    figs = []

    if customer_col and amount_col:
        try:
            top = (
                sales_df.groupby(customer_col)[amount_col]
                .sum().nlargest(15).reset_index()
            )
            top.columns = ["customer", "value"]
            fig = go.Figure([go.Bar(
                x=top["value"], y=top["customer"], orientation="h", marker_color=color,
            )])
            fig.update_layout(
                title=f"Top Customers by {cfg['kpi_labels']['revenue']}",
                height=400, plot_bgcolor="white", paper_bgcolor="white",
                margin=dict(l=140, r=20, t=50, b=40),
                yaxis=dict(autorange="reversed"),
            )
            figs.append(dcc.Graph(figure=fig))
        except Exception:
            pass

    # Transaction count per customer
    if customer_col:
        try:
            txn_count = sales_df.groupby(customer_col).size().nlargest(10).reset_index()
            txn_count.columns = ["customer", "count"]
            fig2 = px.pie(txn_count, values="count", names="customer",
                          title="Transaction Share by Customer",
                          color_discrete_sequence=px.colors.qualitative.Set2)
            fig2.update_layout(height=380, paper_bgcolor="white",
                               margin=dict(l=20, r=20, t=50, b=20))
            figs.append(dcc.Graph(figure=fig2))
        except Exception:
            pass

    if not figs:
        return html.Div([
            html.P("No customer column detected in your data.",
                   className="text-muted text-center mt-5"),
            html.P("Add a 'customer_name' or 'customer' column to unlock this tab.",
                   className="text-muted text-center", style={"fontSize": "0.85rem"}),
        ])

    return html.Div([
        html.H5(f"👥 Customer Analysis",
                style={"color": color, "fontWeight": "700", "marginBottom": "16px"}),
        dbc.Row([dbc.Col(f, md=6) for f in figs]),
    ], style={"padding": "16px"})


# ---------------------------------------------------------------------------
# 5. CASH FLOW  (Accounting)
# ---------------------------------------------------------------------------

def render_domain_cashflow(
    sales_df: pd.DataFrame | None,
    purchase_df: pd.DataFrame | None,
    domain: str = "accounting",
    language: str = "English",
) -> html.Div:
    cfg   = get_domain_config(domain)
    color = _domain_color(domain)

    if sales_df is None or sales_df.empty:
        return _no_data_placeholder(domain)

    date_col   = None
    amount_col = None
    for c in [cfg["date_col"], "bill_date", "date", "month", "period"]:
        if c in sales_df.columns:
            date_col = c
            break
    for c in [cfg["amount_col"], "net_amount", "amount", "revenue", "total"]:
        if c in sales_df.columns:
            amount_col = c
            break

    if not (date_col and amount_col):
        return _no_data_placeholder(domain)

    try:
        tmp = sales_df.copy()
        tmp[date_col] = pd.to_datetime(tmp[date_col], errors="coerce")
        income = tmp.dropna(subset=[date_col]).set_index(date_col)[amount_col].resample("ME").sum()

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=income.index, y=income.values,
            name="Income", marker_color=color,
        ))

        if purchase_df is not None and not purchase_df.empty:
            cost_col = None
            cost_date = None
            for c in ["grn_date", "date", "month"]:
                if c in purchase_df.columns:
                    cost_date = c
                    break
            for c in ["net_amount", "amount", "cost", "expense"]:
                if c in purchase_df.columns:
                    cost_col = c
                    break

            if cost_col and cost_date:
                tmp2 = purchase_df.copy()
                tmp2[cost_date] = pd.to_datetime(tmp2[cost_date], errors="coerce")
                expense = tmp2.dropna(subset=[cost_date]).set_index(cost_date)[cost_col].resample("ME").sum()
                net_cf  = income.subtract(expense, fill_value=0)

                fig.add_trace(go.Bar(
                    x=expense.index, y=[-v for v in expense.values],
                    name="Expenses", marker_color="#dc3545",
                ))
                fig.add_trace(go.Scatter(
                    x=net_cf.index, y=net_cf.values,
                    mode="lines+markers", name="Net Cash Flow",
                    line=dict(color="#ffc107", width=2),
                ))

        fig.update_layout(
            title="Monthly Cash Flow (Income vs Expenses)",
            barmode="relative", plot_bgcolor="white", paper_bgcolor="white",
            margin=dict(l=40, r=20, t=50, b=40), height=380,
        )

        return html.Div([
            html.H5("💵 Cash Flow Analysis",
                    style={"color": color, "fontWeight": "700", "marginBottom": "16px"}),
            dcc.Graph(figure=fig),
        ], style={"padding": "16px"})

    except Exception as exc:
        return html.Div(
            html.P(f"Could not render cash flow chart: {exc}", className="text-danger p-3")
        )


# ---------------------------------------------------------------------------
# 6. DOMAIN INVENTORY  (Retail)
# ---------------------------------------------------------------------------

def render_domain_inventory(
    purchase_df: pd.DataFrame | None,
    domain: str = "retail",
    language: str = "English",
) -> html.Div:
    cfg   = get_domain_config(domain)
    color = _domain_color(domain)

    if purchase_df is None or purchase_df.empty:
        return _no_data_placeholder(domain)

    item_col = None
    for c in [cfg["item_col"], "product_name", "sku", "item", "drug_name"]:
        if c in purchase_df.columns:
            item_col = c
            break

    qty_col = None
    for c in ["quantity", "qty", "units", "stock"]:
        if c in purchase_df.columns:
            qty_col = c
            break

    cost_col = None
    for c in [cfg["cost_col"], "net_amount", "amount", "cost"]:
        if c in purchase_df.columns:
            cost_col = c
            break

    if not item_col:
        return _no_data_placeholder(domain)

    figs = []

    if qty_col:
        try:
            top = purchase_df.groupby(item_col)[qty_col].sum().nlargest(12).reset_index()
            top.columns = ["item", "qty"]
            fig = go.Figure([go.Bar(
                x=top["qty"], y=top["item"], orientation="h", marker_color=color,
            )])
            fig.update_layout(
                title="Top Items by Quantity",
                height=350, plot_bgcolor="white", paper_bgcolor="white",
                margin=dict(l=140, r=20, t=50, b=40),
                yaxis=dict(autorange="reversed"),
            )
            figs.append(dcc.Graph(figure=fig))
        except Exception:
            pass

    if cost_col:
        try:
            top2 = purchase_df.groupby(item_col)[cost_col].sum().nlargest(12).reset_index()
            top2.columns = ["item", "value"]
            fig2 = px.pie(top2, values="value", names="item",
                          title="Inventory Cost Distribution",
                          color_discrete_sequence=px.colors.qualitative.Set3)
            fig2.update_layout(height=350, paper_bgcolor="white",
                               margin=dict(l=20, r=20, t=50, b=20))
            figs.append(dcc.Graph(figure=fig2))
        except Exception:
            pass

    if not figs:
        return _no_data_placeholder(domain)

    return html.Div([
        html.H5("📦 Inventory Analysis",
                style={"color": color, "fontWeight": "700", "marginBottom": "16px"}),
        dbc.Row([dbc.Col(f, md=6) for f in figs]),
    ], style={"padding": "16px"})


# ---------------------------------------------------------------------------
# 7. DOMAIN-AWARE TAB ROUTER
# ---------------------------------------------------------------------------

def render_domain_tab(
    tab_id: str,
    sales_df: pd.DataFrame | None,
    purchase_df: pd.DataFrame | None,
    kpi_data: dict,
    domain: str,
    language: str = "English",
) -> html.Div:
    """
    Single entry point.  Given a tab_id and domain, route to the correct renderer.
    Called by app.py when the domain is NOT 'pharmacy'.
    """
    if tab_id == "overview":
        return render_domain_overview(sales_df, purchase_df, kpi_data, domain, language)
    elif tab_id == "revenue":
        return render_domain_revenue(sales_df, kpi_data, domain, language)
    elif tab_id == "costs":
        return render_domain_costs(purchase_df, kpi_data, domain, language)
    elif tab_id == "customers":
        return render_domain_customers(sales_df, domain, language)
    elif tab_id == "cashflow":
        return render_domain_cashflow(sales_df, purchase_df, domain, language)
    elif tab_id == "inventory":
        return render_domain_inventory(purchase_df, domain, language)
    else:
        # AI Chat, Upload, YOY — shared across all domains, handled in app.py
        return html.Div()
