"""
gst_report.py  —  InsightHub GST Report Tab
============================================
Renders the GST tab content for the Dash app.

India path: reads SGST / CGST / IGST / total_gst columns from purchase_df
            and builds a GSTR-3B style summary table.

USA path:   placeholder for QuickBooks-sourced sales tax data by state.

Functions exported
------------------
  render_gst_tab(purchase_df, branch, start_date, end_date)   → Dash layout
"""

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from dash import html, dcc
import dash_bootstrap_components as dbc
from datetime import datetime

# ── Colour constants (same palette as app.py) ─────────────────────────────────
C_GREEN  = "#1e7e4b"
C_BLUE   = "#0d6efd"
C_ORANGE = "#fd7e14"
C_RED    = "#dc3545"
C_TEAL   = "#17a2b8"
C_PURPLE = "#6f42c1"

CHART_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor ="rgba(0,0,0,0)",
    font         = dict(family="Inter,Arial,sans-serif", size=11),
    margin       = dict(l=40, r=20, t=30, b=40),
)


# ═════════════════════════════════════════════════════════════════════════════
# Helper: apply filters
# ═════════════════════════════════════════════════════════════════════════════

def _filter(df: pd.DataFrame, branch: str,
            start_date, end_date, date_col: str = "grn_date") -> pd.DataFrame:
    out = df.copy()
    if branch and branch != "All" and "branch" in out.columns:
        out = out[out["branch"] == branch]
    if start_date and date_col in out.columns:
        out = out[out[date_col] >= pd.to_datetime(start_date)]
    if end_date and date_col in out.columns:
        out = out[out[date_col] <= pd.to_datetime(end_date)]
    return out


def _fmt_inr(val: float) -> str:
    if val >= 1e7:  return f"₹{val/1e7:.2f}Cr"
    if val >= 1e5:  return f"₹{val/1e5:.2f}L"
    if val >= 1e3:  return f"₹{val/1e3:.1f}K"
    return f"₹{val:.0f}"


# ═════════════════════════════════════════════════════════════════════════════
# INDIA — GSTR-3B style summary
# ═════════════════════════════════════════════════════════════════════════════

def _build_gstr3b_summary(p: pd.DataFrame) -> dict:
    """Calculate GST summary from purchase dataframe."""
    gst_cols = {"sgst": 0, "cgst": 0, "igst": 0, "total_gst": 0}
    for col in gst_cols:
        if col in p.columns:
            gst_cols[col] = float(p[col].fillna(0).sum())
    gst_cols["total_gst"] = gst_cols.get("sgst", 0) + gst_cols.get("cgst", 0) + gst_cols.get("igst", 0)
    if "total_gst" in p.columns:
        reported = float(p["total_gst"].fillna(0).sum())
        if reported > 0:
            gst_cols["total_gst"] = reported

    net_purchase = float(p["net_amount"].fillna(0).sum()) if "net_amount" in p.columns else 0
    gross        = float(p["gross_amount"].fillna(0).sum()) if "gross_amount" in p.columns else 0
    return {**gst_cols, "net_purchase": net_purchase, "gross_purchase": gross}


def _supplier_gst_table(p: pd.DataFrame) -> list[dict]:
    """Aggregate GST per supplier."""
    if p.empty or "supplier_name" not in p.columns:
        return []
    cols  = [c for c in ["supplier_name", "net_amount", "sgst", "cgst", "igst", "total_gst"]
             if c in p.columns]
    grp   = p[cols].groupby("supplier_name", as_index=False).sum(numeric_only=True)
    grp   = grp.sort_values("total_gst" if "total_gst" in grp.columns else "net_amount",
                             ascending=False)
    rows  = grp.head(20).to_dict("records")
    return rows


def _monthly_gst_trend(p: pd.DataFrame) -> pd.DataFrame:
    """Monthly GST totals for trend chart."""
    if p.empty or "grn_date" not in p.columns:
        return pd.DataFrame()
    out = p.copy()
    out["month"] = pd.to_datetime(out["grn_date"]).dt.to_period("M").astype(str)
    gst_c = [c for c in ["sgst","cgst","igst","total_gst","net_amount"] if c in out.columns]
    return out.groupby("month")[gst_c].sum(numeric_only=True).reset_index().sort_values("month")


# ═════════════════════════════════════════════════════════════════════════════
# DASH LAYOUT
# ═════════════════════════════════════════════════════════════════════════════

def _kpi_card(label: str, value: str, color: str = C_GREEN) -> html.Div:
    return html.Div([
        html.Div(label, style={"fontSize": "0.72rem", "color": "#6b7280", "fontWeight": 600,
                               "textTransform": "uppercase", "letterSpacing": "0.04em"}),
        html.Div(value, style={"fontSize": "1.45rem", "fontWeight": 700, "color": color,
                               "marginTop": "2px"}),
    ], style={"background": "#fff", "borderRadius": "10px",
              "padding": "1rem 1.2rem", "boxShadow": "0 1px 4px rgba(0,0,0,0.07)",
              "borderLeft": f"4px solid {color}"})


def render_gst_tab(
    purchase_df: pd.DataFrame,
    branch:      str  = "All",
    start_date         = None,
    end_date           = None,
    country:     str  = "IN",     # "IN" or "US"
    sales_df:    pd.DataFrame = None,
) -> html.Div:
    """
    Main entry point: renders the GST / Tax tab.
    country="IN" → India GSTR-3B view
    country="US" → USA Sales Tax view (QuickBooks data)
    """
    p = _filter(purchase_df, branch, start_date, end_date) if not purchase_df.empty else purchase_df

    if country == "IN":
        return _render_india_gst(p, branch, start_date, end_date)
    else:
        return _render_usa_tax(sales_df or pd.DataFrame(), branch, start_date, end_date)


def _render_india_gst(p: pd.DataFrame, branch: str, start_date, end_date) -> html.Div:
    summary  = _build_gstr3b_summary(p)
    sup_rows = _supplier_gst_table(p)
    monthly  = _monthly_gst_trend(p)

    # ── KPI row ──
    kpis = html.Div([
        _kpi_card("Total GST Paid",   _fmt_inr(summary["total_gst"]),    C_PURPLE),
        _kpi_card("SGST",             _fmt_inr(summary["sgst"]),          C_BLUE),
        _kpi_card("CGST",             _fmt_inr(summary["cgst"]),          C_TEAL),
        _kpi_card("IGST",             _fmt_inr(summary["igst"]),          C_ORANGE),
        _kpi_card("Net Purchases",    _fmt_inr(summary["net_purchase"]),  C_GREEN),
    ], style={"display": "grid",
              "gridTemplateColumns": "repeat(auto-fill, minmax(160px, 1fr))",
              "gap": "0.75rem", "marginBottom": "1.2rem"})

    # ── Monthly trend chart ──
    trend_chart = html.Div()
    if not monthly.empty and "month" in monthly.columns:
        fig = go.Figure()
        colors = {"sgst": C_BLUE, "cgst": C_TEAL, "igst": C_ORANGE}
        for col, color in colors.items():
            if col in monthly.columns:
                fig.add_trace(go.Bar(
                    x=monthly["month"], y=monthly[col],
                    name=col.upper(), marker_color=color,
                ))
        fig.update_layout(**CHART_LAYOUT, barmode="stack",
                          title_text="Monthly GST Breakdown (Stacked)",
                          title_font_size=13,
                          legend=dict(orientation="h", y=-0.15))
        trend_chart = html.Div([
            html.Div("Monthly GST Trend", style={"fontWeight": 600, "fontSize": "0.9rem",
                                                  "marginBottom": "0.5rem"}),
            dcc.Graph(figure=fig, config={"displayModeBar": False}),
        ], style={"background": "#fff", "borderRadius": "10px",
                  "padding": "1rem", "boxShadow": "0 1px 4px rgba(0,0,0,0.07)",
                  "marginBottom": "1.2rem"})

    # ── GSTR-3B summary table ──
    gstr3b_table = html.Div([
        html.Div("GSTR-3B Summary (Input Tax Credit)", style={
            "fontWeight": 700, "fontSize": "0.9rem", "marginBottom": "0.75rem",
            "color": C_GREEN}),
        html.Table([
            html.Thead(html.Tr([
                html.Th("Tax Type"),
                html.Th("Amount", style={"textAlign": "right"}),
                html.Th("% of Net Purchase", style={"textAlign": "right"}),
            ])),
            html.Tbody([
                html.Tr([
                    html.Td("SGST"),
                    html.Td(_fmt_inr(summary["sgst"]),    style={"textAlign": "right"}),
                    html.Td("{:.2f}%".format(
                        summary["sgst"]/summary["net_purchase"]*100
                        if summary["net_purchase"] else 0),
                        style={"textAlign": "right"}),
                ]),
                html.Tr([
                    html.Td("CGST"),
                    html.Td(_fmt_inr(summary["cgst"]),    style={"textAlign": "right"}),
                    html.Td("{:.2f}%".format(
                        summary["cgst"]/summary["net_purchase"]*100
                        if summary["net_purchase"] else 0),
                        style={"textAlign": "right"}),
                ]),
                html.Tr([
                    html.Td("IGST"),
                    html.Td(_fmt_inr(summary["igst"]),    style={"textAlign": "right"}),
                    html.Td("{:.2f}%".format(
                        summary["igst"]/summary["net_purchase"]*100
                        if summary["net_purchase"] else 0),
                        style={"textAlign": "right"}),
                ]),
                html.Tr([
                    html.Td(html.Strong("Total GST")),
                    html.Td(html.Strong(_fmt_inr(summary["total_gst"])),
                            style={"textAlign": "right"}),
                    html.Td(html.Strong("{:.2f}%".format(
                        summary["total_gst"]/summary["net_purchase"]*100
                        if summary["net_purchase"] else 0)),
                        style={"textAlign": "right"}),
                ], style={"borderTop": "2px solid #e2e8f0", "fontWeight": 700}),
            ]),
        ], style={"width": "100%", "borderCollapse": "collapse",
                  "fontSize": "0.82rem"}),
    ], style={"background": "#fff", "borderRadius": "10px",
              "padding": "1.2rem", "boxShadow": "0 1px 4px rgba(0,0,0,0.07)",
              "marginBottom": "1.2rem"})

    # ── Supplier GST table ──
    sup_table = html.Div()
    if sup_rows:
        def _sup_row(r: dict):
            name  = r.get("supplier_name", "")
            net   = r.get("net_amount",    0)
            sgst  = r.get("sgst",          0)
            cgst  = r.get("cgst",          0)
            igst  = r.get("igst",          0)
            total = r.get("total_gst",     0) or (sgst + cgst + igst)
            return html.Tr([
                html.Td(name[:40],         style={"fontSize": "0.78rem"}),
                html.Td(_fmt_inr(net),     style={"textAlign": "right", "fontSize": "0.78rem"}),
                html.Td(_fmt_inr(sgst),    style={"textAlign": "right", "fontSize": "0.78rem"}),
                html.Td(_fmt_inr(cgst),    style={"textAlign": "right", "fontSize": "0.78rem"}),
                html.Td(_fmt_inr(igst),    style={"textAlign": "right", "fontSize": "0.78rem"}),
                html.Td(_fmt_inr(total),   style={"textAlign": "right", "fontSize": "0.78rem",
                                                   "fontWeight": 600, "color": C_PURPLE}),
            ])

        sup_table = html.Div([
            html.Div("Supplier-wise GST (Top 20)", style={
                "fontWeight": 700, "fontSize": "0.9rem", "marginBottom": "0.75rem"}),
            html.Div([
                html.Table([
                    html.Thead(html.Tr([
                        html.Th("Supplier"),
                        html.Th("Net Amt",    style={"textAlign": "right"}),
                        html.Th("SGST",       style={"textAlign": "right"}),
                        html.Th("CGST",       style={"textAlign": "right"}),
                        html.Th("IGST",       style={"textAlign": "right"}),
                        html.Th("Total GST",  style={"textAlign": "right"}),
                    ])),
                    html.Tbody([_sup_row(r) for r in sup_rows]),
                ], style={"width": "100%", "borderCollapse": "collapse",
                          "fontSize": "0.8rem"}),
            ], style={"overflowX": "auto"}),
        ], style={"background": "#fff", "borderRadius": "10px",
                  "padding": "1.2rem", "boxShadow": "0 1px 4px rgba(0,0,0,0.07)"})

    empty_msg = html.Div() if not p.empty else html.Div(
        "No purchase data found for the selected filters.",
        style={"color": "#94a3b8", "padding": "2rem", "textAlign": "center"}
    )

    return html.Div([
        # Header
        html.Div([
            html.H4("GST Report — India", style={"margin": 0, "fontWeight": 700, "color": C_GREEN}),
            html.Span("GSTR-3B Input Tax Credit Summary",
                      style={"fontSize": "0.78rem", "color": "#6b7280"}),
        ], style={"marginBottom": "1.2rem"}),
        empty_msg,
        kpis,
        trend_chart,
        gstr3b_table,
        sup_table,
    ])


def _render_usa_tax(s: pd.DataFrame, branch: str, start_date, end_date) -> html.Div:
    """
    USA Sales Tax view — placeholder until QuickBooks OAuth is wired.
    Shows connection CTA if no QuickBooks data is present.
    """
    has_data = not s.empty and "state" in s.columns

    if not has_data:
        return html.Div([
            html.Div([
                html.Div("🇺🇸", style={"fontSize": "3rem", "marginBottom": "0.5rem"}),
                html.H4("USA Sales Tax Report", style={"color": C_BLUE, "fontWeight": 700}),
                html.P(
                    "Connect QuickBooks to automatically pull sales tax data by state. "
                    "Once connected, you'll see a state-wise tax breakdown and IRS Schedule C summary.",
                    style={"color": "#6b7280", "maxWidth": "480px", "margin": "0 auto 1.5rem",
                           "lineHeight": 1.6, "fontSize": "0.88rem"}
                ),
                dbc.Button(
                    "🔗 Connect QuickBooks",
                    href="/connect/quickbooks",
                    color="primary",
                    style={"fontWeight": 600},
                ),
                html.Div([
                    html.Small("Or upload a QuickBooks Excel export to preview immediately.",
                               style={"color": "#94a3b8"})
                ], style={"marginTop": "0.75rem"}),
            ], style={"textAlign": "center", "padding": "4rem 2rem"}),
        ], style={"minHeight": "50vh", "display": "flex",
                  "alignItems": "center", "justifyContent": "center"})

    # If QuickBooks data is present — state-wise table
    state_grp = s.groupby("state")[["net_amount", "tax_amount"]].sum().reset_index()
    state_grp = state_grp.sort_values("tax_amount", ascending=False)
    rows = [
        html.Tr([
            html.Td(r["state"]),
            html.Td(f"${r['net_amount']:,.0f}",  style={"textAlign": "right"}),
            html.Td(f"${r['tax_amount']:,.0f}",  style={"textAlign": "right"}),
            html.Td("{:.2f}%".format(r["tax_amount"]/r["net_amount"]*100
                                     if r["net_amount"] else 0),
                    style={"textAlign": "right"}),
        ])
        for _, r in state_grp.iterrows()
    ]
    return html.Div([
        html.H4("USA Sales Tax — State Summary", style={"color": C_BLUE, "fontWeight": 700,
                                                         "marginBottom": "1rem"}),
        html.Table([
            html.Thead(html.Tr([
                html.Th("State"), html.Th("Sales", style={"textAlign":"right"}),
                html.Th("Tax Collected", style={"textAlign":"right"}),
                html.Th("Tax Rate", style={"textAlign":"right"}),
            ])),
            html.Tbody(rows),
        ], style={"width": "100%", "borderCollapse": "collapse", "fontSize": "0.82rem"}),
    ], style={"background": "#fff", "borderRadius": "10px",
              "padding": "1.2rem", "boxShadow": "0 1px 4px rgba(0,0,0,0.07)"})
