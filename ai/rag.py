"""
ai/rag.py  —  RAG pipeline + AI Chat Dash tab
==============================================
Combines ChromaDB similarity search (embeddings.py) with
Groq LLM inference (groq_client.py) to answer natural-language
questions about a tenant's business data.

Also provides render_ai_chat_tab() — the Dash tab layout for AI Chat.

Flow
----
  User question
      ↓
  query_multi() → top-5 matching data rows from ChromaDB
      ↓
  build_kpi_context() → current KPI snapshot
      ↓
  Groq Llama 3.1 → answer with cited evidence
      ↓
  Render in AI Chat tab

Usage
-----
from ai.rag import rag_answer, render_ai_chat_tab
answer = rag_answer("Which branch had the highest sales last month?", tenant_id=3)
"""

import logging
from typing import Optional
from dash import html, dcc
import dash_bootstrap_components as dbc

logger = logging.getLogger(__name__)

C_GREEN  = "#1e7e4b"
C_BLUE   = "#0d6efd"
C_ORANGE = "#fd7e14"
C_GRAY   = "#6b7280"
C_RED    = "#dc3545"
C_PURPLE = "#6f42c1"


# ═════════════════════════════════════════════════════════════════════════════
# RAG answer generation
# ═════════════════════════════════════════════════════════════════════════════

def rag_answer(
    question:    str,
    tenant_id:   int,
    kpi_data:    Optional[dict] = None,
    history:     Optional[list] = None,
    language:    str = "English",
) -> tuple[str, list[dict]]:
    """
    Full RAG pipeline: retrieve → augment → generate.
    Returns (answer_text, updated_chat_history).
    """
    from ai.embeddings  import query_multi
    from ai.groq_client import chat, build_kpi_context

    # Step 1: Retrieve relevant rows from vector store
    rag_results = []
    try:
        rag_results = query_multi(question, tenant_id, top_k=5)
    except Exception as exc:
        logger.warning("[rag] ChromaDB query failed: %s", exc)

    # Step 2: Build context string
    context = build_kpi_context(kpi_data or {}, rag_results)

    # Step 3: Chat with Groq
    answer, updated_history = chat(
        user_message=question,
        history=history or [],
        context=context,
        language=language,
    )
    return answer, updated_history


def get_anomaly_report(
    sales_df,
    tenant_id:   int,
    tenant_name: str = "Your Business",
) -> list[dict]:
    """Run anomaly detection on the sales time series."""
    from ai.groq_client import detect_anomalies
    import pandas as pd

    if sales_df is None or sales_df.empty:
        return []

    required = ["bill_date", "net_amount"]
    if not all(c in sales_df.columns for c in required):
        return []

    series_df = sales_df.groupby("bill_date").agg(
        sales=("net_amount",  "sum"),
        margin=("margin_pct", "mean") if "margin_pct" in sales_df.columns else ("net_amount", "count"),
    ).reset_index().sort_values("bill_date")

    series = [
        {"date": str(r["bill_date"]), "sales": float(r["sales"]),
         "margin": float(r.get("margin", 0))}
        for _, r in series_df.iterrows()
    ]
    return detect_anomalies(series, tenant_name=tenant_name)


# ═════════════════════════════════════════════════════════════════════════════
# AI Chat Dash tab layout
# ═════════════════════════════════════════════════════════════════════════════

def render_ai_chat_tab(
    tenant_name: str = "Your Business",
    language:    str = "English",
) -> html.Div:
    """
    Render the AI Chat tab.
    State (chat history) is stored in dcc.Store("ai-chat-history").
    Callbacks are registered in app.py.
    """
    suggested = [
        "📊 How did we perform last month?",
        "🏆 Which branch had the highest margin?",
        "⚠️ Are there any anomalies in our sales data?",
        "📦 What are our top suppliers by purchase value?",
        "💰 What's our average cash vs credit ratio?",
        "📅 Compare this year vs last year sales",
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
    ], style={"marginBottom": "1rem", "lineHeight": "2"})

    return html.Div([
        # Header
        html.Div([
            html.Div([
                html.Span("🤖", style={"fontSize": "1.4rem", "marginRight": "0.5rem"}),
                html.Span("AI Business Assistant",
                          style={"fontWeight": 700, "fontSize": "1rem", "color": C_GREEN}),
            ], style={"display": "flex", "alignItems": "center"}),
            html.Div([
                html.Span("Powered by Groq · Llama 3.1",
                          style={"fontSize": "0.72rem", "color": C_GRAY}),
                html.Span(" · ", style={"color": "#e2e8f0", "margin": "0 4px"}),
                dcc.Dropdown(
                    id="ai-language-select",
                    options=[
                        {"label": "English",  "value": "English"},
                        {"label": "தமிழ் (Tamil)", "value": "Tamil"},
                        {"label": "हिंदी (Hindi)",  "value": "Hindi"},
                    ],
                    value=language,
                    clearable=False,
                    style={"fontSize": "0.78rem", "width": "160px", "display": "inline-block"},
                ),
            ], style={"display": "flex", "alignItems": "center", "gap": "4px"}),
        ], style={"display": "flex", "justifyContent": "space-between",
                  "alignItems": "center", "marginBottom": "1rem"}),

        # Stores
        dcc.Store(id="ai-chat-history", data=[]),
        dcc.Store(id="ai-kpi-context",  data={}),

        # Chat messages area
        html.Div(
            id="ai-chat-messages",
            children=[
                _system_message(
                    f"Hello! I'm your InsightHub AI assistant for {tenant_name}. "
                    "Ask me anything about your sales, purchases, margins, or inventory. "
                    "I can also spot anomalies and summarise your performance."
                )
            ],
            style={
                "height": "420px", "overflowY": "auto", "padding": "1rem",
                "background": "#f8fafc", "borderRadius": "10px",
                "border": "1px solid #e2e8f0", "marginBottom": "0.75rem",
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
                placeholder="Ask a question about your business data...",
                rows=2,
                style={"fontSize": "0.88rem", "resize": "none",
                       "borderRadius": "10px", "border": "1.5px solid #e2e8f0",
                       "flex": 1, "padding": "0.6rem 0.9rem"},
            ),
            html.Div([
                dbc.Button("Send", id="ai-chat-send",
                           color="success", style={"fontWeight": 700, "width": "80px"}),
                dbc.Button("Clear", id="ai-chat-clear", color="light",
                           size="sm", style={"fontSize": "0.78rem", "marginTop": "4px"}),
            ], style={"display": "flex", "flexDirection": "column",
                      "gap": "4px", "marginLeft": "8px"}),
        ], style={"display": "flex", "alignItems": "flex-start"}),

        # Anomaly detect button
        html.Div([
            dbc.Button(
                "🔍 Detect Anomalies in My Data",
                id="ai-anomaly-btn",
                color="outline-primary",
                size="sm",
                style={"fontSize": "0.78rem", "marginTop": "0.75rem"},
            ),
            html.Div(id="ai-anomaly-results"),
        ]),

    ], style={"background": "#fff", "borderRadius": "12px",
              "padding": "1.2rem", "boxShadow": "0 1px 4px rgba(0,0,0,0.07)"})


def _system_message(text: str) -> html.Div:
    return html.Div([
        html.Div("🤖", style={"fontSize": "1rem", "marginRight": "0.5rem",
                              "flexShrink": 0, "marginTop": "2px"}),
        html.Div(text, style={"fontSize": "0.85rem", "color": "#374151",
                               "lineHeight": 1.6, "background": "#fff",
                               "borderRadius": "0 10px 10px 10px",
                               "padding": "0.6rem 0.9rem",
                               "border": "1px solid #e2e8f0",
                               "boxShadow": "0 1px 2px rgba(0,0,0,0.04)"}),
    ], style={"display": "flex", "alignItems": "flex-start", "marginBottom": "0.75rem"})


def render_user_message(text: str) -> html.Div:
    return html.Div([
        html.Div(text, style={"fontSize": "0.85rem", "color": "#fff",
                               "lineHeight": 1.6, "background": C_GREEN,
                               "borderRadius": "10px 0 10px 10px",
                               "padding": "0.6rem 0.9rem",
                               "maxWidth": "80%"}),
        html.Div("👤", style={"fontSize": "1rem", "marginLeft": "0.5rem",
                               "flexShrink": 0, "marginTop": "2px"}),
    ], style={"display": "flex", "alignItems": "flex-start",
              "justifyContent": "flex-end", "marginBottom": "0.75rem"})


def render_assistant_message(text: str, is_error: bool = False) -> html.Div:
    return html.Div([
        html.Div("🤖", style={"fontSize": "1rem", "marginRight": "0.5rem",
                               "flexShrink": 0, "marginTop": "2px"}),
        html.Div(text, style={
            "fontSize": "0.85rem",
            "color": C_RED if is_error else "#374151",
            "lineHeight": 1.6,
            "background": "#fff5f5" if is_error else "#fff",
            "borderRadius": "0 10px 10px 10px",
            "padding": "0.6rem 0.9rem",
            "border": f"1px solid {C_RED if is_error else '#e2e8f0'}",
            "boxShadow": "0 1px 2px rgba(0,0,0,0.04)",
            "whiteSpace": "pre-wrap",
        }),
    ], style={"display": "flex", "alignItems": "flex-start", "marginBottom": "0.75rem"})


def render_anomaly_results(anomalies: list[dict]) -> html.Div:
    """Render anomaly detection results as a card list."""
    if not anomalies:
        return html.Div(
            "✅ No significant anomalies detected in the recent data.",
            style={"color": C_GREEN, "fontSize": "0.85rem",
                   "padding": "0.75rem", "background": "#f0fdf4",
                   "borderRadius": "8px", "marginTop": "0.75rem",
                   "border": "1px solid #bbf7d0"}
        )

    type_icons = {"spike": "📈", "drop": "📉", "gap": "⚠️", "margin_outlier": "🔴"}
    type_colors= {"spike": C_ORANGE, "drop": C_RED, "gap": C_ORANGE, "margin_outlier": C_RED}

    cards = []
    for a in anomalies:
        atype = a.get("type", "spike")
        cards.append(html.Div([
            html.Div([
                html.Span(type_icons.get(atype, "⚠️"),
                          style={"fontSize": "1.1rem", "marginRight": "0.5rem"}),
                html.Span(a.get("date", "?"),
                          style={"fontWeight": 700, "fontSize": "0.83rem",
                                 "color": type_colors.get(atype, C_ORANGE)}),
                html.Span(f" · {atype.replace('_',' ').title()}",
                          style={"fontSize": "0.72rem", "color": C_GRAY,
                                 "marginLeft": "4px"}),
            ]),
            html.Div(a.get("description", ""),
                     style={"fontSize": "0.8rem", "color": "#374151",
                            "marginTop": "2px", "lineHeight": 1.5}),
        ], style={"background": "#fff", "borderRadius": "8px",
                  "padding": "0.7rem 1rem", "marginBottom": "0.5rem",
                  "border": f"1px solid {type_colors.get(atype, '#e2e8f0')}",
                  "borderLeft": f"4px solid {type_colors.get(atype, C_ORANGE)}"}))

    return html.Div([
        html.Div(f"⚠️ {len(anomalies)} anomalies detected",
                 style={"fontWeight": 700, "fontSize": "0.88rem",
                        "color": C_ORANGE, "marginBottom": "0.5rem",
                        "marginTop": "0.75rem"}),
        *cards,
    ])
