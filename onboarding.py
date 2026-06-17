"""
onboarding.py  —  InsightHub Tenant Onboarding Wizard + Upload Rollback
========================================================================
Onboarding Wizard (5 steps):
  Step 1 — Business Profile (name, domain, timezone, currency)
  Step 2 — Data Source  (upload CSV/Excel or connect integration)
  Step 3 — Alert Config (email, WhatsApp, SMS channels)
  Step 4 — Billing Plan (Razorpay or Stripe checkout)
  Step 5 — Go Live      (checklist confirmation)

Upload Rollback:
  render_upload_rollback_tab(engine, tenant_id) — table of uploads with Rollback button

This module renders Dash layouts. Callbacks live in app.py.
"""

import pandas as pd
from dash import html, dcc
import dash_bootstrap_components as dbc
from typing import Any, Optional

C_GREEN  = "#1e7e4b"
C_BLUE   = "#0d6efd"
C_ORANGE = "#fd7e14"
C_GRAY   = "#6b7280"
C_RED    = "#dc3545"
C_PURPLE = "#6f42c1"


# ═════════════════════════════════════════════════════════════════════════════
# ONBOARDING WIZARD
# ═════════════════════════════════════════════════════════════════════════════

STEPS = [
    {"num": 1, "icon": "🏢", "title": "Business Profile"},
    {"num": 2, "icon": "📁", "title": "Data Source"},
    {"num": 3, "icon": "🔔", "title": "Alert Config"},
    {"num": 4, "icon": "💳", "title": "Choose Plan"},
    {"num": 5, "icon": "🚀", "title": "Go Live"},
]


def render_onboarding_wizard(
    current_step: int = 1,
    tenant_id:    Optional[int] = None,
    engine:       Any = None,
    error:        Optional[str] = None,
    prefill:      Optional[dict] = None,
) -> html.Div:
    """Render the full onboarding wizard at the given step."""
    pf = prefill or {}

    # ── Step progress bar ──
    progress_items = []
    for s in STEPS:
        is_done    = s["num"] < current_step
        is_current = s["num"] == current_step
        color = (C_GREEN if is_done else (C_BLUE if is_current else "#e2e8f0"))
        text_color = "#fff" if (is_done or is_current) else C_GRAY
        progress_items.append(
            html.Div([
                html.Div(
                    "✓" if is_done else s["icon"],
                    style={"width":"36px","height":"36px","borderRadius":"50%",
                           "background":color,"color":text_color,
                           "display":"flex","alignItems":"center","justifyContent":"center",
                           "fontSize":"1rem","fontWeight":700,"flexShrink":0},
                ),
                html.Div([
                    html.Div(f"Step {s['num']}", style={"fontSize":"0.65rem","color":C_GRAY}),
                    html.Div(s["title"], style={"fontSize":"0.8rem","fontWeight":600,
                                                "color": C_GREEN if is_done else
                                                         (C_BLUE if is_current else C_GRAY)}),
                ], style={"marginLeft":"0.5rem"}),
            ], style={"display":"flex","alignItems":"center","flex":1,
                      "opacity":"1" if (is_done or is_current) else "0.45"})
        )
        if s["num"] < len(STEPS):
            progress_items.append(
                html.Div(style={"flex":"0 0 24px","height":"2px",
                                "background": C_GREEN if is_done else "#e2e8f0",
                                "margin":"0 0.25rem","alignSelf":"center"})
            )

    progress_bar = html.Div(progress_items,
                            style={"display":"flex","alignItems":"center",
                                   "padding":"1.2rem 1.5rem","background":"#fff",
                                   "borderRadius":"12px","marginBottom":"1.5rem",
                                   "boxShadow":"0 1px 4px rgba(0,0,0,0.07)",
                                   "overflowX":"auto"})

    # ── Step content ──
    step_content = {
        1: _step1_profile,
        2: _step2_datasource,
        3: _step3_alerts,
        4: _step4_billing,
        5: _step5_golive,
    }.get(current_step, lambda **_: html.Div("Invalid step"))

    content = step_content(tenant_id=tenant_id, engine=engine,
                           error=error, prefill=pf)

    return html.Div([
        html.Div([
            html.H4("Account Setup",
                    style={"margin":0,"fontWeight":700,"color":C_GREEN}),
            html.Span(f"Step {current_step} of {len(STEPS)} — {STEPS[current_step-1]['title']}",
                      style={"fontSize":"0.78rem","color":C_GRAY}),
        ], style={"marginBottom":"1.2rem"}),
        progress_bar,
        content,
    ])


def _step1_profile(tenant_id=None, engine=None, error=None, prefill=None) -> html.Div:
    pf = prefill or {}
    return _wizard_card("🏢 Business Profile", [
        html.P("Tell us about your business so we can personalise your dashboard.",
               style={"color":C_GRAY,"fontSize":"0.85rem"}),
        _field("Business Name",    "onb-name",      pf.get("name",""),    "MedStar Pharmacy"),
        _field("Owner Email",      "onb-email",     pf.get("email",""),   "owner@mybusiness.com"),
        _field("Owner Phone",      "onb-phone",     pf.get("phone",""),   "+91 98765 43210"),
        html.Div([
            html.Div([
                html.Label("Business Domain", style=_label_style()),
                dcc.Dropdown(
                    id="onb-domain",
                    options=[
                        {"label":"Pharmacy",         "value":"pharmacy"},
                        {"label":"Retail",           "value":"retail"},
                        {"label":"Food & Beverage",  "value":"fnb"},
                        {"label":"Manufacturing",    "value":"manufacturing"},
                        {"label":"Finance / CA",     "value":"finance"},
                        {"label":"Professional Svc", "value":"services"},
                        {"label":"Other",            "value":"generic"},
                    ],
                    value=pf.get("domain","pharmacy"),
                    clearable=False, style={"fontSize":"0.85rem"},
                ),
            ], style={"flex":1}),
            html.Div([
                html.Label("Currency", style=_label_style()),
                dcc.Dropdown(
                    id="onb-currency",
                    options=[
                        {"label":"₹ Indian Rupee (INR)","value":"INR"},
                        {"label":"$ US Dollar (USD)",   "value":"USD"},
                        {"label":"£ British Pound (GBP)","value":"GBP"},
                    ],
                    value=pf.get("currency","INR"),
                    clearable=False, style={"fontSize":"0.85rem"},
                ),
            ], style={"flex":"0 0 200px"}),
        ], style={"display":"flex","gap":"1rem","marginBottom":"1rem"}),
        _error(error),
        dbc.Button("Next: Data Source →", id="onb-step1-next",
                   color="success", style={"fontWeight":600,"width":"100%","marginTop":"0.5rem"}),
    ])


def _step2_datasource(tenant_id=None, engine=None, error=None, prefill=None) -> html.Div:
    return _wizard_card("📁 Connect Your Data", [
        html.P("Choose how to get your business data into InsightHub.",
               style={"color":C_GRAY,"fontSize":"0.85rem"}),
        # Option cards
        html.Div([
            _option_card("📤", "Upload Excel / CSV",
                         "Upload your Marg, Tally, QuickBooks, or custom export file.",
                         "onb-src-upload", active=True),
            _option_card("🔗", "Connect QuickBooks",
                         "OAuth 2.0 connection — pulls P&L and sales tax automatically.",
                         "onb-src-qb"),
            _option_card("📂", "Marg ERP File Drop",
                         "Point a folder on your server — we watch it and auto-import.",
                         "onb-src-marg"),
        ], style={"display":"grid","gridTemplateColumns":"1fr 1fr 1fr","gap":"0.75rem",
                  "marginBottom":"1.2rem"}),
        # Upload section (shown by default)
        html.Div([
            dcc.Upload(
                id="onb-upload",
                children=html.Div([
                    html.Div("📁", style={"fontSize":"2rem"}),
                    html.Div("Drag & drop or click to upload",
                             style={"fontWeight":600,"fontSize":"0.88rem","marginTop":"4px"}),
                    html.Div("Supports: .xlsx, .xls, .csv",
                             style={"fontSize":"0.75rem","color":C_GRAY}),
                ]),
                style={"border":"2px dashed #e2e8f0","borderRadius":"10px",
                       "textAlign":"center","padding":"1.5rem","cursor":"pointer",
                       "background":"#fafbff"},
                multiple=False,
            ),
            html.Div(id="onb-upload-feedback"),
        ], id="onb-upload-section"),
        _error(error),
        html.Div([
            dbc.Button("← Back", id="onb-step2-back", color="light",
                       style={"fontSize":"0.85rem"}),
            dbc.Button("Next: Alerts →", id="onb-step2-next", color="success",
                       style={"fontWeight":600,"marginLeft":"auto"}),
        ], style={"display":"flex","marginTop":"1rem"}),
    ])


def _step3_alerts(tenant_id=None, engine=None, error=None, prefill=None) -> html.Div:
    pf = prefill or {}
    return _wizard_card("🔔 Alert Channels", [
        html.P("Configure where you want to receive daily digests and threshold alerts.",
               style={"color":C_GRAY,"fontSize":"0.85rem"}),
        # Email
        html.Div([
            html.Div([
                html.Span("📧 Email", style={"fontWeight":700,"fontSize":"0.88rem"}),
                dbc.Switch(id="onb-alert-email-en", value=True, style={"marginLeft":"auto"}),
            ], style={"display":"flex","alignItems":"center","marginBottom":"0.5rem"}),
            _field("Email Address", "onb-alert-email", pf.get("email",""), "owner@example.com"),
        ], style={"background":"#f8fafc","borderRadius":"8px",
                  "padding":"1rem","marginBottom":"0.75rem"}),
        # WhatsApp
        html.Div([
            html.Div([
                html.Span("💬 WhatsApp", style={"fontWeight":700,"fontSize":"0.88rem"}),
                html.Span(" (India)", style={"fontSize":"0.72rem","color":C_GRAY}),
                dbc.Switch(id="onb-alert-wa-en", value=False, style={"marginLeft":"auto"}),
            ], style={"display":"flex","alignItems":"center","marginBottom":"0.5rem"}),
            _field("WhatsApp Number", "onb-alert-wa", pf.get("phone",""), "+919876543210"),
        ], style={"background":"#f8fafc","borderRadius":"8px",
                  "padding":"1rem","marginBottom":"0.75rem"}),
        # SMS (Twilio)
        html.Div([
            html.Div([
                html.Span("📱 SMS", style={"fontWeight":700,"fontSize":"0.88rem"}),
                html.Span(" (USA/Global via Twilio)", style={"fontSize":"0.72rem","color":C_GRAY}),
                dbc.Switch(id="onb-alert-sms-en", value=False, style={"marginLeft":"auto"}),
            ], style={"display":"flex","alignItems":"center","marginBottom":"0.5rem"}),
            _field("Mobile Number", "onb-alert-sms", pf.get("phone",""), "+14155238886"),
        ], style={"background":"#f8fafc","borderRadius":"8px",
                  "padding":"1rem","marginBottom":"0.75rem"}),
        html.Div([
            html.Span("Slack", style={"fontWeight":700,"fontSize":"0.88rem"}),
            dbc.Switch(id="onb-alert-slack-en", value=False, style={"marginLeft":"auto"}),
        ], style={"display":"flex","alignItems":"center","background":"#f8fafc",
                  "borderRadius":"8px","padding":"0.75rem 1rem","marginBottom":"0.75rem"}),
        _error(error),
        html.Div([
            dbc.Button("← Back", id="onb-step3-back", color="light"),
            dbc.Button("Next: Plan →", id="onb-step3-next", color="success",
                       style={"fontWeight":600,"marginLeft":"auto"}),
        ], style={"display":"flex","marginTop":"1rem"}),
    ])


def _step4_billing(tenant_id=None, engine=None, error=None, prefill=None) -> html.Div:
    pf       = prefill or {}
    currency = pf.get("currency", "INR")

    try:
        from billing import BillingEngine
        plans = BillingEngine(currency).get_plan_display()
    except Exception:
        plans = []

    plan_cards = []
    for plan in plans:
        popular = plan["plan_id"] == "growth"
        plan_cards.append(
            html.Div([
                html.Div(plan["name"], style={"fontWeight":700,"fontSize":"1rem",
                                              "color": C_GREEN if popular else "#1a1a2e"}),
                html.Div(plan["monthly"],
                         style={"fontSize":"1.3rem","fontWeight":700,"color":C_BLUE,
                                "margin":"0.4rem 0"}),
                html.Ul([html.Li(f, style={"fontSize":"0.75rem","color":C_GRAY,
                                           "marginBottom":"2px"})
                         for f in plan["features"][:4]],
                        style={"paddingLeft":"1rem","margin":"0.5rem 0 1rem"}),
                dbc.Button(
                    "Start Free Trial →" if not popular else "⭐ Most Popular — Try Free",
                    id={"type":"onb-plan-btn","plan":plan["plan_id"]},
                    color="success" if popular else "outline-success",
                    size="sm", style={"width":"100%","fontWeight":600,"fontSize":"0.8rem"},
                ),
            ], style={
                "background": "#f0fdf4" if popular else "#fff",
                "border": f"2px solid {C_GREEN}" if popular else "1.5px solid #e2e8f0",
                "borderRadius":"12px","padding":"1.2rem 1rem",
                "boxShadow": "0 2px 8px rgba(30,126,75,0.12)" if popular else "none",
            })
        )

    if not plan_cards:
        plan_cards = [html.P("Could not load plans. Please refresh or contact support.",
                             style={"color":C_GRAY})]

    return _wizard_card("💳 Choose Your Plan", [
        html.P("All plans start with a 14-day free trial. No credit card required to begin.",
               style={"color":C_GRAY,"fontSize":"0.85rem","marginBottom":"1.2rem"}),
        html.Div(plan_cards, style={"display":"grid",
                                    "gridTemplateColumns":"repeat(auto-fill,minmax(175px,1fr))",
                                    "gap":"0.75rem","marginBottom":"1.2rem"}),
        dbc.Button("← Back to Alerts", id="onb-step4-back", color="light",
                   style={"fontSize":"0.85rem"}),
    ])


def _step5_golive(tenant_id=None, engine=None, error=None, prefill=None) -> html.Div:
    checklist = [
        ("✅", "Business profile saved",      True),
        ("✅", "First data file uploaded",    True),
        ("✅", "Alert channels configured",   True),
        ("✅", "Plan selected",               True),
        ("🔐", "Enable MFA for your account", False),
        ("📊", "Explore your first dashboard", False),
    ]

    rows = []
    for icon, label, done in checklist:
        rows.append(html.Div([
            html.Span(icon, style={"fontSize":"1.1rem","marginRight":"0.5rem"}),
            html.Span(label, style={"fontSize":"0.85rem",
                                    "color":C_GREEN if done else "#374151",
                                    "textDecoration":"line-through" if done else "none"}),
            html.Span(" ✓" if done else "", style={"color":C_GREEN,"marginLeft":"auto",
                                                    "fontWeight":700}),
        ], style={"display":"flex","alignItems":"center","padding":"0.6rem 1rem",
                  "background":"#f8fafc","borderRadius":"8px","marginBottom":"0.4rem"}))

    return _wizard_card("🚀 You're Ready to Go!", [
        html.Div("🎉", style={"fontSize":"3rem","textAlign":"center","marginBottom":"0.5rem"}),
        html.P("Your InsightHub workspace is set up. Here's your launch checklist:",
               style={"color":C_GRAY,"fontSize":"0.85rem","textAlign":"center",
                      "marginBottom":"1.2rem"}),
        html.Div(rows, style={"marginBottom":"1.5rem"}),
        html.Div([
            dbc.Button("🔐 Enable MFA", href="/mfa/setup", color="outline-success",
                       style={"fontWeight":600}),
            dbc.Button("📊 Go to Dashboard", href="/", color="success",
                       style={"fontWeight":600,"marginLeft":"0.5rem"}),
        ], style={"textAlign":"center"}),
    ])


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _wizard_card(title: str, children: list) -> html.Div:
    return html.Div([
        html.Div(title, style={"fontWeight":700,"fontSize":"1rem","color":"#1a1a2e",
                               "marginBottom":"1.2rem"}),
        *children,
    ], style={"background":"#fff","borderRadius":"14px","padding":"1.8rem",
              "boxShadow":"0 2px 12px rgba(0,0,0,0.08)","maxWidth":"680px"})


def _field(label: str, field_id: str, value: str = "", placeholder: str = "") -> html.Div:
    return html.Div([
        html.Label(label, style=_label_style()),
        dbc.Input(id=field_id, value=value, placeholder=placeholder,
                  type="text", size="sm",
                  style={"fontSize":"0.85rem","marginBottom":"0.75rem"}),
    ])


def _label_style() -> dict:
    return {"fontSize":"0.78rem","color":C_GRAY,"fontWeight":600,
            "display":"block","marginBottom":"4px"}


def _error(msg: Optional[str]) -> html.Div:
    if not msg:
        return html.Div()
    return dbc.Alert(msg, color="danger", dismissable=True,
                     style={"fontSize":"0.82rem","padding":"0.5rem 0.9rem"})


def _option_card(icon: str, title: str, desc: str, id_: str, active: bool = False) -> html.Div:
    return html.Div([
        html.Div(icon, style={"fontSize":"1.8rem","marginBottom":"0.4rem"}),
        html.Div(title, style={"fontWeight":700,"fontSize":"0.83rem","marginBottom":"2px"}),
        html.Div(desc,  style={"fontSize":"0.72rem","color":C_GRAY,"lineHeight":1.4}),
    ], id=id_,
    style={
        "background": "#f0fdf4" if active else "#f8fafc",
        "border": f"2px solid {C_GREEN}" if active else "1.5px solid #e2e8f0",
        "borderRadius":"10px","padding":"1rem","cursor":"pointer",
        "textAlign":"center",
    })


# ═════════════════════════════════════════════════════════════════════════════
# UPLOAD ROLLBACK TAB
# ═════════════════════════════════════════════════════════════════════════════

def render_upload_rollback_tab(
    engine:    Any,
    tenant_id: Optional[int] = None,
) -> html.Div:
    """
    Render the upload history table with rollback buttons.
    Reads from upload_history table (already exists in data_loader.py).
    """
    history = _load_upload_history(engine, tenant_id)

    if history.empty:
        return html.Div([
            html.Div("No upload history found.",
                     style={"color":C_GRAY,"padding":"2rem","textAlign":"center"}),
        ])

    rows = []
    for _, r in history.iterrows():
        uid      = r.get("id",           "")
        fname    = r.get("filename",     "unknown")
        rtype    = r.get("report_type",  "?")
        branch   = r.get("branch",       "?")
        rows_n   = r.get("row_count",    0)
        uploaded = r.get("uploaded_at",  "")
        status   = r.get("status",       "active")

        status_badge = html.Span(
            status.upper(),
            style={"background": C_GREEN if status=="active" else "#e2e8f0",
                   "color": "#fff" if status=="active" else C_GRAY,
                   "borderRadius":"4px","padding":"2px 8px",
                   "fontSize":"0.68rem","fontWeight":700},
        )

        rollback_btn = dbc.Button(
            "↩ Rollback",
            id={"type":"rollback-btn","uid":str(uid)},
            color="outline-danger", size="sm",
            style={"fontSize":"0.72rem"},
            disabled=(status != "active"),
        ) if status == "active" else html.Span("—", style={"color":C_GRAY})

        rows.append(html.Tr([
            html.Td(str(fname)[:40], style={"fontSize":"0.78rem"}),
            html.Td(rtype,           style={"fontSize":"0.78rem"}),
            html.Td(branch,          style={"fontSize":"0.78rem"}),
            html.Td(f"{rows_n:,}",  style={"textAlign":"right","fontSize":"0.78rem"}),
            html.Td(str(uploaded)[:19], style={"fontSize":"0.78rem","color":C_GRAY}),
            html.Td(status_badge,   style={"textAlign":"center"}),
            html.Td(rollback_btn,   style={"textAlign":"center"}),
        ]))

    return html.Div([
        html.Div([
            html.H5("Upload History & Rollback",
                    style={"margin":0,"fontWeight":700,"color":C_GREEN}),
            html.Span("Rollback removes the upload and reverts the data to the previous state.",
                      style={"fontSize":"0.78rem","color":C_GRAY}),
        ], style={"marginBottom":"1rem"}),
        html.Div(id="rollback-feedback"),
        html.Div([
            html.Table([
                html.Thead(html.Tr([
                    html.Th("File"),
                    html.Th("Type"),
                    html.Th("Branch"),
                    html.Th("Rows",      style={"textAlign":"right"}),
                    html.Th("Uploaded At"),
                    html.Th("Status",    style={"textAlign":"center"}),
                    html.Th("Action",    style={"textAlign":"center"}),
                ])),
                html.Tbody(rows),
            ], style={"width":"100%","borderCollapse":"collapse","fontSize":"0.8rem"}),
        ], style={"overflowX":"auto","background":"#fff","borderRadius":"10px",
                  "padding":"1.2rem","boxShadow":"0 1px 4px rgba(0,0,0,0.07)"}),
    ])


def _load_upload_history(engine: Any, tenant_id: Optional[int] = None) -> pd.DataFrame:
    """Load upload_history table from DB."""
    if engine is None:
        return pd.DataFrame()
    try:
        from sqlalchemy import text
        query  = "SELECT * FROM upload_history"
        params = {}
        if tenant_id:
            query += " WHERE tenant_id = :tid"
            params = {"tid": tenant_id}
        query += " ORDER BY uploaded_at DESC LIMIT 50"
        with engine.connect() as conn:
            return pd.read_sql_query(text(query), conn, params=params)
    except Exception:
        return pd.DataFrame()


def do_rollback(upload_id: int, engine: Any, tenant_id: Optional[int] = None) -> tuple[bool, str]:
    """
    Roll back a specific upload:
    1. Mark the upload_history row as 'rolled_back'
    2. Delete all data rows with matching upload_id
    Returns (success, message)
    """
    if engine is None:
        return False, "No database connection."
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            # Verify ownership
            row = conn.execute(text("""
                SELECT id, report_type, tenant_id FROM upload_history WHERE id = :uid
            """), {"uid": upload_id}).fetchone()
            if not row:
                return False, f"Upload #{upload_id} not found."
            if tenant_id and row[2] and row[2] != tenant_id:
                return False, "Permission denied."

            report_type = row[1]
            table = "sales_data" if report_type == "sales" else "purchase_data"

            # Delete data rows
            conn.execute(text(f"""
                DELETE FROM {table} WHERE upload_id = :uid
            """), {"uid": upload_id})

            # Mark as rolled back
            conn.execute(text("""
                UPDATE upload_history SET status = 'rolled_back' WHERE id = :uid
            """), {"uid": upload_id})

        return True, f"Upload #{upload_id} rolled back successfully."
    except Exception as exc:
        return False, f"Rollback failed: {exc}"
