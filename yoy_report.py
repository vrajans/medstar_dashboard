"""
yoy_report.py  —  Year-over-Year Comparison Tab
================================================
Renders a side-by-side YoY analytics view:

  • KPI delta cards (Sales, Purchases, Margin %, Bills)
  • Monthly bar chart: current year vs prior year (sales)
  • Month-by-month table with variance

Usage
-----
from yoy_report import render_yoy_tab
content = render_yoy_tab(sales_df, purchase_df, branch, selected_year)
"""

import pandas as pd
import plotly.graph_objects as go
from dash import html, dcc
import dash_bootstrap_components as dbc
from datetime import date

C_GREEN  = "#1e7e4b"
C_BLUE   = "#0d6efd"
C_ORANGE = "#fd7e14"
C_RED    = "#dc3545"
C_TEAL   = "#17a2b8"
C_PURPLE = "#6f42c1"
C_GRAY   = "#6b7280"

CHART_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor ="rgba(0,0,0,0)",
    font=dict(family="Inter,Arial,sans-serif", size=11),
    margin=dict(l=40, r=20, t=30, b=50),
)

MONTHS = ["Jan","Feb","Mar","Apr","May","Jun",
          "Jul","Aug","Sep","Oct","Nov","Dec"]


def _fmt(v: float, currency: str = "INR") -> str:
    if currency == "USD":
        if v >= 1e6: return f"${v/1e6:.2f}M"
        if v >= 1e3: return f"${v/1e3:.1f}K"
        return f"${v:.0f}"
    if v >= 1e7: return f"₹{v/1e7:.2f}Cr"
    if v >= 1e5: return f"₹{v/1e5:.2f}L"
    if v >= 1e3: return f"₹{v/1e3:.1f}K"
    return f"₹{v:.0f}"


def _pct(curr: float, prev: float) -> str:
    if prev == 0: return "—"
    delta = (curr - prev) / abs(prev) * 100
    arrow = "▲" if delta >= 0 else "▼"
    color = C_GREEN if delta >= 0 else C_RED
    return arrow, delta, color


def _monthly_agg(df: pd.DataFrame, date_col: str,
                 value_col: str, year: int) -> pd.Series:
    """Aggregate a value column by month for a given year. Returns 12-element Series."""
    if df.empty or date_col not in df.columns or value_col not in df.columns:
        return pd.Series([0.0] * 12, index=range(1, 13))
    out  = df.copy()
    out[date_col] = pd.to_datetime(out[date_col], errors="coerce")
    out  = out[out[date_col].dt.year == year]
    grp  = out.groupby(out[date_col].dt.month)[value_col].sum()
    return grp.reindex(range(1, 13), fill_value=0)


def render_yoy_tab(
    sales_df:    pd.DataFrame,
    purchase_df: pd.DataFrame,
    branch:      str  = "All",
    curr_year:   int  = None,
    currency:    str  = "INR",
) -> html.Div:
    """Render the Year-over-Year comparison tab."""
    if curr_year is None:
        curr_year = date.today().year
    prev_year = curr_year - 1

    # Filter by branch
    s = sales_df.copy()
    p = purchase_df.copy()
    if branch and branch != "All":
        if "branch" in s.columns: s = s[s["branch"] == branch]
        if "branch" in p.columns: p = p[p["branch"] == branch]

    # ── Monthly series ──────────────────────────────────────────
    s_curr = _monthly_agg(s, "bill_date", "net_amount", curr_year)
    s_prev = _monthly_agg(s, "bill_date", "net_amount", prev_year)
    p_curr = _monthly_agg(p, "grn_date",  "net_amount", curr_year)
    p_prev = _monthly_agg(p, "grn_date",  "net_amount", prev_year)

    m_curr = _monthly_agg(s, "bill_date", "margin_pct", curr_year)
    m_prev = _monthly_agg(s, "bill_date", "margin_pct", prev_year)

    # ── Full-year totals ──────────────────────────────────────
    def _total(df, date_col, col, year):
        if df.empty or date_col not in df.columns or col not in df.columns:
            return 0.0
        d = df.copy(); d[date_col] = pd.to_datetime(d[date_col], errors="coerce")
        return float(d[d[date_col].dt.year == year][col].sum())

    s_tot_c = s_curr.sum();  s_tot_p = s_prev.sum()
    p_tot_c = p_curr.sum();  p_tot_p = p_prev.sum()
    m_avg_c = float(m_curr.replace(0, None).mean() or 0)
    m_avg_p = float(m_prev.replace(0, None).mean() or 0)

    # ── Year selector ─────────────────────────────────────────
    avail_years = sorted({
        int(pd.to_datetime(v).year)
        for df, col in [(s, "bill_date"), (p, "grn_date")]
        for v in (df[col].dropna() if not df.empty and col in df.columns else [])
    }, reverse=True)
    if not avail_years:
        avail_years = [curr_year, prev_year]

    year_selector = html.Div([
        html.Span("Compare year: ", style={"fontSize": "0.82rem", "color": C_GRAY,
                                           "marginRight": "0.5rem", "fontWeight": 600}),
        dcc.Dropdown(
            id="yoy-year-select",
            options=[{"label": str(y), "value": y} for y in avail_years],
            value=curr_year,
            clearable=False,
            style={"width": "120px", "fontSize": "0.82rem"},
        ),
    ], style={"display": "flex", "alignItems": "center", "marginBottom": "1.2rem"})

    # ── KPI cards row ─────────────────────────────────────────
    def kpi_card(label, curr_v, prev_v, is_pct=False):
        if prev_v and prev_v != 0:
            delta = (curr_v - prev_v) / abs(prev_v) * 100
        else:
            delta = None

        fmt_curr = f"{curr_v:.1f}%" if is_pct else _fmt(curr_v, currency)
        fmt_prev = f"{prev_v:.1f}%" if is_pct else _fmt(prev_v, currency)

        delta_el = html.Div()
        if delta is not None:
            arrow = "▲" if delta >= 0 else "▼"
            col   = C_GREEN if delta >= 0 else C_RED
            delta_el = html.Div([
                html.Span(f"{arrow} {abs(delta):.1f}%",
                          style={"color": col, "fontWeight": 700, "fontSize": "0.85rem"}),
                html.Span(" vs prior year",
                          style={"color": C_GRAY, "fontSize": "0.72rem", "marginLeft": "4px"}),
            ], style={"marginTop": "4px"})

        return html.Div([
            html.Div(label, style={"fontSize": "0.72rem", "color": C_GRAY, "fontWeight": 600,
                                   "textTransform": "uppercase", "letterSpacing": "0.04em"}),
            html.Div(fmt_curr, style={"fontSize": "1.4rem", "fontWeight": 700,
                                      "color": "#1a1a2e", "marginTop": "2px"}),
            html.Div([
                html.Span(f"{prev_year}: ", style={"color": C_GRAY, "fontSize": "0.72rem"}),
                html.Span(fmt_prev, style={"color": C_GRAY, "fontSize": "0.72rem"}),
            ], style={"marginTop": "2px"}),
            delta_el,
        ], style={"background": "#fff", "borderRadius": "10px",
                  "padding": "1rem 1.2rem", "boxShadow": "0 1px 4px rgba(0,0,0,0.07)",
                  "borderTop": f"3px solid {C_GREEN}"})

    kpi_row = html.Div([
        kpi_card(f"Sales {curr_year}",    s_tot_c, s_tot_p),
        kpi_card(f"Purchases {curr_year}", p_tot_c, p_tot_p),
        kpi_card(f"Avg Margin {curr_year}", m_avg_c, m_avg_p, is_pct=True),
    ], style={"display": "grid",
              "gridTemplateColumns": "repeat(auto-fill, minmax(200px, 1fr))",
              "gap": "0.75rem", "marginBottom": "1.5rem"})

    # ── Sales YoY bar chart ───────────────────────────────────
    fig_sales = go.Figure()
    fig_sales.add_trace(go.Bar(
        x=MONTHS, y=s_prev.values,
        name=str(prev_year),
        marker_color="rgba(13,110,253,0.35)",
        marker_line_width=0,
    ))
    fig_sales.add_trace(go.Bar(
        x=MONTHS, y=s_curr.values,
        name=str(curr_year),
        marker_color=C_GREEN,
    ))
    fig_sales.update_layout(
        **CHART_LAYOUT,
        barmode="group",
        title_text=f"Monthly Sales — {prev_year} vs {curr_year}",
        title_font_size=13,
        legend=dict(orientation="h", y=-0.18),
    )
    fig_sales.update_yaxes(tickprefix="₹" if currency=="INR" else "$", tickformat=".2s")

    # ── Purchase YoY bar chart ────────────────────────────────
    fig_purch = go.Figure()
    fig_purch.add_trace(go.Bar(
        x=MONTHS, y=p_prev.values,
        name=str(prev_year),
        marker_color="rgba(253,126,20,0.35)",
        marker_line_width=0,
    ))
    fig_purch.add_trace(go.Bar(
        x=MONTHS, y=p_curr.values,
        name=str(curr_year),
        marker_color=C_ORANGE,
    ))
    fig_purch.update_layout(
        **CHART_LAYOUT,
        barmode="group",
        title_text=f"Monthly Purchases — {prev_year} vs {curr_year}",
        title_font_size=13,
        legend=dict(orientation="h", y=-0.18),
    )
    fig_purch.update_yaxes(tickprefix="₹" if currency=="INR" else "$", tickformat=".2s")

    chart_row = html.Div([
        html.Div([dcc.Graph(figure=fig_sales,
                            config={"displayModeBar": False})],
                 style={"flex":1, "background":"#fff","borderRadius":"10px",
                        "padding":"0.75rem","boxShadow":"0 1px 4px rgba(0,0,0,0.07)"}),
        html.Div([dcc.Graph(figure=fig_purch,
                            config={"displayModeBar": False})],
                 style={"flex":1, "background":"#fff","borderRadius":"10px",
                        "padding":"0.75rem","boxShadow":"0 1px 4px rgba(0,0,0,0.07)"}),
    ], style={"display":"flex","gap":"1rem","marginBottom":"1.5rem"})

    # ── Month-by-month comparison table ──────────────────────
    def _row_color(curr_v, prev_v):
        if curr_v > prev_v: return "#f0fdf4"
        if curr_v < prev_v: return "#fff5f5"
        return "#fff"

    def _delta_cell(curr_v, prev_v):
        if prev_v == 0: return html.Td("—", style={"textAlign":"right","color":C_GRAY})
        d   = (curr_v - prev_v) / abs(prev_v) * 100
        col = C_GREEN if d >= 0 else C_RED
        return html.Td(
            f"{'▲' if d>=0 else '▼'} {abs(d):.1f}%",
            style={"textAlign":"right","color":col,"fontWeight":600,"fontSize":"0.8rem"}
        )

    table_rows = []
    for i, m in enumerate(MONTHS, 1):
        sc = float(s_curr.get(i, 0)); sp = float(s_prev.get(i, 0))
        pc = float(p_curr.get(i, 0)); pp = float(p_prev.get(i, 0))
        bg = _row_color(sc, sp)
        table_rows.append(html.Tr([
            html.Td(m, style={"fontWeight":600,"fontSize":"0.8rem","padding":"6px 8px"}),
            html.Td(_fmt(sp, currency), style={"textAlign":"right","color":C_GRAY,"fontSize":"0.78rem"}),
            html.Td(_fmt(sc, currency), style={"textAlign":"right","fontSize":"0.8rem"}),
            _delta_cell(sc, sp),
            html.Td(_fmt(pp, currency), style={"textAlign":"right","color":C_GRAY,"fontSize":"0.78rem"}),
            html.Td(_fmt(pc, currency), style={"textAlign":"right","fontSize":"0.8rem"}),
            _delta_cell(pc, pp),
        ], style={"background":bg}))

    # Total row
    table_rows.append(html.Tr([
        html.Td(html.Strong("Total"), style={"padding":"6px 8px"}),
        html.Td(html.Strong(_fmt(s_tot_p, currency)), style={"textAlign":"right","color":C_GRAY}),
        html.Td(html.Strong(_fmt(s_tot_c, currency)), style={"textAlign":"right"}),
        _delta_cell(s_tot_c, s_tot_p),
        html.Td(html.Strong(_fmt(p_tot_p, currency)), style={"textAlign":"right","color":C_GRAY}),
        html.Td(html.Strong(_fmt(p_tot_c, currency)), style={"textAlign":"right"}),
        _delta_cell(p_tot_c, p_tot_p),
    ], style={"borderTop":"2px solid #e2e8f0","background":"#f8fafc"}))

    month_table = html.Div([
        html.Div("Month-by-Month Comparison", style={
            "fontWeight": 700, "fontSize": "0.9rem", "marginBottom": "0.75rem"}),
        html.Div([
            html.Table([
                html.Thead(html.Tr([
                    html.Th("Month", style={"padding":"6px 8px"}),
                    html.Th(f"Sales {prev_year}", style={"textAlign":"right","color":C_GRAY}),
                    html.Th(f"Sales {curr_year}", style={"textAlign":"right"}),
                    html.Th("Δ Sales",            style={"textAlign":"right"}),
                    html.Th(f"Purch {prev_year}", style={"textAlign":"right","color":C_GRAY}),
                    html.Th(f"Purch {curr_year}", style={"textAlign":"right"}),
                    html.Th("Δ Purch",            style={"textAlign":"right"}),
                ], style={"background":"#f8fafc"})),
                html.Tbody(table_rows),
            ], style={"width":"100%","borderCollapse":"collapse","fontSize":"0.8rem"}),
        ], style={"overflowX":"auto"}),
    ], style={"background":"#fff","borderRadius":"10px",
              "padding":"1.2rem","boxShadow":"0 1px 4px rgba(0,0,0,0.07)"})

    return html.Div([
        html.Div([
            html.H4("Year-over-Year Analysis",
                    style={"margin":0,"fontWeight":700,"color":C_GREEN}),
            html.Span("Sales & Purchase comparison across years",
                      style={"fontSize":"0.78rem","color":C_GRAY}),
        ], style={"marginBottom":"1rem"}),
        year_selector,
        kpi_row,
        chart_row,
        month_table,
    ])
