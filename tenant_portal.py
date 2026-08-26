"""
tenant_portal.py
Dash UI components for the InsightHub Admin Portal (Tenants tab).

Provides:
  render_tenants_tab()          - full tab layout
  get_api_token()               - extract JWT from Flask session
  call_api(method, path, ...)   - HTTP helper calling FastAPI on :8000
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

import requests
from dash import dash_table, dcc, html
import dash_bootstrap_components as dbc
from flask import session as flask_session
from flask_login import current_user

# ── API client ────────────────────────────────────────────────────────────────

# In production set FASTAPI_BASE_URL in .env (e.g. https://your-api.onrender.com)
API_BASE = os.environ.get("FASTAPI_BASE_URL", "http://127.0.0.1:8000")


def get_api_token() -> Optional[str]:
    """Retrieve the FastAPI JWT access token stored in the Flask session."""
    return flask_session.get("api_access_token")


def _refresh_api_token() -> Optional[str]:
    """Attempt to get a fresh access token using the stored refresh token.
    Updates flask_session["api_access_token"] on success.
    Returns the new access token, or None on failure.
    """
    refresh_tok = flask_session.get("api_refresh_token")
    if not refresh_tok:
        return None
    try:
        resp = requests.post(
            f"{API_BASE}/auth/refresh",
            json={"refresh_token": refresh_tok},
            timeout=5,
        )
        if resp.ok:
            new_tok = resp.json().get("access_token")
            flask_session["api_access_token"] = new_tok
            return new_tok
    except Exception:
        pass
    return None


def _auto_login_api() -> Optional[str]:
    """Fallback: login to FastAPI with service-account credentials from env.

    Used when the Dash session has no token (e.g. FastAPI was offline at
    login time and has since come up).  Stores tokens in flask_session.
    """
    svc_user = os.environ.get("FASTAPI_ADMIN_USER", "admin")
    svc_pass = os.environ.get("FASTAPI_ADMIN_PASS", "admin123")
    try:
        resp = requests.post(
            f"{API_BASE}/auth/login",
            json={"username": svc_user, "password": svc_pass},
            timeout=5,
        )
        if resp.ok:
            data = resp.json()
            flask_session["api_access_token"]  = data.get("access_token")
            flask_session["api_refresh_token"] = data.get("refresh_token")
            print(f"[tenant_portal] Auto-login to FastAPI succeeded for '{svc_user}'")
            return data.get("access_token")
        else:
            print(f"[tenant_portal] Auto-login failed: {resp.status_code} {resp.text[:200]}")
    except Exception as exc:
        print(f"[tenant_portal] Auto-login error: {exc}")
    return None


def call_api(method: str, path: str, json_body: Any = None, params: dict = None) -> tuple[int, Any]:
    """Call the FastAPI service.  Returns (status_code, response_json).

    Token resolution order:
      1. Use existing token from flask_session
      2. If missing, auto-login with service-account credentials
      3. If 401, try refresh token; if refresh fails, try auto-login once more
    """
    def _do(token: Optional[str]):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return requests.request(
            method.upper(),
            f"{API_BASE}{path}",
            json=json_body,
            params=params,
            headers=headers,
            timeout=5,
        )

    try:
        token = get_api_token()
        # If no token at all, try service-account auto-login first
        if not token:
            token = _auto_login_api()

        resp = _do(token)

        # If still 401, try refresh then auto-login as last resort
        if resp.status_code == 401:
            new_tok = _refresh_api_token() or _auto_login_api()
            if new_tok:
                resp = _do(new_tok)

        try:
            data = resp.json()
        except Exception:
            data = {"detail": resp.text}
        return resp.status_code, data
    except requests.exceptions.ConnectionError:
        return 503, {"detail": "FastAPI service not reachable (is uvicorn running on :8000?)"}
    except Exception as exc:
        return 500, {"detail": str(exc)}


# ── Module display names ──────────────────────────────────────────────────────

MODULE_LABELS = {
    "sales_analytics":   "Sales Analytics",
    "purchase_analytics":"Purchase Analytics",
    "pdf_reports":       "PDF Reports",
    "data_upload":       "Data Upload",
    "threshold_alerts":  "Threshold Alerts",
    "branch_compare":    "Branch Compare",
}

DOMAIN_OPTIONS = [
    {"label": "💊 Pharmacy / Medical",     "value": "pharmacy"},
    {"label": "🛍️ Retail / E-commerce",   "value": "retail"},
    {"label": "💻 SaaS / Software",        "value": "saas"},
    {"label": "📒 Accounting / Finance",   "value": "accounting"},
    {"label": "🏥 Healthcare / Insurance", "value": "healthcare"},
    {"label": "📈 General Business",       "value": "generic"},
]

PLAN_OPTIONS = [
    {"label": "Basic",      "value": "basic"},
    {"label": "Pro",        "value": "pro"},
    {"label": "Enterprise", "value": "enterprise"},
]

PLAN_BADGE = {"basic": "secondary", "pro": "primary", "enterprise": "success"}


# ── Tenant list table ──────────────────────────────────────────────────────────

def _tenant_table(tenants: list) -> dash_table.DataTable:
    rows = [
        {
            "id":           t.get("id", ""),
            "Name":         t.get("name", ""),
            "Slug":         t.get("slug", ""),
            "Domain":       t.get("domain_type", "").capitalize(),
            "Plan":         t.get("plan", "").capitalize(),
            "Status":       "Active" if t.get("is_active") else "Inactive",
            "Contact":      t.get("contact_email", ""),
            "Created":      (t.get("created_at", "") or "")[:10],
        }
        for t in (tenants or [])
    ]
    return dash_table.DataTable(
        id="tenants-table",
        data=rows,
        columns=[{"name": c, "id": c} for c in ["Name", "Slug", "Domain", "Plan", "Status", "Contact", "Created"]],
        row_selectable="single",
        selected_rows=[],
        style_table={"overflowX": "auto"},
        style_cell={
            "fontFamily": "Segoe UI, system-ui",
            "fontSize": "0.82rem",
            "padding": "8px 12px",
            "textAlign": "left",
            "border": "1px solid #e9ecef",
        },
        style_header={
            "backgroundColor": "#f8f9fa",
            "fontWeight": "700",
            "fontSize": "0.75rem",
            "textTransform": "uppercase",
            "letterSpacing": "0.5px",
            "color": "#495057",
        },
        style_data_conditional=[
            {"if": {"filter_query": '{Status} = "Inactive"'}, "color": "#adb5bd"},
            {"if": {"state": "selected"}, "backgroundColor": "#e8f5e9", "border": "1px solid #1e7e4b"},
        ],
        page_size=10,
    )


# ── Module toggle panel ────────────────────────────────────────────────────────

def _module_panel() -> html.Div:
    return html.Div(
        id="module-toggle-panel",
        style={"display": "none"},
        children=[
            html.Hr(className="s-divider"),
            html.Div([
                html.Span("Module Toggles", className="section-heading"),
                html.Span(id="module-tenant-label",
                          style={"marginLeft": "8px", "fontSize": "0.8rem", "color": "#6c757d"}),
            ], style={"display": "flex", "alignItems": "center", "marginBottom": "12px"}),

            html.Div(id="module-toggles-grid", style={
                "display": "grid",
                "gridTemplateColumns": "repeat(3, 1fr)",
                "gap": "10px",
                "marginBottom": "12px",
            }),

            dbc.Button("Save Module Settings", id="btn-save-modules", color="success",
                       size="sm", className="me-2"),
            html.Div(id="module-save-result", style={"marginTop": "8px"}),
        ],
    )


# ── Schema mapping panel ───────────────────────────────────────────────────────

def _mapping_panel() -> html.Div:
    return html.Div(
        id="mapping-panel",
        style={"display": "none"},
        children=[
            html.Hr(className="s-divider"),
            html.Span("Schema Mapping", className="section-heading"),
            html.P("Map your source column names to InsightHub canonical columns.",
                   style={"fontSize": "0.8rem", "color": "#6c757d", "margin": "4px 0 12px"}),

            dbc.Row([
                dbc.Col([
                    dbc.Label("Entity", style={"fontSize": "0.75rem", "fontWeight": "700"}),
                    dbc.Select(
                        id="mapping-entity-select",
                        options=[
                            {"label": "Sales",     "value": "sales"},
                            {"label": "Purchases", "value": "purchases"},
                            {"label": "Inventory", "value": "inventory"},
                        ],
                        value="sales",
                        style={"fontSize": "0.82rem"},
                    ),
                ], width=3),
            ], className="mb-3"),

            html.Div(id="mapping-rows-container"),

            dbc.Button("Save Mappings", id="btn-save-mappings", color="success",
                       size="sm", className="me-2 mt-2"),
            html.Div(id="mapping-save-result", style={"marginTop": "8px"}),
        ],
    )


# ── Add tenant form ────────────────────────────────────────────────────────────

def _add_tenant_form() -> dbc.Card:
    return dbc.Card([
        dbc.CardHeader(html.Span("Onboard New Tenant", className="section-heading")),
        dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    dbc.Label("Tenant Name", style={"fontSize": "0.75rem", "fontWeight": "700"}),
                    dbc.Input(id="new-tenant-name", placeholder="e.g. Apollo Pharmacy / Acme SaaS",
                              style={"fontSize": "0.82rem"}),
                ], width=4),
                dbc.Col([
                    dbc.Label("Slug", style={"fontSize": "0.75rem", "fontWeight": "700"}),
                    dbc.Input(id="new-tenant-slug", placeholder="e.g. apollo-pharmacy or acme-saas",
                              style={"fontSize": "0.82rem"}),
                ], width=3),
                dbc.Col([
                    dbc.Label("Domain", style={"fontSize": "0.75rem", "fontWeight": "700"}),
                    dbc.Select(id="new-tenant-domain", options=DOMAIN_OPTIONS,
                               value="pharmacy", style={"fontSize": "0.82rem"}),
                ], width=2),
                dbc.Col([
                    dbc.Label("Plan", style={"fontSize": "0.75rem", "fontWeight": "700"}),
                    dbc.Select(id="new-tenant-plan", options=PLAN_OPTIONS,
                               value="basic", style={"fontSize": "0.82rem"}),
                ], width=2),
            ], className="mb-2"),
            dbc.Row([
                dbc.Col([
                    dbc.Label("Contact Email", style={"fontSize": "0.75rem", "fontWeight": "700"}),
                    dbc.Input(id="new-tenant-email", placeholder="admin@client.com", type="email",
                              style={"fontSize": "0.82rem"}),
                ], width=4),
                dbc.Col([
                    dbc.Label(" ", style={"fontSize": "0.75rem"}),
                    html.Div([
                        dbc.Button("Create Tenant", id="btn-create-tenant",
                                   color="success", size="sm", className="me-2"),
                        dbc.Button("Deactivate Selected", id="btn-deactivate-tenant",
                                   color="danger", outline=True, size="sm"),
                    ]),
                ], width=4, style={"paddingTop": "4px"}),
            ]),
            html.Div(id="tenant-action-result", style={"marginTop": "10px"}),
        ]),
    ], className="mb-3", style={"border": "1px solid #dee2e6"})


# ── Full tab layout ────────────────────────────────────────────────────────────

def render_tenants_tab(local_tenants: list = None) -> html.Div:
    """Render the full Tenants admin tab layout.

    local_tenants: pre-fetched rows from local SQLite (passed when FastAPI is offline).
    """
    # Try FastAPI first; fall back to local_tenants if offline
    status, tenants = call_api("GET", "/tenants")
    if not isinstance(tenants, list):
        # FastAPI offline — use locally saved tenants if provided
        tenants = []
        if local_tenants:
            # Convert local_tenants row dicts to the FastAPI response shape
            tenants = [
                {
                    "id":           r.get("id", ""),
                    "name":         r.get("Name", ""),
                    "slug":         r.get("Slug", ""),
                    "domain_type":  r.get("Domain", "pharmacy").lower(),
                    "plan":         r.get("Plan", "basic").lower(),
                    "is_active":    r.get("Status", "Active") == "Active",
                    "contact_email": r.get("Contact", ""),
                    "created_at":   r.get("Created", ""),
                }
                for r in local_tenants
            ]

    count = len(tenants)

    return html.Div([
        # Header
        dbc.Row([
            dbc.Col([
                html.H5([
                    "Tenant Management ",
                    dbc.Badge(f"{count} tenant{'s' if count != 1 else ''}",
                              color="primary", className="ms-1"),
                ], className="mb-0"),
                html.P("Onboard clients, configure modules, and map data schemas.",
                       style={"fontSize": "0.82rem", "color": "#6c757d", "margin": "2px 0 0"}),
            ]),
        ], className="mb-3"),

        # Tenant table
        dbc.Card([
            dbc.CardBody([
                _tenant_table(tenants),
                dcc.Store(id="selected-tenant-store", data=None),
            ]),
        ], className="mb-3", style={"border": "1px solid #dee2e6"}),

        # Module toggle + mapping panels (hidden until tenant selected)
        _module_panel(),
        _mapping_panel(),

        # Onboarding form
        _add_tenant_form(),
    ], style={"padding": "0 8px"})
