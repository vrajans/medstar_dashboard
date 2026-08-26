"""
tenant_analytics.py — InsightHub Phase 2
Per-tenant analytics rendered from data in `sales` / `purchases`
filtered by tenant_id.  BRD US-105, US-107, US-108, US-203, US-214.

Design: Professional SaaS palette (navy + sky-blue accent).
No emojis. Clean Inter typography. Consistent 2-color chart scheme.
"""

import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from dash import html, dcc, dash_table
import dash_bootstrap_components as dbc
from sqlalchemy import text

# ── Professional brand palette ────────────────────────────────
# Primary Navy + Sky Blue accent — trustworthy, enterprise SaaS
C_NAVY    = "#1E293B"   # primary text / headers
C_BLUE    = "#2563EB"   # primary brand accent (charts, links)
C_SKY     = "#0EA5E9"   # secondary blue (secondary charts)
C_TEAL    = "#0D9488"   # positive / success variant
C_GREEN   = "#059669"   # positive delta indicators
C_AMBER   = "#D97706"   # warning / cost indicators
C_RED     = "#DC2626"   # negative delta / danger
C_INDIGO  = "#4F46E5"   # customer-related metrics
C_SLATE   = "#64748B"   # secondary text / labels
C_MUTED   = "#94A3B8"   # muted text / placeholder
C_BORDER  = "#E2E8F0"   # card borders
C_BG      = "#F8FAFC"   # page / section background
C_WHITE   = "#FFFFFF"   # card background

# Chart color sequences (professional, 2-3 colors max per chart)
PALETTE_PRIMARY  = [C_BLUE, C_SKY, C_TEAL, C_INDIGO, "#7C3AED", "#0891B2"]
PALETTE_GRADIENT = [[0, "#BFDBFE"], [1, C_BLUE]]  # light → brand blue

CHART_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="Inter,system-ui,-apple-system,sans-serif", size=12, color=C_NAVY),
    margin=dict(l=8, r=8, t=32, b=8),
    legend=dict(
        orientation="h", yanchor="bottom", y=1.04, xanchor="right", x=1,
        bgcolor="rgba(0,0,0,0)", font=dict(size=11),
    ),
    xaxis=dict(
        gridcolor="#F1F5F9", linecolor=C_BORDER, tickfont=dict(size=11),
        showgrid=False, zeroline=False,
    ),
    yaxis=dict(
        gridcolor="#F1F5F9", linecolor="rgba(0,0,0,0)", tickfont=dict(size=11),
        showgrid=True, zeroline=False,
    ),
    hoverlabel=dict(
        bgcolor=C_WHITE, bordercolor=C_BORDER,
        font=dict(family="Inter,system-ui,sans-serif", size=12, color=C_NAVY),
    ),
)


# ── Data loading ──────────────────────────────────────────────

def load_tenant_df(engine, tenant_id: int):
    """Return (sales_df, purchases_df) for this tenant."""

    def _query(table):
        try:
            with engine.connect() as conn:
                # Primary: direct tenant_id match
                df = pd.read_sql_query(
                    text(f"SELECT * FROM {table} WHERE CAST(tenant_id AS INTEGER) = :tid"),
                    conn, params={"tid": int(tenant_id)}
                )
                if not df.empty:
                    return df
                # Fallback: via upload_history upload_ids
                uid_rows = conn.execute(
                    text("SELECT id FROM upload_history "
                         "WHERE CAST(tenant_id AS INTEGER)=:tid AND status='active'"),
                    {"tid": int(tenant_id)}
                ).fetchall()
                uid_list = [r[0] for r in uid_rows]
                if not uid_list:
                    return pd.DataFrame()
                placeholders = ",".join(str(i) for i in uid_list)
                df2 = pd.read_sql_query(
                    text(f"SELECT * FROM {table} WHERE upload_id IN ({placeholders})"),
                    conn
                )
                return df2
        except Exception as exc:
            print(f"[ta] ERROR {table}: {exc}")
            return pd.DataFrame()

    s = _query("sales")
    p = _query("purchases")

    for df in (s, p):
        if not df.empty:
            if "bill_date" in df.columns:
                df["bill_date"] = pd.to_datetime(df["bill_date"], errors="coerce")
            if "net_amount" in df.columns:
                df["net_amount"] = pd.to_numeric(df["net_amount"], errors="coerce").fillna(0)
    return s, p


# ── Number formatting ─────────────────────────────────────────

def _fmt(v, cur="$"):
    """Format currency value with appropriate abbreviation."""
    if v >= 1_000_000:
        return f"{cur}{v/1_000_000:.2f}M"
    if v >= 10_000:
        return f"{cur}{v/1_000:.1f}K"
    return f"{cur}{v:,.0f}"


def _fmt_axis(v, cur="$"):
    """Short format for axis tick labels."""
    if v >= 1_000_000:
        return f"{cur}{v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"{cur}{v/1_000:.0f}K"
    return f"{cur}{v:.0f}"


# ── Shared UI components ──────────────────────────────────────

def _kpi_card(label, value, delta=None, delta_label="vs prior period", color=C_BLUE, subtitle=None):
    """
    Professional KPI metric card — no emojis, clean enterprise design.
    Top accent border in brand color. Delta shown as colored ▲▼ text.
    """
    delta_el = html.Div()
    if delta is not None:
        pos = delta >= 0
        arrow = "▲" if pos else "▼"
        d_col = C_GREEN if pos else C_RED
        delta_el = html.Div([
            html.Span(f"{arrow} {abs(delta):.1f}% ", style={"color": d_col, "fontWeight": 600}),
            html.Span(delta_label, style={"color": C_MUTED}),
        ], style={"fontSize": "0.7rem", "marginTop": "6px"})
    elif subtitle:
        delta_el = html.Div(subtitle, style={"fontSize": "0.7rem", "marginTop": "6px", "color": C_MUTED})

    return html.Div([
        html.Div(value, style={
            "fontSize": "1.75rem", "fontWeight": 700, "color": color,
            "lineHeight": 1.1, "letterSpacing": "-0.5px", "fontVariantNumeric": "tabular-nums",
        }),
        html.Div(label, style={
            "fontSize": "0.69rem", "color": C_SLATE, "fontWeight": 500,
            "textTransform": "uppercase", "letterSpacing": "0.07em", "marginTop": "5px",
        }),
        delta_el,
    ], style={
        "background": C_WHITE, "borderRadius": "10px", "padding": "1.1rem 1.25rem",
        "border": f"1px solid {C_BORDER}",
        "borderTop": f"3px solid {color}",
        "flex": 1, "minWidth": "155px",
    })


def _section_header(title, subtitle=None):
    """Clean section divider with title and optional subtitle."""
    return html.Div([
        html.H5(title, style={
            "fontWeight": 700, "color": C_NAVY, "margin": 0, "fontSize": "1.05rem",
        }),
        html.Span(subtitle, style={"color": C_SLATE, "fontSize": "0.78rem"}) if subtitle else html.Div(),
    ], style={"marginBottom": "1rem"})


def _card(title, body, height=None, badge=None):
    """Section card with clean header and optional badge."""
    h = {"height": f"{height}px", "overflow": "hidden"} if height else {}
    badge_el = html.Span(
        badge, style={
            "fontSize": "0.65rem", "fontWeight": 600, "padding": "2px 8px",
            "borderRadius": "100px", "background": "#EFF6FF", "color": C_BLUE,
            "border": f"1px solid #BFDBFE", "marginLeft": "8px",
        }
    ) if badge else html.Span()

    return html.Div([
        html.Div([
            html.Span(title, style={
                "fontWeight": 600, "fontSize": "0.82rem", "color": C_NAVY,
            }),
            badge_el,
        ], style={"marginBottom": "0.8rem", "display": "flex", "alignItems": "center"}),
        html.Div(body, style=h),
    ], style={
        "background": C_WHITE, "borderRadius": "10px",
        "padding": "1.15rem 1.25rem", "border": f"1px solid {C_BORDER}",
        "marginBottom": "0.85rem",
    })


def _empty_state(title, msg="No data available for this period.", icon_text="—"):
    """Empty state card with helpful message."""
    return _card(title, html.Div([
        html.Div(icon_text, style={"fontSize": "2rem", "color": C_BORDER, "textAlign": "center", "marginBottom": "0.5rem"}),
        html.Div(msg, style={"color": C_MUTED, "textAlign": "center", "fontSize": "0.83rem"}),
    ], style={"padding": "2rem 0"}))


def _fig(fig, height=280):
    """Wrap a plotly figure in a dcc.Graph with shared layout."""
    fig.update_layout(**CHART_LAYOUT, height=height)
    return dcc.Graph(
        figure=fig,
        config={"displayModeBar": False, "responsive": True},
        # Explicit pixel height prevents chart from overflowing into cards below
        style={"height": f"{height}px", "marginTop": "-4px"},
    )


def _styled_table(df, columns=None, page_size=15):
    """Professional data table with clean styling."""
    if columns is None:
        columns = [{"name": c, "id": c} for c in df.columns]
    return dash_table.DataTable(
        columns=columns,
        data=df.to_dict("records"),
        page_size=page_size,
        sort_action="native",
        filter_action="native",
        style_table={"overflowX": "auto", "borderRadius": "6px"},
        style_cell={
            "fontSize": "0.79rem", "padding": "9px 14px", "textAlign": "left",
            "fontFamily": "Inter,system-ui,sans-serif", "color": C_NAVY,
            "border": f"1px solid {C_BORDER}",
        },
        style_header={
            "backgroundColor": C_BG, "fontWeight": 600, "color": C_SLATE,
            "fontSize": "0.72rem", "textTransform": "uppercase", "letterSpacing": "0.04em",
            "border": f"1px solid {C_BORDER}",
        },
        style_data={"border": f"1px solid {C_BORDER}"},
        style_data_conditional=[
            {"if": {"row_index": "odd"}, "backgroundColor": "#FAFBFC"},
            {"if": {"state": "selected"}, "backgroundColor": "#EFF6FF", "border": f"1px solid {C_BLUE}"},
        ],
    )


# ── Period comparison helpers ─────────────────────────────────

def _period_delta(s: pd.DataFrame, col="net_amount"):
    """Split data into current half and prior half; return % change."""
    if s.empty or "bill_date" not in s.columns or s["bill_date"].isna().all():
        return None
    s = s.dropna(subset=["bill_date"])
    mid = s["bill_date"].min() + (s["bill_date"].max() - s["bill_date"].min()) / 2
    curr = s[s["bill_date"] >= mid][col].sum()
    prev = s[s["bill_date"] <  mid][col].sum()
    if prev == 0:
        return None
    return (curr - prev) / prev * 100


def _monthly(df):
    """Group by calendar month, return sorted DataFrame."""
    if df.empty or "bill_date" not in df.columns:
        return pd.DataFrame(columns=["month", "net_amount"])
    d = df.copy().dropna(subset=["bill_date"])
    d["month"] = d["bill_date"].dt.to_period("M").astype(str)
    return d.groupby("month", as_index=False)["net_amount"].sum().sort_values("month")


def _yoy_data(df):
    """
    Build year-over-year comparison DataFrame.
    Returns DataFrame with columns [month_num, year, net_amount] or empty.
    Only returns data if 2+ distinct years exist.
    """
    if df.empty or "bill_date" not in df.columns:
        return pd.DataFrame()
    d = df.copy().dropna(subset=["bill_date"])
    d["year"] = d["bill_date"].dt.year
    d["month_num"] = d["bill_date"].dt.month
    years = sorted(d["year"].unique())
    if len(years) < 2:
        return pd.DataFrame()
    # Take the last 2 years only
    last2 = years[-2:]
    d = d[d["year"].isin(last2)]
    agg = d.groupby(["year", "month_num"], as_index=False)["net_amount"].sum()
    return agg


MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ── Overview tab ──────────────────────────────────────────────

def render_overview(s: pd.DataFrame, p: pd.DataFrame, tenant_name: str,
                    error=None, cur="$") -> html.Div:

    if error:
        return html.Div(dbc.Alert(f"Data load error: {error}", color="danger"),
                        style={"padding": "1rem"})

    rev   = s["net_amount"].sum() if not s.empty else 0
    txns  = len(s)
    avg   = rev / txns if txns else 0
    custs = s["supplier_name"].nunique() if (not s.empty and "supplier_name" in s.columns) else 0
    costs = p["net_amount"].sum() if not p.empty else 0
    has_costs  = not p.empty and costs > 0
    margin_pct = ((rev - costs) / rev * 100) if (rev > 0 and has_costs) else None

    d_rev  = _period_delta(s, "net_amount")
    d_txns = _period_delta(s.assign(cnt=1), "cnt") if not s.empty else None

    margin_val   = f"{margin_pct:.1f}%" if margin_pct is not None else "N/A"
    margin_color = (C_GREEN if margin_pct >= 20 else C_AMBER) if margin_pct is not None else C_MUTED
    margin_sub   = None if margin_pct is not None else "upload cost data"

    kpi_row = html.Div([
        _kpi_card("Total Revenue",    _fmt(rev, cur),        d_rev,   color=C_BLUE),
        _kpi_card("Transactions",     f"{txns:,}",           d_txns,  color=C_INDIGO),
        _kpi_card("Avg Transaction",  _fmt(avg, cur),        None,    color=C_TEAL),
        _kpi_card("Unique Customers", f"{custs:,}",          None,    color=C_SKY),
        _kpi_card("Gross Margin",     margin_val,            None,
                  color=margin_color, subtitle=margin_sub),
    ], style={"display": "flex", "gap": "0.7rem", "marginBottom": "1.1rem", "flexWrap": "wrap"})

    # ── Monthly revenue trend (BRD US-107) ──
    mon = _monthly(s)
    if not mon.empty:
        fig_trend = go.Figure()
        fig_trend.add_bar(
            x=mon["month"], y=mon["net_amount"],
            marker_color=C_BLUE, marker_opacity=0.85,
            name="Revenue",
            hovertemplate="%{x}<br><b>" + cur + "%{y:,.0f}</b><extra></extra>",
        )
        # Trend line
        if len(mon) >= 3:
            x_idx = list(range(len(mon)))
            z = np.polyfit(x_idx, mon["net_amount"], 1)
            trend_y = [z[0] * i + z[1] for i in x_idx]
            fig_trend.add_scatter(
                x=mon["month"], y=trend_y, mode="lines",
                line=dict(color=C_AMBER, dash="dot", width=2.5),
                name="Trend", hoverinfo="skip",
            )
        fig_trend.update_layout(
            yaxis=dict(tickprefix=cur, tickformat=",.0f"),
            xaxis_title="", yaxis_title="",
            bargap=0.28,
        )
        trend_content = _fig(fig_trend, 270)
    else:
        trend_content = html.Div(
            "Upload data with dates to see monthly revenue trend.",
            style={"color": C_MUTED, "textAlign": "center", "padding": "3rem", "fontSize": "0.85rem"},
        )

    # ── YoY comparison chart (BRD US-203) ──
    yoy = _yoy_data(s)
    if not yoy.empty:
        years = sorted(yoy["year"].unique())
        prev_yr, curr_yr = years[0], years[1]
        prev_df = yoy[yoy["year"] == prev_yr].sort_values("month_num")
        curr_df = yoy[yoy["year"] == curr_yr].sort_values("month_num")
        months_lbl = [MONTH_ABBR[m - 1] for m in range(1, 13)]

        def _fill12(df):
            full = pd.DataFrame({"month_num": range(1, 13)})
            m = full.merge(df[["month_num", "net_amount"]], on="month_num", how="left").fillna(0)
            return m["net_amount"].tolist()

        fig_yoy = go.Figure()
        fig_yoy.add_bar(
            name=str(prev_yr), x=months_lbl, y=_fill12(prev_df),
            marker_color="#BFDBFE", marker_opacity=0.9,
            hovertemplate="%{x} " + str(prev_yr) + "<br><b>" + cur + "%{y:,.0f}</b><extra></extra>",
        )
        fig_yoy.add_bar(
            name=str(curr_yr), x=months_lbl, y=_fill12(curr_df),
            marker_color=C_BLUE, marker_opacity=0.9,
            hovertemplate="%{x} " + str(curr_yr) + "<br><b>" + cur + "%{y:,.0f}</b><extra></extra>",
        )
        fig_yoy.update_layout(
            barmode="group", bargap=0.2, bargroupgap=0.06,
            yaxis=dict(tickprefix=cur, tickformat=",.0f"),
            xaxis_title="", yaxis_title="",
        )
        yoy_chart = _card(
            f"Year-over-Year Comparison",
            _fig(fig_yoy, 260),
            badge=f"{prev_yr} vs {curr_yr}",
        )
    else:
        yoy_chart = html.Div()

    # ── Top customers bar (BRD US-108) ──
    if not s.empty and "supplier_name" in s.columns:
        top = (s[s["supplier_name"].notna()]
               .groupby("supplier_name", as_index=False)["net_amount"]
               .sum().nlargest(10, "net_amount"))
        if not top.empty:
            fig_cust = go.Figure(go.Bar(
                x=top["net_amount"], y=top["supplier_name"],
                orientation="h",
                marker=dict(color=top["net_amount"], colorscale=PALETTE_GRADIENT, showscale=False),
                hovertemplate="%{y}<br><b>" + cur + "%{x:,.0f}</b><extra></extra>",
            ))
            fig_cust.update_layout(
                yaxis={"categoryorder": "total ascending"},
                xaxis=dict(tickprefix=cur, tickformat=",.0f"),
                xaxis_title="", yaxis_title="",
            )
            cust_content = _fig(fig_cust, 340)
        else:
            cust_content = html.Div("No customer data.", style={"color": C_MUTED, "padding": "2rem"})
    else:
        cust_content = html.Div("No customer column detected.",
                                style={"color": C_MUTED, "padding": "2rem"})

    # ── Revenue vs costs donut ──
    if rev > 0:
        _labels  = ["Revenue"]
        _values  = [rev]
        _colors  = [C_BLUE]
        if has_costs and costs > 0:
            _labels += ["Costs"]
            _values += [costs]
            _colors += [C_AMBER]
        fig_d = go.Figure(go.Pie(
            labels=_labels, values=_values,
            hole=0.62, marker_colors=_colors,
            textinfo="label+percent",
            textfont=dict(size=11),
            hovertemplate=f"%{{label}}: {cur}%{{value:,.0f}}<extra></extra>",
        ))
        center_text = _fmt(rev, cur)
        fig_d.add_annotation(
            text=f"<b>{center_text}</b><br><span style='font-size:9px'>Revenue</span>",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=13, color=C_NAVY),
        )
        donut = _fig(fig_d, 250)
    else:
        donut = html.Div()

    return html.Div([
        _section_header(
            f"{tenant_name} — Overview",
            f"{txns:,} transactions · {custs:,} customers",
        ),
        kpi_row,
        html.Div([
            html.Div(_card("Monthly Revenue", trend_content),
                     style={"flex": "3", "minWidth": "300px"}),
            html.Div(_card("Revenue Breakdown", donut),
                     style={"flex": "1", "minWidth": "200px"}),
        ], style={"display": "flex", "gap": "0.75rem", "flexWrap": "wrap"}),
        yoy_chart,
        _card("Top 10 Clients by Revenue", cust_content),
    ], style={"padding": "0.15rem 0.5rem 0.5rem"})


# ── Revenue tab ───────────────────────────────────────────────

def render_revenue(s: pd.DataFrame, tenant_name: str, error=None, cur="$") -> html.Div:
    if error:
        return html.Div(dbc.Alert(f"Error: {error}", color="danger"), style={"padding": "1rem"})

    if s.empty:
        return html.Div([
            _section_header(f"{tenant_name} — Revenue"),
            _empty_state("Revenue Analysis", "No revenue data uploaded yet.", "—"),
        ], style={"padding": "0.5rem"})

    rev  = s["net_amount"].sum()
    txns = len(s)
    avg  = rev / txns if txns else 0
    d_rev = _period_delta(s)

    kpi_row = html.Div([
        _kpi_card("Total Revenue",   _fmt(rev, cur),      d_rev, color=C_BLUE),
        _kpi_card("Transactions",    f"{txns:,}",          None,  color=C_INDIGO),
        _kpi_card("Avg Transaction", _fmt(avg, cur),       None,  color=C_TEAL),
    ], style={"display": "flex", "gap": "0.7rem", "marginBottom": "1.1rem", "flexWrap": "wrap"})

    mon = _monthly(s)

    if not mon.empty:
        # Monthly bar — group by branch if multiple
        if "branch" in s.columns and s["branch"].nunique() > 1:
            d = s.copy().dropna(subset=["bill_date"])
            d["month"] = d["bill_date"].dt.to_period("M").astype(str)
            mon_br = d.groupby(["month", "branch"], as_index=False)["net_amount"].sum()
            fig_bar = px.bar(
                mon_br, x="month", y="net_amount", color="branch",
                barmode="group", color_discrete_sequence=PALETTE_PRIMARY,
                labels={"net_amount": "Revenue", "month": "", "branch": ""},
            )
            fig_bar.update_layout(
                yaxis=dict(tickprefix=cur, tickformat=",.0f"), bargap=0.22,
            )
        else:
            fig_bar = go.Figure(go.Bar(
                x=mon["month"], y=mon["net_amount"],
                marker_color=C_BLUE, marker_opacity=0.85,
                hovertemplate="%{x}<br><b>" + cur + "%{y:,.0f}</b><extra></extra>",
            ))
            fig_bar.update_layout(
                yaxis=dict(tickprefix=cur, tickformat=",.0f"),
                bargap=0.28, xaxis_title="", yaxis_title="",
            )

        # Cumulative
        mon["cumulative"] = mon["net_amount"].cumsum()
        fig_cum = go.Figure()
        fig_cum.add_bar(
            x=mon["month"], y=mon["net_amount"],
            marker_color="#BFDBFE", name="Monthly",
            hovertemplate="%{x}<br><b>" + cur + "%{y:,.0f}</b><extra></extra>",
        )
        fig_cum.add_scatter(
            x=mon["month"], y=mon["cumulative"],
            mode="lines+markers", name="Cumulative",
            line=dict(color=C_BLUE, width=2.5),
            marker=dict(size=5, color=C_BLUE),
            hovertemplate="%{x}<br>Cumulative: <b>" + cur + "%{y:,.0f}</b><extra></extra>",
        )
        fig_cum.update_layout(
            yaxis=dict(tickprefix=cur, tickformat=",.0f"),
            bargap=0.28, xaxis_title="", yaxis_title="",
        )

        bar_card = _card("Monthly Revenue", _fig(fig_bar, 280))
        cum_card = _card("Cumulative Revenue", _fig(fig_cum, 280))
    else:
        bar_card = _empty_state("Monthly Revenue", "Dates not detected in data.")
        cum_card = html.Div()

    # YoY comparison
    yoy = _yoy_data(s)
    if not yoy.empty:
        years = sorted(yoy["year"].unique())
        prev_yr, curr_yr = years[0], years[1]
        prev_df = yoy[yoy["year"] == prev_yr]
        curr_df = yoy[yoy["year"] == curr_yr]

        def _fill12(df):
            full = pd.DataFrame({"month_num": range(1, 13)})
            m = full.merge(df[["month_num", "net_amount"]], on="month_num", how="left").fillna(0)
            return m["net_amount"].tolist()

        fig_yoy = go.Figure()
        fig_yoy.add_bar(
            name=str(prev_yr), x=MONTH_ABBR, y=_fill12(prev_df),
            marker_color="#BFDBFE",
            hovertemplate="%{x} " + str(prev_yr) + "<br><b>" + cur + "%{y:,.0f}</b><extra></extra>",
        )
        fig_yoy.add_bar(
            name=str(curr_yr), x=MONTH_ABBR, y=_fill12(curr_df),
            marker_color=C_BLUE,
            hovertemplate="%{x} " + str(curr_yr) + "<br><b>" + cur + "%{y:,.0f}</b><extra></extra>",
        )
        fig_yoy.update_layout(
            barmode="group", bargap=0.2, bargroupgap=0.06,
            yaxis=dict(tickprefix=cur, tickformat=",.0f"),
            xaxis_title="", yaxis_title="",
        )
        yoy_card = _card(
            "Year-over-Year Revenue",
            _fig(fig_yoy, 260),
            badge=f"{prev_yr} vs {curr_yr}",
        )
    else:
        yoy_card = html.Div()

    # Customer table
    if "supplier_name" in s.columns:
        cust_agg = (
            s.groupby("supplier_name", as_index=False)
             .agg(txns=("net_amount", "count"), total=("net_amount", "sum"))
             .sort_values("total", ascending=False)
             .head(50)
        )
        total_rev = s["net_amount"].sum()
        cust_agg["share"] = (
            (cust_agg["total"] / total_rev * 100).round(1).map("{:.1f}%".format)
            if total_rev else "—"
        )
        cust_agg["total_fmt"] = cust_agg["total"].map(lambda v: _fmt(v, cur))
        disp = cust_agg[["supplier_name", "txns", "total_fmt", "share"]].copy()
        disp.columns = ["Customer / Client", "Transactions", "Revenue", "Share"]
        cust_card = _card("Revenue by Customer", _styled_table(disp))
    else:
        cust_card = html.Div()

    return html.Div([
        _section_header(f"{tenant_name} — Revenue Analysis"),
        kpi_row,
        html.Div([
            html.Div(bar_card, style={"flex": "1", "minWidth": "280px"}),
            html.Div(cum_card, style={"flex": "1", "minWidth": "280px"}),
        ], style={"display": "flex", "gap": "0.75rem", "flexWrap": "wrap"}),
        yoy_card,
        cust_card,
    ], style={"padding": "0.15rem 0.5rem 0.5rem"})


# ── Costs tab ─────────────────────────────────────────────────

def render_costs(p: pd.DataFrame, s: pd.DataFrame, tenant_name: str,
                 error=None, cur="$") -> html.Div:
    if error:
        return html.Div(dbc.Alert(f"Error: {error}", color="danger"), style={"padding": "1rem"})

    if p.empty:
        return html.Div([
            _section_header(f"{tenant_name} — Costs & Purchases"),
            html.Div([
                html.Div("No cost data uploaded yet.",
                         style={"textAlign": "center", "color": C_SLATE, "fontWeight": 600,
                                "padding": "2rem 0 0.5rem"}),
                html.P("Upload a vendor invoice, expense report, or purchase file to analyse costs.",
                       style={"textAlign": "center", "color": C_MUTED, "fontSize": "0.85rem"}),
            ], style={
                "background": C_WHITE, "borderRadius": "10px", "padding": "3rem 2rem",
                "border": f"1px solid {C_BORDER}",
            }),
        ], style={"padding": "0.5rem"})

    costs  = p["net_amount"].sum()
    rev    = s["net_amount"].sum() if not s.empty else 0
    margin = ((rev - costs) / rev * 100) if rev > 0 else 0

    kpi_row = html.Div([
        _kpi_card("Total Costs",  _fmt(costs, cur), None, color=C_AMBER),
        _kpi_card("Gross Margin", f"{margin:.1f}%", None, color=C_GREEN if margin >= 20 else C_AMBER),
        _kpi_card("Cost Entries", f"{len(p):,}",    None, color=C_INDIGO),
    ], style={"display": "flex", "gap": "0.7rem", "marginBottom": "1.1rem", "flexWrap": "wrap"})

    mon = _monthly(p)
    if not mon.empty:
        fig_cost = go.Figure(go.Bar(
            x=mon["month"], y=mon["net_amount"],
            marker_color=C_AMBER, marker_opacity=0.85,
            hovertemplate="%{x}<br><b>" + cur + "%{y:,.0f}</b><extra></extra>",
        ))
        fig_cost.update_layout(
            yaxis=dict(tickprefix=cur, tickformat=",.0f"),
            bargap=0.28, xaxis_title="", yaxis_title="",
        )
        cost_chart = _fig(fig_cost, 260)
    else:
        cost_chart = html.Div("Dates not detected.", style={"color": C_MUTED, "padding": "2rem"})

    if "supplier_name" in p.columns:
        top_v = (
            p.groupby("supplier_name", as_index=False)["net_amount"]
             .sum().nlargest(10, "net_amount")
        )
        fig_v = go.Figure(go.Bar(
            x=top_v["net_amount"], y=top_v["supplier_name"],
            orientation="h",
            marker=dict(
                color=top_v["net_amount"],
                colorscale=[[0, "#FED7AA"], [1, C_AMBER]],
                showscale=False,
            ),
            hovertemplate="%{y}<br><b>" + cur + "%{x:,.0f}</b><extra></extra>",
        ))
        fig_v.update_layout(
            yaxis={"categoryorder": "total ascending"},
            xaxis=dict(tickprefix=cur, tickformat=",.0f"),
            xaxis_title="", yaxis_title="",
        )
        vendor_card = _card("Top 10 Vendors by Cost", _fig(fig_v, 310))
    else:
        vendor_card = html.Div()

    return html.Div([
        _section_header(f"{tenant_name} — Costs & Purchases"),
        kpi_row,
        html.Div([
            html.Div(_card("Monthly Costs", cost_chart),
                     style={"flex": "1", "minWidth": "280px"}),
            html.Div(vendor_card, style={"flex": "1", "minWidth": "280px"}),
        ], style={"display": "flex", "gap": "0.75rem", "flexWrap": "wrap"}),
    ], style={"padding": "0.15rem 0.5rem 0.5rem"})


# ── Customers tab ─────────────────────────────────────────────────────────────

def render_customers(s, tenant_name, error=None, cur="$"):
    if error:
        return html.Div(dbc.Alert(f"Error: {error}", color="danger"), style={"padding": "1rem"})

    if s.empty or "supplier_name" not in s.columns:
        return html.Div([
            _section_header(f"{tenant_name} — Customer Analysis"),
            _empty_state("Customer Analysis", "No customer data detected in the uploaded file."),
        ], style={"padding": "0.15rem 0.5rem 0.5rem"})

    agg = (
        s.groupby("supplier_name", as_index=False)
         .agg(txns=("net_amount", "count"), total=("net_amount", "sum"))
         .sort_values("total", ascending=False)
    )
    total_rev = s["net_amount"].sum()
    n_custs   = len(agg)
    top_cust  = agg.iloc[0]["supplier_name"] if not agg.empty else "—"
    top_rev   = agg.iloc[0]["total"]         if not agg.empty else 0
    top_share = (top_rev / total_rev * 100)  if total_rev else 0
    agg["share_pct"] = (agg["total"] / total_rev * 100).round(1) if total_rev else 0.0

    kpi_row = html.Div([
        _kpi_card("Total Customers",   f"{n_custs:,}",         None, color=C_INDIGO),
        _kpi_card("Top Customer",      top_cust,                None, color=C_BLUE),
        _kpi_card("Top Customer Share", f"{top_share:.1f}%",   None, color=C_SKY),
    ], style={"display": "flex", "gap": "0.7rem", "marginBottom": "1.1rem", "flexWrap": "wrap"})

    top20 = agg.head(20)
    fig_pareto = go.Figure(go.Bar(
        x=top20["supplier_name"], y=top20["total"],
        marker=dict(
            color=top20["total"],
            colorscale=[[0, "#BFDBFE"], [1, C_BLUE]],
            showscale=False,
        ),
        hovertemplate="%{x}<br><b>" + cur + "%{y:,.0f}</b><extra></extra>",
    ))
    fig_pareto.update_layout(
        yaxis=dict(tickprefix=cur, tickformat=",.0f"),
        xaxis=dict(tickangle=-40),
        bargap=0.28, xaxis_title="", yaxis_title="",
    )

    pie_df = agg.head(8).copy()
    if len(agg) > 8:
        others = pd.DataFrame([{
            "supplier_name": "Others",
            "total": agg.iloc[8:]["total"].sum(),
        }])
        pie_df = pd.concat([pie_df, others], ignore_index=True)

    fig_pie = go.Figure(go.Pie(
        labels=pie_df["supplier_name"],
        values=pie_df["total"],
        hole=0.45,
        marker=dict(colors=PALETTE_PRIMARY + ["#CBD5E1"]),
        textinfo="percent",
        hovertemplate="%{label}<br><b>" + cur + "%{value:,.0f}</b> (%{percent})<extra></extra>",
    ))
    fig_pie.update_layout(
        showlegend=True,
        legend=dict(orientation="v", font=dict(size=10), x=1.02, y=1, xanchor="left"),
        margin=dict(l=0, r=120, t=20, b=0),
    )

    disp = agg.copy()
    try:
        if "bill_date" in s.columns:
            date_agg = s.groupby("supplier_name")["bill_date"].agg(["min", "max"]).reset_index()
            date_agg.columns = ["supplier_name", "first", "last"]
            date_agg["first"] = date_agg["first"].dt.strftime("%Y-%m-%d")
            date_agg["last"]  = date_agg["last"].dt.strftime("%Y-%m-%d")
            disp = disp.merge(date_agg, on="supplier_name", how="left")
        else:
            disp["first"] = "—"
            disp["last"]  = "—"
    except Exception:
        disp["first"] = "—"
        disp["last"]  = "—"
    disp["total_fmt"] = disp["total"].map(lambda v: _fmt(v, cur))
    disp["share_fmt"] = disp["share_pct"].map("{:.1f}%".format)
    tbl_data = disp[["supplier_name", "txns", "total_fmt", "share_fmt", "first", "last"]].copy()
    tbl_data.columns = ["Customer", "Transactions", "Revenue", "Share %", "First Sale", "Last Sale"]

    return html.Div([
        _section_header(
            f"{tenant_name} — Customer Analysis",
            f"{n_custs:,} customers · {_fmt(total_rev, cur)} total revenue",
        ),
        kpi_row,
        html.Div([
            html.Div(_card("Revenue by Client (Top 20)", _fig(fig_pareto, 310)),
                     style={"flex": "2", "minWidth": "300px"}),
            html.Div(_card("Revenue Distribution", _fig(fig_pie, 310)),
                     style={"flex": "1", "minWidth": "220px"}),
        ], style={"display": "flex", "gap": "0.75rem", "flexWrap": "wrap"}),
        _card("All Customers", _styled_table(tbl_data)),
    ], style={"padding": "0.15rem 0.5rem 0.5rem"})


# ── Cash Flow tab ────────────────────────────────────────────────────────────

def render_cashflow(s: pd.DataFrame, p: pd.DataFrame, tenant_name: str,
                    error=None, cur="$") -> html.Div:
    """Net cash flow: revenue − purchases, month-by-month."""
    if error:
        return html.Div(dbc.Alert(f"Error: {error}", color="danger"), style={"padding": "1rem"})

    rev  = s["net_amount"].sum()  if not s.empty and "net_amount" in s.columns  else 0
    cost = p["net_amount"].sum()  if not p.empty and "net_amount" in p.columns  else 0
    net  = rev - cost

    kpi_row = html.Div([
        _kpi_card("Total Revenue",   _fmt(rev,  cur), None, color=C_GREEN),
        _kpi_card("Total Costs",     _fmt(cost, cur), None, color=C_AMBER),
        _kpi_card("Net Cash Flow",   _fmt(net,  cur), None,
                  color=C_GREEN if net >= 0 else C_RED),
    ], style={"display": "flex", "gap": "0.7rem", "marginBottom": "1.1rem", "flexWrap": "wrap"})

    # Monthly waterfall
    try:
        s_mon = _monthly(s).rename(columns={"net_amount": "revenue"}) if not s.empty else pd.DataFrame()
        p_mon = _monthly(p).rename(columns={"net_amount": "cost"})    if not p.empty else pd.DataFrame()
        if not s_mon.empty and not p_mon.empty:
            cf = s_mon.merge(p_mon, on="month", how="outer").fillna(0)
        elif not s_mon.empty:
            cf = s_mon.copy(); cf["cost"] = 0
        elif not p_mon.empty:
            cf = p_mon.copy(); cf["revenue"] = 0
        else:
            cf = pd.DataFrame()

        if not cf.empty:
            cf["net"] = cf["revenue"] - cf["cost"]
            fig_cf = go.Figure()
            fig_cf.add_trace(go.Bar(
                x=cf["month"], y=cf["revenue"], name="Revenue",
                marker_color=C_GREEN, opacity=0.82,
                hovertemplate="%{x}<br>Revenue: <b>" + cur + "%{y:,.0f}</b><extra></extra>",
            ))
            fig_cf.add_trace(go.Bar(
                x=cf["month"], y=cf["cost"], name="Cost",
                marker_color=C_AMBER, opacity=0.82,
                hovertemplate="%{x}<br>Cost: <b>" + cur + "%{y:,.0f}</b><extra></extra>",
            ))
            fig_cf.add_trace(go.Scatter(
                x=cf["month"], y=cf["net"], name="Net",
                mode="lines+markers",
                line=dict(color=C_BLUE, width=2, dash="dot"),
                marker=dict(size=6),
                hovertemplate="%{x}<br>Net: <b>" + cur + "%{y:,.0f}</b><extra></extra>",
            ))
            fig_cf.update_layout(
                barmode="group", bargap=0.22,
                yaxis=dict(tickprefix=cur, tickformat=",.0f"),
                legend=dict(orientation="h", x=0, y=1.12),
            )
            cf_chart = _card("Monthly Revenue vs Cost", _fig(fig_cf, 320))
        else:
            cf_chart = html.Div()
    except Exception:
        cf_chart = html.Div()

    return html.Div([
        _section_header(f"{tenant_name} — Cash Flow"),
        kpi_row,
        cf_chart,
    ], style={"padding": "0.15rem 0.5rem 0.5rem"})


# ── Public entry point ────────────────────────────────────────────────────────

def render_tab(tab, s, p, tenant_name, error=None, cur="$"):
    """Route to the correct renderer by tab value."""
    if tab == "overview":
        return render_overview(s, p, tenant_name, error, cur)
    elif tab in ("sales", "revenue"):
        return render_revenue(s, tenant_name, error, cur)
    elif tab in ("purchases", "costs"):
        return render_costs(p, s, tenant_name, error, cur)
    elif tab in ("compare", "customers"):
        return render_customers(s, tenant_name, error, cur)
    elif tab == "cashflow":
        return render_cashflow(s, p, tenant_name, error, cur)
    else:
        return render_overview(s, p, tenant_name, error, cur)
