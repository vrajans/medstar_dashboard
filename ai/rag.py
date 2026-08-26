"""
ai/rag.py  --  AI Chat pipeline + Dash tab
==========================================
Uses Groq Llama 3.1 (128k context window) with DIRECT DATA INJECTION --
no ChromaDB or vector database required.

Instead of semantic search, we build rich business context by injecting:
  - Daily sales summary (last 60 days)
  - Branch performance table
  - Top 10 suppliers by purchase value
  - Margin trend
  - Active alerts / anomaly flags

This fits comfortably in Llama 3.1's 128k context and gives the AI
full visibility into the actual numbers, making answers highly accurate.

Flow
----
  User question
      |
  build_data_context(sales_df, purchase_df)
      |   -- converts DataFrames to compact text tables
      |
  build_kpi_context(kpi_data)
      |   -- adds KPI snapshot (totals, margin, top branch)
      |
  Groq Llama 3.1  (up to 128k tokens context)
      |
  Answer streamed back to AI Chat tab

No external vector DB needed. Works 100% offline except for the Groq API call.

Usage
-----
from ai.rag import rag_answer, render_ai_chat_tab

answer, history = rag_answer(
    question="Which branch had the highest margin last month?",
    sales_df=sales_df,
    purchase_df=purchase_df,
    kpi_data=kpi_data,
)
"""

import logging
from typing import Optional
import pandas as pd
from dash import html, dcc
import dash_bootstrap_components as dbc

logger = logging.getLogger(__name__)

C_GREEN  = "#1e7e4b"
C_BLUE   = "#0d6efd"
C_ORANGE = "#fd7e14"
C_GRAY   = "#6b7280"
C_RED    = "#dc3545"
C_PURPLE = "#6f42c1"


# =============================================================================
# Context builders  --  DataFrame -> compact text for Groq prompt
# =============================================================================

def build_data_context(
    sales_df:    Optional[pd.DataFrame] = None,
    purchase_df: Optional[pd.DataFrame] = None,
    max_sales_rows:    int = 60,
    max_purchase_rows: int = 30,
) -> str:
    """
    Convert sales and purchase DataFrames to a compact text representation
    suitable for injection into the Groq system prompt.

    Produces sections:
      1. Daily Sales Summary (last N days)
      2. Branch Performance
      3. Top Suppliers
      4. Purchase Summary
    """
    sections = []

    # ── 1. Daily sales summary ───────────────────────────────────────────────
    if sales_df is not None and not sales_df.empty:
        try:
            s = sales_df.copy()
            if "bill_date" in s.columns:
                s["bill_date"] = pd.to_datetime(s["bill_date"], errors="coerce")
                s = s.dropna(subset=["bill_date"]).sort_values("bill_date", ascending=False)
                daily = s.groupby("bill_date").agg(
                    sales    =("net_amount",      "sum"),
                    bills    =("total_bills",     "sum") if "total_bills" in s.columns else ("net_amount", "count"),
                    margin   =("margin_pct",      "mean") if "margin_pct" in s.columns else ("net_amount", "count"),
                    cash     =("cash_sales",      "sum") if "cash_sales" in s.columns else ("net_amount", "count"),
                    credit   =("credit_sales",    "sum") if "credit_sales" in s.columns else ("net_amount", "count"),
                ).reset_index().sort_values("bill_date", ascending=False)

                rows = []
                for _, r in daily.head(max_sales_rows).iterrows():
                    rows.append(
                        f"  {str(r['bill_date'])[:10]}  sales={_fmt(r['sales'])}  "
                        f"margin={r.get('margin', 0):.1f}%  bills={int(r.get('bills', 0))}"
                        + (f"  cash={_fmt(r.get('cash',0))}  credit={_fmt(r.get('credit',0))}"
                           if "cash_sales" in s.columns else "")
                    )
                if rows:
                    sections.append(
                        f"DAILY SALES (last {len(rows)} days, most recent first):\n" +
                        "\n".join(rows)
                    )

            # ── 2. Branch performance ────────────────────────────────────────
            if "branch" in s.columns:
                branch = s.groupby("branch").agg(
                    total_sales=("net_amount", "sum"),
                    avg_margin =("margin_pct", "mean") if "margin_pct" in s.columns else ("net_amount", "count"),
                    total_bills=("total_bills","sum")  if "total_bills" in s.columns else ("net_amount", "count"),
                ).reset_index().sort_values("total_sales", ascending=False)

                rows = []
                for _, r in branch.iterrows():
                    rows.append(
                        f"  {r['branch']}:  sales={_fmt(r['total_sales'])}  "
                        f"margin={r.get('avg_margin',0):.1f}%  bills={int(r.get('total_bills',0))}"
                    )
                if rows:
                    sections.append("BRANCH PERFORMANCE:\n" + "\n".join(rows))

            # ── 3. Monthly trend ─────────────────────────────────────────────
            if "bill_date" in s.columns:
                s["month"] = pd.to_datetime(s["bill_date"], errors="coerce").dt.to_period("M")
                monthly = s.groupby("month").agg(
                    sales =("net_amount", "sum"),
                    margin=("margin_pct", "mean") if "margin_pct" in s.columns else ("net_amount", "count"),
                ).reset_index().sort_values("month", ascending=False).head(12)

                rows = [
                    f"  {str(r['month'])}:  sales={_fmt(r['sales'])}  margin={r.get('margin',0):.1f}%"
                    for _, r in monthly.iterrows()
                ]
                if rows:
                    sections.append("MONTHLY SALES TREND (last 12 months):\n" + "\n".join(rows))

        except Exception as exc:
            logger.warning("[rag] sales context build error: %s", exc)

    # ── 4. Purchase / supplier summary ───────────────────────────────────────
    if purchase_df is not None and not purchase_df.empty:
        try:
            p = purchase_df.copy()

            if "supplier_name" in p.columns and "net_amount" in p.columns:
                suppliers = p.groupby("supplier_name").agg(
                    total =("net_amount", "sum"),
                    orders=("net_amount", "count"),
                    gst   =("total_gst",  "sum") if "total_gst" in p.columns else ("net_amount", "count"),
                ).reset_index().sort_values("total", ascending=False)

                rows = []
                for _, r in suppliers.head(max_purchase_rows).iterrows():
                    row = f"  {r['supplier_name']}:  purchased={_fmt(r['total'])}  orders={int(r['orders'])}"
                    if "total_gst" in p.columns:
                        row += f"  gst={_fmt(r.get('gst', 0))}"
                    rows.append(row)
                if rows:
                    sections.append(
                        f"TOP {len(rows)} SUPPLIERS BY PURCHASE VALUE:\n" + "\n".join(rows)
                    )

            # Purchase monthly trend
            if "grn_date" in p.columns:
                p["month"] = pd.to_datetime(p["grn_date"], errors="coerce").dt.to_period("M")
                pm = p.groupby("month").agg(
                    total=("net_amount", "sum"),
                    gst  =("total_gst",  "sum") if "total_gst" in p.columns else ("net_amount", "count"),
                ).reset_index().sort_values("month", ascending=False).head(6)

                rows = [
                    f"  {str(r['month'])}:  purchases={_fmt(r['total'])}" +
                    (f"  gst={_fmt(r.get('gst',0))}" if "total_gst" in p.columns else "")
                    for _, r in pm.iterrows()
                ]
                if rows:
                    sections.append("PURCHASE MONTHLY TREND:\n" + "\n".join(rows))

        except Exception as exc:
            logger.warning("[rag] purchase context build error: %s", exc)

    if not sections:
        return "No business data available yet. Ask the user to upload their sales/purchase data."

    return "\n\n".join(sections)


def _fmt(v) -> str:
    """Format a number as a compact currency string."""
    try:
        v = float(v)
        if v >= 1_000_000: return f"${v/1_000_000:.2f}M"
        if v >= 1_000:     return f"${v/1_000:.1f}K"
        return f"${v:.0f}"
    except Exception:
        return str(v)


# =============================================================================
# Multi-Agent Platform entry point
# =============================================================================

def agent_answer(
    question:    str,
    tenant_id:   int                      = 1,
    kpi_data:    Optional[dict]           = None,
    history:     Optional[list]           = None,
    language:    str                      = "English",
    sales_df:    Optional[pd.DataFrame]   = None,
    purchase_df: Optional[pd.DataFrame]   = None,
    memory=None,
    uploaded_file_info: Optional[dict]    = None,
) -> tuple[str, list[dict], list[dict]]:
    """
    Route a question through the Multi-Agent Platform.

    Returns (answer_text, updated_chat_history, reasoning_steps).

    reasoning_steps: list of {step, thought, action, observation} dicts
    shown in the UI as expandable trace cards.
    """
    from ai.agents.router import RouterAgent

    router = RouterAgent(memory=memory)
    result = router.route(
        question=question,
        sales_df=sales_df,
        purchase_df=purchase_df,
        kpi_data=kpi_data,
        tenant_id=tenant_id,
        language=language,
        history=history or [],
        uploaded_file_info=uploaded_file_info,
    )

    # Extract updated history from analytics agent metadata if available
    updated_history = result.metadata.get("updated_history", history or [])
    if result.answer and result.agent in ("analytics", "insight", "forecast", "schema", "quality"):
        updated_history = (history or []) + [
            {"role": "user",      "content": question},
            {"role": "assistant", "content": result.answer},
        ]

    return result.answer, updated_history, result.reasoning_steps


# =============================================================================
# Legacy RAG answer function (kept for backward compatibility)
# =============================================================================

def rag_answer(
    question:    str,
    tenant_id:   int        = 1,
    kpi_data:    Optional[dict]           = None,
    history:     Optional[list]           = None,
    language:    str                      = "English",
    sales_df:    Optional[pd.DataFrame]   = None,
    purchase_df: Optional[pd.DataFrame]   = None,
) -> tuple[str, list[dict], list]:
    """
    Answer a business question using direct data injection into Groq.

    LEGACY FUNCTION — kept for backward compatibility.
    New code should use agent_answer() which routes through the full
    Multi-Agent Platform (Router → Schema/Analytics/Insight/Forecast/Quality).

    Returns (answer_text, updated_chat_history, trace_cards).
    """
    from ai.groq_client import chat, build_kpi_context

    # Build rich data context from raw DataFrames
    data_context = build_data_context(sales_df, purchase_df)

    # Build KPI summary header
    kpi_context = build_kpi_context(kpi_data or {}, rag_results=[])

    # Combine: KPI snapshot first, then full data tables
    full_context = f"{kpi_context}\n\n{data_context}"

    # Chat with Groq (full context injected into system prompt)
    answer, updated_history = chat(
        user_message=question,
        history=history or [],
        context=full_context,
        language=language,
    )
    return answer, updated_history, []   # [] = no agent trace cards


# =============================================================================
# Anomaly detection
# =============================================================================

def get_anomaly_report(
    sales_df,
    tenant_id:   int,
    tenant_name: str = "Your Business",
) -> list[dict]:
    """Run anomaly detection on the sales time series."""
    from ai.groq_client import detect_anomalies

    if sales_df is None or sales_df.empty:
        return []

    required = ["bill_date", "net_amount"]
    if not all(c in sales_df.columns for c in required):
        return []

    series_df = (
        sales_df
        .assign(bill_date=pd.to_datetime(sales_df["bill_date"], errors="coerce"))
        .dropna(subset=["bill_date"])
        .groupby("bill_date")
        .agg(
            sales =("net_amount",  "sum"),
            margin=("margin_pct",  "mean") if "margin_pct" in sales_df.columns else ("net_amount", "count"),
        )
        .reset_index()
        .sort_values("bill_date")
    )

    series = [
        {
            "date":   str(r["bill_date"])[:10],
            "sales":  float(r["sales"]),
            "margin": float(r.get("margin", 0)),
        }
        for _, r in series_df.iterrows()
    ]
    return detect_anomalies(series, tenant_name=tenant_name)


# =============================================================================
# Dash AI Chat tab layout
# =============================================================================

def render_ai_chat_tab(
    tenant_name: str = "Your Business",
    language:    str = "English",
) -> html.Div:
    """
    Render the AI Chat tab.
    Chat history stored in dcc.Store("ai-chat-history").
    Callbacks registered in app.py.
    """
    suggested = [
        "📊 How did we perform last month?",
        "🏆 Which branch had the highest margin?",
        "📉 Show me the monthly sales trend",
        "📦 Who are our top suppliers by purchase value?",
        "💰 What is our cash vs credit sales ratio?",
        "⚠️ Are there any anomalies in our sales data?",
    ]

    suggested_chips = html.Div([
        html.Button(
            s,
            id={"type": "ai-suggest-btn", "idx": i},
            n_clicks=0,
            style={
                "background": "#f0f9ff", "border": "1px solid #bfdbfe",
                "borderRadius": "20px", "padding": "6px 14px",
                "fontSize": "0.78rem", "color": C_BLUE, "cursor": "pointer",
                "fontWeight": 500, "marginRight": "6px", "marginBottom": "6px",
            },
        )
        for i, s in enumerate(suggested)
    ], style={"marginBottom": "1rem", "lineHeight": "2.2"})

    return html.Div([
        # Header row
        html.Div([
            html.Div([
                html.Span("🤖", style={"fontSize": "1.4rem", "marginRight": "0.5rem"}),
                html.Span("AI Business Assistant",
                          style={"fontWeight": 700, "fontSize": "1rem", "color": C_GREEN}),
                html.Span(" · Powered by Groq Llama 3.1",
                          style={"fontSize": "0.78rem", "color": C_GRAY, "marginLeft": "8px"}),
            ], style={"display": "flex", "alignItems": "center"}),
            # Language selector
            dcc.Dropdown(
                id="ai-language-select",
                options=[
                    {"label": "English",         "value": "English"},
                    {"label": "Tamil (தமிழ்)",   "value": "Tamil"},
                    {"label": "Hindi (हिंदी)",   "value": "Hindi"},
                ],
                value=language,
                clearable=False,
                style={"fontSize": "0.78rem", "width": "170px"},
            ),
        ], style={"display": "flex", "justifyContent": "space-between",
                  "alignItems": "center", "marginBottom": "1rem"}),

        # Agent platform indicator
        html.Div([
            html.Span("🤖 ", style={"fontSize": "0.9rem"}),
            html.Span("Multi-Agent AI Platform",
                      style={"fontSize": "0.75rem", "color": C_GREEN,
                             "fontWeight": 700, "marginRight": "8px"}),
            html.Span("· Router · Schema · Analytics · Insight · Forecast · Quality",
                      style={"fontSize": "0.72rem", "color": C_GRAY}),
        ], style={
            "background": "#f0fdf4", "border": "1px solid #bbf7d0",
            "borderRadius": "8px", "padding": "6px 12px",
            "marginBottom": "0.5rem", "display": "flex", "alignItems": "center",
        }),

        # Agent trace panel (hidden until a question is asked)
        html.Div(id="ai-agent-trace", style={"marginBottom": "0.5rem"}),

        # Chat messages area
        html.Div(
            id="ai-chat-messages",
            children=[_system_message(
                f"Hello! I'm your InsightHub AI assistant for {tenant_name}. "
                "I have full access to your sales history, branch performance, supplier data, "
                "and margin trends. Ask me anything about your business numbers."
            )],
            style={
                "height": "400px", "overflowY": "auto",
                "padding": "1rem", "background": "#f8fafc",
                "borderRadius": "10px", "border": "1px solid #e2e8f0",
                "marginBottom": "0.75rem",
            },
        ),

        # Suggested questions
        html.Div("Try asking:", style={"fontSize": "0.75rem", "color": C_GRAY,
                                        "fontWeight": 600, "marginBottom": "4px"}),
        suggested_chips,

        # Input row
        html.Div([
            dbc.Textarea(
                id="ai-chat-input",
                placeholder="Ask anything about your business data...",
                rows=2,
                style={
                    "fontSize": "0.88rem", "resize": "none",
                    "borderRadius": "10px", "border": "1.5px solid #e2e8f0",
                    "flex": 1, "padding": "0.6rem 0.9rem",
                },
            ),
            html.Div([
                dbc.Button("Send", id="ai-chat-send",
                           color="success", style={"fontWeight": 700, "width": "90px"}),
                dbc.Button("Clear", id="ai-chat-clear", color="light",
                           size="sm", style={"fontSize": "0.78rem", "marginTop": "4px"}),
            ], style={"display": "flex", "flexDirection": "column",
                      "gap": "4px", "marginLeft": "8px"}),
        ], style={"display": "flex", "alignItems": "flex-start"}),

        # Anomaly detection section
        html.Div([
            dbc.Button(
                "Detect Anomalies in My Sales Data",
                id="ai-anomaly-btn",
                color="outline-primary",
                size="sm",
                style={"fontSize": "0.78rem", "marginTop": "0.75rem"},
            ),
            html.Div(id="ai-anomaly-results"),
        ]),

    ], style={
        "background": "#fff", "borderRadius": "12px",
        "padding": "1.25rem", "boxShadow": "0 1px 4px rgba(0,0,0,0.07)",
    })


def _system_message(text: str) -> html.Div:
    return html.Div([
        html.Div("AI", style={"fontSize": "1rem", "marginRight": "0.5rem",
                               "flexShrink": 0, "marginTop": "2px"}),
        html.Div(text, style={
            "fontSize": "0.85rem", "color": "#374151", "lineHeight": 1.6,
            "background": "#fff", "borderRadius": "0 10px 10px 10px",
            "padding": "0.6rem 0.9rem", "border": "1px solid #e2e8f0",
            "boxShadow": "0 1px 2px rgba(0,0,0,0.04)",
        }),
    ], style={"display": "flex", "alignItems": "flex-start", "marginBottom": "0.75rem"})


def render_user_message(text: str) -> html.Div:
    return html.Div([
        html.Div(text, style={
            "fontSize": "0.85rem", "color": "#fff", "lineHeight": 1.6,
            "background": C_GREEN, "borderRadius": "10px 0 10px 10px",
            "padding": "0.6rem 0.9rem", "maxWidth": "80%",
        }),
        html.Div("User", style={"fontSize": "1rem", "marginLeft": "0.5rem",
                               "flexShrink": 0, "marginTop": "2px"}),
    ], style={"display": "flex", "alignItems": "flex-start",
              "justifyContent": "flex-end", "marginBottom": "0.75rem"})




def render_assistant_message(text: str, is_error: bool = False) -> html.Div:
    return html.Div([
        html.Div("AI", style={"fontSize": "1rem", "marginRight": "0.5rem",
                               "flexShrink": 0, "marginTop": "2px"}),
        html.Div(text, style={
            "fontSize": "0.85rem",
            "color":      C_RED if is_error else "#374151",
            "lineHeight": 1.6,
            "background": "#fff5f5" if is_error else "#fff",
            "borderRadius": "0 10px 10px 10px",
            "padding":    "0.6rem 0.9rem",
            "border":     f"1px solid {C_RED if is_error else '#e2e8f0'}",
            "boxShadow":  "0 1px 2px rgba(0,0,0,0.04)",
            "whiteSpace": "pre-wrap",
        }),
    ], style={"display": "flex", "alignItems": "flex-start", "marginBottom": "0.75rem"})


# =============================================================================
# Agent Reasoning Trace UI
# =============================================================================

def render_agent_trace(
    reasoning_steps: list[dict],
    agent_name: str = "analytics",
) -> html.Div:
    """
    Render the multi-agent reasoning trace as expandable step cards.

    Each card shows: THOUGHT -> ACTION -> OBSERVATION
    Step 0 is always the Router decision (which agent was selected and why).
    """
    if not reasoning_steps:
        return html.Div()

    _AGENT_COLORS = {
        "analytics": C_BLUE,
        "insight":   C_PURPLE,
        "forecast":  C_ORANGE,
        "schema":    "#0891b2",
        "quality":   C_GREEN,
        "router":    C_GRAY,
    }
    _AGENT_ICONS = {
        "analytics": "📊", "insight": "🔍", "forecast": "📈",
        "schema": "🗂️", "quality": "✅", "router": "🧭",
    }

    color = _AGENT_COLORS.get(agent_name, C_GRAY)
    icon  = _AGENT_ICONS.get(agent_name, "🤖")

    agent_badge = html.Div([
        html.Span(f"{icon} ", style={"fontSize": "0.85rem"}),
        html.Span(f"{agent_name.title()} Agent",
                  style={"fontWeight": 700, "color": color, "fontSize": "0.78rem"}),
        html.Span(
            f" · {len(reasoning_steps)} reasoning step"
            f"{'s' if len(reasoning_steps) != 1 else ''}",
            style={"color": C_GRAY, "fontSize": "0.72rem", "marginLeft": "6px"},
        ),
    ], style={"display": "flex", "alignItems": "center",
              "marginBottom": "4px", "padding": "4px 0"})

    step_cards = []
    for s in reasoning_steps:
        step_num  = s.get("step", "?")
        is_router = step_num == 0
        thought   = s.get("thought", "")
        label_txt = (
            f"🧭 Router: {thought[:80]}{'...' if len(thought) > 80 else ''}"
            if is_router
            else f"Step {step_num}: {thought[:70]}{'...' if len(thought) > 70 else ''}"
        )

        step_cards.append(html.Details([
            html.Summary(
                label_txt,
                style={
                    "fontSize": "0.75rem", "cursor": "pointer",
                    "color": C_GRAY if is_router else "#374151",
                    "fontWeight": 600 if is_router else 400,
                    "userSelect": "none",
                },
            ),
            html.Div([
                _trace_row("💭 Thought",     s.get("thought", ""),      C_GRAY),
                _trace_row("⚡ Action",      s.get("action", ""),       C_BLUE),
                _trace_row("👁️ Observation", s.get("observation", ""), color),
            ], style={"padding": "6px 8px", "fontSize": "0.75rem", "lineHeight": 1.6}),
        ], style={
            "background": "#f8fafc" if is_router else "#fff",
            "border": f"2px solid {C_GRAY}" if is_router else f"1px solid {color}22",
            "borderRadius": "6px", "padding": "6px 10px",
            "marginBottom": "4px",
        }))

    return html.Div([
        agent_badge,
        html.Div(step_cards),
    ], style={
        "background": "#fafafa",
        "border":     f"1px solid {color}44",
        "borderLeft": f"3px solid {color}",
        "borderRadius": "8px",
        "padding": "8px 12px",
        "marginBottom": "0.5rem",
    })


def _trace_row(label: str, value: str, color: str) -> html.Div:
    """One labelled row inside a reasoning trace card."""
    if not value:
        return html.Div()
    return html.Div([
        html.Span(f"{label}: ",
                  style={"fontWeight": 600, "color": color,
                         "whiteSpace": "nowrap", "minWidth": "110px"}),
        html.Span(value[:300] + ("..." if len(value) > 300 else ""),
                  style={"color": "#374151"}),
    ], style={"display": "flex", "gap": "8px", "marginBottom": "2px", "flexWrap": "wrap"})


# =============================================================================
# Anomaly results renderer
# =============================================================================

def render_anomaly_results(anomalies: list[dict]) -> html.Div:
    """Render anomaly detection results as a card list."""
    if not anomalies:
        return html.Div(
            "No significant anomalies detected in your recent sales data.",
            style={
                "color": C_GREEN, "fontSize": "0.85rem",
                "padding": "0.75rem", "background": "#f0fdf4",
                "borderRadius": "8px", "marginTop": "0.75rem",
                "border": "1px solid #bbf7d0",
            }
        )

    type_colors = {
        "spike": C_ORANGE, "drop": C_RED,
        "gap": C_ORANGE, "margin_outlier": C_RED,
    }

    cards = []
    for a in anomalies:
        atype = a.get("type", "spike")
        color = type_colors.get(atype, C_ORANGE)
        cards.append(html.Div([
            html.Div([
                html.Span(a.get("date", "?"),
                          style={"fontWeight": 700, "fontSize": "0.83rem", "color": color}),
                html.Span(f" - {atype.replace('_', ' ').title()}",
                          style={"fontSize": "0.72rem", "color": C_GRAY, "marginLeft": "4px"}),
            ]),
            html.Div(a.get("description", ""),
                     style={"fontSize": "0.8rem", "color": "#374151",
                            "marginTop": "2px", "lineHeight": 1.5}),
        ], style={
            "background": "#fff", "borderRadius": "8px",
            "padding": "0.7rem 1rem", "marginBottom": "0.5rem",
            "border": f"1px solid {color}",
            "borderLeft": f"4px solid {color}",
        }))

    return html.Div([
        html.Div(f"⚠️ {len(anomalies)} anomalies detected",
                 style={"fontWeight": 700, "fontSize": "0.88rem",
                        "color": C_ORANGE, "marginBottom": "0.5rem",
                        "marginTop": "0.75rem"}),
        *cards,
    ])
