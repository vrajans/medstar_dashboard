"""
llm_settings.py  —  Tenant "AI Settings" tab
=============================================
Lets a tenant admin bring their own LLM provider + API key + model (BYO),
backed by ai.llm_gateway storage. When no BYO config is set, the tenant uses
the platform default (Groq / Llama).

Dash component IDs (callbacks registered in app.py):
    llm-provider-select · llm-model-input · llm-apikey-input · llm-baseurl-input
    llm-save-btn · llm-test-btn · llm-clear-btn · llm-status-msg · llm-current-display
"""

from __future__ import annotations
from typing import Any

from dash import html, dcc
import dash_bootstrap_components as dbc

C_NAVY  = "#1E293B"
C_BLUE  = "#2563EB"
C_GREEN = "#059669"
C_AMBER = "#D97706"

_INPUT_STYLE = {
    "background": "#0F172A", "border": "1px solid rgba(255,255,255,0.10)",
    "color": "#F1F5F9", "fontSize": "0.85rem",
}
_LABEL_STYLE = {
    "fontSize": "0.7rem", "fontWeight": 600, "textTransform": "uppercase",
    "letterSpacing": "0.07em", "color": "#94A3B8", "marginBottom": "5px",
}
_CARD_STYLE = {
    "background": C_NAVY, "border": "1px solid rgba(255,255,255,0.08)",
    "borderRadius": "12px", "padding": "1.25rem 1.5rem", "marginBottom": "1rem",
}


def _mask_key(key: str) -> str:
    if not key:
        return "—"
    if len(key) <= 8:
        return "••••"
    return f"{key[:4]}••••••••{key[-4:]}"


def render_current_config(tenant_id: int, engine: Any) -> html.Div:
    """Show the tenant's active LLM config (BYO or platform default)."""
    from ai import llm_gateway as g
    byo = None
    try:
        byo = g.get_tenant_llm(engine, int(tenant_id))
    except Exception:
        byo = None

    if byo and byo.get("api_key"):
        prov = g.PROVIDERS.get(byo["provider"], {})
        rows = [
            ("Mode", "Bring-your-own (BYO)"),
            ("Provider", prov.get("label", byo["provider"])),
            ("Model", byo.get("model") or prov.get("default_model") or "—"),
            ("API key", _mask_key(byo.get("api_key", ""))),
        ]
        if byo.get("base_url"):
            rows.append(("Endpoint", byo["base_url"]))
        badge_color, badge_text = C_GREEN, "Active · BYO"
    else:
        rows = [
            ("Mode", "Platform default"),
            ("Provider", "Groq"),
            ("Model", "llama-3.3-70b-versatile"),
            ("API key", "Managed by InsightHub"),
        ]
        badge_color, badge_text = C_BLUE, "Active · Default"

    body = [
        html.Div(
            html.Span(badge_text, style={
                "background": badge_color, "color": "white", "fontSize": "0.68rem",
                "fontWeight": 700, "padding": "3px 10px", "borderRadius": "100px",
            }),
            style={"marginBottom": "0.9rem"},
        )
    ]
    for label, val in rows:
        body.append(html.Div([
            html.Span(label, style={"color": "#94A3B8", "fontSize": "0.78rem",
                                    "width": "110px", "display": "inline-block"}),
            html.Span(val, style={"color": "#F1F5F9", "fontSize": "0.85rem", "fontWeight": 600}),
        ], style={"marginBottom": "5px"}))
    return html.Div(body, style=_CARD_STYLE)


def render_llm_settings_tab(tenant_id: int, engine: Any) -> html.Div:
    """Full AI Settings layout for a tenant."""
    from ai import llm_gateway as g
    provider_opts = [{"label": v["label"], "value": k} for k, v in g.PROVIDERS.items()]

    return html.Div(
        [
            html.Div(
                [
                    html.Div("🤖  AI / Language Model",
                             style={"fontSize": "1.1rem", "fontWeight": 700, "color": "#F1F5F9"}),
                    html.Div(
                        "Use the built-in model, or bring your own provider and key for "
                        "frontier quality or your own compliance boundary.",
                        style={"fontSize": "0.8rem", "color": "#64748B", "marginTop": "3px"},
                    ),
                ],
                style={"marginBottom": "1.25rem"},
            ),

            # Current config
            html.Div(id="llm-current-display",
                     children=render_current_config(tenant_id, engine)),

            # Status message
            html.Div(id="llm-status-msg", style={"marginBottom": "0.75rem"}),

            # Configure form
            html.Div(
                [
                    html.Div("Configure your provider",
                             style={"fontSize": "0.95rem", "fontWeight": 700,
                                    "color": "#F1F5F9", "marginBottom": "1rem"}),
                    dbc.Row(
                        [
                            dbc.Col([
                                html.Div("Provider", style=_LABEL_STYLE),
                                dcc.Dropdown(
                                    id="llm-provider-select", options=provider_opts,
                                    value="openai", clearable=False,
                                    className="dark-dropdown", style=_INPUT_STYLE),
                            ], width=4),
                            dbc.Col([
                                html.Div("Model (optional — uses provider default if blank)",
                                         style=_LABEL_STYLE),
                                dbc.Input(id="llm-model-input", type="text",
                                          placeholder="e.g. gpt-4o-mini", style=_INPUT_STYLE),
                            ], width=8),
                        ], className="g-2", style={"marginBottom": "0.8rem"}),
                    dbc.Row(
                        [
                            dbc.Col([
                                html.Div("API key", style=_LABEL_STYLE),
                                dbc.Input(id="llm-apikey-input", type="password",
                                          placeholder="sk-…", style=_INPUT_STYLE),
                            ], width=6),
                            dbc.Col([
                                html.Div("Endpoint / base URL (Azure or self-hosted only)",
                                         style=_LABEL_STYLE),
                                dbc.Input(id="llm-baseurl-input", type="text",
                                          placeholder="https://your-resource.openai.azure.com",
                                          style=_INPUT_STYLE),
                            ], width=6),
                        ], className="g-2", style={"marginBottom": "1rem"}),
                    html.Div([
                        dbc.Button("Save", id="llm-save-btn", color="primary",
                                   style={"fontWeight": 700, "background": C_BLUE,
                                          "border": "none", "marginRight": "8px"}),
                        dbc.Button("Test connection", id="llm-test-btn", outline=True,
                                   color="light", style={"fontWeight": 600, "marginRight": "8px"}),
                        dbc.Button("Reset to default", id="llm-clear-btn", outline=True,
                                   color="warning", style={"fontWeight": 600}),
                    ]),
                    html.Div([
                        html.Span("🔒 "),
                        html.Span("Your key is used only to serve your own analytics and is never "
                                  "shared across tenants. Store production keys in your vault."),
                    ], style={"fontSize": "0.75rem", "color": "#64748B", "marginTop": "0.9rem"}),
                ],
                style=_CARD_STYLE,
            ),
        ],
        style={"maxWidth": "900px", "margin": "0 auto", "padding": "0.5rem"},
    )
