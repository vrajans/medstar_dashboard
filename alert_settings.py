"""
alert_settings.py — US-205
Tenant alert channel configuration UI (Dash component + Flask routes).

Tenant admins can add / remove alert channels:
  • email     → email address
  • sms       → E.164 phone number  (+1XXXXXXXXXX)
  • whatsapp  → E.164 phone number
  • slack     → incoming webhook URL

Dash component IDs (registered as callbacks in app.py):
  alerts-channel-select
  alerts-recipient-input
  alerts-label-input
  alerts-add-btn
  alerts-status-msg
  alerts-channels-display
  alerts-delete-store       (dcc.Store for delete requests)
"""

from __future__ import annotations
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Dash / DBC ────────────────────────────────────────────────────────────────
from dash import html, dcc
import dash_bootstrap_components as dbc
from sqlalchemy import text


# ── Helpers ───────────────────────────────────────────────────────────────────

_CHANNEL_OPTS = [
    {"label": "📧  Email",     "value": "email"},
    {"label": "📱  SMS",       "value": "sms"},
    {"label": "💬  WhatsApp",  "value": "whatsapp"},
    {"label": "🔔  Slack",     "value": "slack"},
]

_PLACEHOLDER = {
    "email":     "owner@company.com",
    "sms":       "+12025551234  (E.164 format)",
    "whatsapp":  "+12025551234  (E.164 format)",
    "slack":     "https://hooks.slack.com/services/…",
}

_CHANNEL_ICON = {
    "email":    "📧",
    "sms":      "📱",
    "whatsapp": "💬",
    "slack":    "🔔",
}

C_NAVY = "#1E293B"
C_BLUE = "#2563EB"


def _get_channels(engine: Any, tenant_id: int) -> list[dict]:
    """Return all alert channels (active + inactive) for a tenant."""
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT id, channel, recipient, label, is_active
                    FROM   tenant_alert_channels
                    WHERE  tenant_id = :tid
                    ORDER  BY channel, id
                """),
                {"tid": tenant_id},
            ).fetchall()
        return [
            {"id": r[0], "channel": r[1], "recipient": r[2],
             "label": r[3] or "", "is_active": bool(r[4])}
            for r in rows
        ]
    except Exception as exc:
        logger.error("[alert_settings] _get_channels failed: %s", exc)
        return []


def _add_channel(engine: Any, tenant_id: int,
                 channel: str, recipient: str, label: str) -> tuple[bool, str]:
    """Insert or re-activate a channel row. Returns (success, message)."""
    if not channel or not recipient:
        return False, "Channel type and recipient are required."
    if channel == "sms" and not recipient.startswith("+"):
        return False, "SMS / WhatsApp numbers must be in E.164 format starting with '+'."
    if channel == "whatsapp" and not recipient.startswith("+"):
        return False, "WhatsApp numbers must be in E.164 format starting with '+'."
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO tenant_alert_channels
                    (tenant_id, channel, recipient, label, is_active)
                VALUES (:tid, :ch, :rec, :lbl, 1)
                ON CONFLICT(tenant_id, channel, recipient)
                DO UPDATE SET is_active=1, label=excluded.label
            """), {"tid": tenant_id, "ch": channel, "rec": recipient.strip(), "lbl": label.strip()})
        return True, f"✅ {channel.title()} alert added: {recipient.strip()}"
    except Exception as exc:
        logger.error("[alert_settings] _add_channel failed: %s", exc)
        return False, f"Database error: {exc}"


def _delete_channel(engine: Any, channel_id: int, tenant_id: int) -> tuple[bool, str]:
    """Hard-delete an alert channel row (with tenant ownership guard)."""
    try:
        with engine.begin() as conn:
            result = conn.execute(text("""
                DELETE FROM tenant_alert_channels
                WHERE id = :id AND tenant_id = :tid
            """), {"id": channel_id, "tid": tenant_id})
        return True, "Channel removed."
    except Exception as exc:
        logger.error("[alert_settings] _delete_channel failed: %s", exc)
        return False, f"Database error: {exc}"


def _toggle_channel(engine: Any, channel_id: int, tenant_id: int,
                    active: bool) -> tuple[bool, str]:
    """Enable / disable an alert channel."""
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE tenant_alert_channels
                SET is_active = :active
                WHERE id = :id AND tenant_id = :tid
            """), {"active": int(active), "id": channel_id, "tid": tenant_id})
        verb = "enabled" if active else "paused"
        return True, f"Channel {verb}."
    except Exception as exc:
        logger.error("[alert_settings] _toggle_channel failed: %s", exc)
        return False, f"Database error: {exc}"


# ── Dash layout ───────────────────────────────────────────────────────────────

def _render_channel_table(channels: list[dict]) -> html.Div:
    """Render the configured channel rows."""
    if not channels:
        return html.Div(
            "No alert channels configured yet.  Add one below to start receiving alerts.",
            style={
                "color": "#64748B", "fontSize": "0.82rem",
                "padding": "1.25rem", "textAlign": "center",
                "border": "1px dashed #334155", "borderRadius": "8px",
                "marginBottom": "1rem",
            },
        )

    header = html.Div(
        [
            html.Span("Type",      style={"width": "80px",  "fontWeight": 600}),
            html.Span("Recipient", style={"flex": 1,         "fontWeight": 600}),
            html.Span("Label",     style={"width": "140px", "fontWeight": 600}),
            html.Span("Status",    style={"width": "70px",  "fontWeight": 600, "textAlign": "center"}),
            html.Span("Actions",   style={"width": "140px", "fontWeight": 600, "textAlign": "center"}),
        ],
        style={
            "display": "flex", "gap": "8px", "alignItems": "center",
            "padding": "6px 12px",
            "fontSize": "0.7rem", "color": "#64748B", "textTransform": "uppercase",
            "letterSpacing": "0.06em", "borderBottom": "1px solid #334155",
        },
    )

    rows = []
    for ch in channels:
        icon   = _CHANNEL_ICON.get(ch["channel"], "📡")
        active = ch["is_active"]
        rows.append(
            html.Div(
                [
                    html.Span(
                        f"{icon}  {ch['channel']}",
                        style={"width": "80px", "fontSize": "0.82rem", "fontWeight": 600,
                               "color": "#94A3B8"},
                    ),
                    html.Span(
                        ch["recipient"],
                        style={"flex": 1, "fontSize": "0.82rem", "color": "#F1F5F9",
                               "overflow": "hidden", "textOverflow": "ellipsis",
                               "whiteSpace": "nowrap"},
                    ),
                    html.Span(
                        ch["label"] or "—",
                        style={"width": "140px", "fontSize": "0.78rem", "color": "#64748B"},
                    ),
                    html.Span(
                        "Active" if active else "Paused",
                        style={
                            "width": "70px", "textAlign": "center",
                            "fontSize": "0.7rem", "fontWeight": 700,
                            "color": "#059669" if active else "#D97706",
                        },
                    ),
                    html.Div(
                        [
                            dbc.Button(
                                "Pause" if active else "Resume",
                                id={"type": "alert-toggle-btn", "index": ch["id"]},
                                size="sm", color="warning" if active else "success",
                                outline=True,
                                style={"fontSize": "0.7rem", "padding": "2px 8px"},
                            ),
                            dbc.Button(
                                "✕",
                                id={"type": "alert-delete-btn", "index": ch["id"]},
                                size="sm", color="danger", outline=True,
                                style={"fontSize": "0.7rem", "padding": "2px 8px",
                                       "marginLeft": "4px"},
                            ),
                        ],
                        style={"width": "140px", "textAlign": "center"},
                    ),
                ],
                style={
                    "display": "flex", "gap": "8px", "alignItems": "center",
                    "padding": "10px 12px",
                    "borderBottom": "1px solid rgba(255,255,255,0.04)",
                    "opacity": "0.5" if not active else "1",
                },
            )
        )

    return html.Div(
        [header, *rows],
        style={
            "background": "#0F172A", "border": "1px solid #334155",
            "borderRadius": "10px", "marginBottom": "1.25rem",
            "overflow": "hidden",
        },
    )


def render_alert_settings_tab(tenant_id: int, engine: Any) -> html.Div:
    """
    Return a Dash layout for alert channel management.
    Designed to be placed inside a billing/settings tab.
    Requires app.py to register the associated callbacks.
    """
    channels = _get_channels(engine, tenant_id)

    card_style = {
        "background": C_NAVY, "border": "1px solid rgba(255,255,255,0.08)",
        "borderRadius": "12px", "padding": "1.25rem 1.5rem", "marginBottom": "1rem",
    }
    label_style = {
        "fontSize": "0.7rem", "fontWeight": 600, "textTransform": "uppercase",
        "letterSpacing": "0.07em", "color": "#94A3B8", "marginBottom": "5px",
    }

    return html.Div(
        [
            # Header
            html.Div(
                [
                    html.Div("🔔  Alert Channels",
                             style={"fontSize": "1.1rem", "fontWeight": 700, "color": "#F1F5F9"}),
                    html.Div(
                        "Configure where InsightHub sends threshold, expiry, and digest alerts.",
                        style={"fontSize": "0.8rem", "color": "#64748B", "marginTop": "3px"},
                    ),
                ],
                style={"marginBottom": "1.25rem"},
            ),

            # Current channels
            html.Div(id="alerts-channels-display",
                     children=_render_channel_table(channels)),

            # Status message
            html.Div(id="alerts-status-msg", style={"marginBottom": "0.75rem"}),

            # Add channel form
            html.Div(
                [
                    html.Div("Add Alert Channel",
                             style={"fontSize": "0.95rem", "fontWeight": 700,
                                    "color": "#F1F5F9", "marginBottom": "1rem"}),
                    dbc.Row(
                        [
                            dbc.Col(
                                [
                                    html.Div("Channel Type", style=label_style),
                                    dcc.Dropdown(
                                        id="alerts-channel-select",
                                        options=_CHANNEL_OPTS,
                                        value="email",
                                        clearable=False,
                                        style={
                                            "background": "#0F172A",
                                            "border": "1px solid rgba(255,255,255,0.10)",
                                            "borderRadius": "8px",
                                            "color": "#F1F5F9",
                                            "fontSize": "0.85rem",
                                        },
                                        className="dark-dropdown",
                                    ),
                                ],
                                width=3,
                            ),
                            dbc.Col(
                                [
                                    html.Div("Recipient", style=label_style),
                                    dbc.Input(
                                        id="alerts-recipient-input",
                                        placeholder="owner@company.com",
                                        type="text",
                                        style={
                                            "background": "#0F172A",
                                            "border": "1px solid rgba(255,255,255,0.10)",
                                            "color": "#F1F5F9", "fontSize": "0.85rem",
                                        },
                                    ),
                                ],
                                width=5,
                            ),
                            dbc.Col(
                                [
                                    html.Div("Label (optional)", style=label_style),
                                    dbc.Input(
                                        id="alerts-label-input",
                                        placeholder='e.g. "Owner Mobile"',
                                        type="text",
                                        style={
                                            "background": "#0F172A",
                                            "border": "1px solid rgba(255,255,255,0.10)",
                                            "color": "#F1F5F9", "fontSize": "0.85rem",
                                        },
                                    ),
                                ],
                                width=3,
                            ),
                            dbc.Col(
                                [
                                    html.Div(" ", style=label_style),
                                    dbc.Button(
                                        "+ Add",
                                        id="alerts-add-btn",
                                        color="primary",
                                        style={
                                            "fontWeight": 700, "width": "100%",
                                            "background": C_BLUE, "border": "none",
                                        },
                                    ),
                                ],
                                width=1,
                            ),
                        ],
                        align="end",
                        className="g-2",
                    ),

                    # Tips
                    html.Div(
                        [
                            html.Span("💡 "),
                            html.Span("SMS / WhatsApp numbers must include country code: "),
                            html.Code("+12025551234",
                                      style={"background": "#0F172A", "padding": "1px 5px",
                                             "borderRadius": "4px", "fontSize": "0.78rem"}),
                            html.Span("  •  Slack: paste an Incoming Webhook URL."),
                        ],
                        style={"fontSize": "0.75rem", "color": "#64748B",
                               "marginTop": "0.75rem"},
                    ),
                ],
                style=card_style,
            ),

            # Store for delete/toggle trigger
            dcc.Store(id="alerts-action-store", data={}),
        ],
        style={"maxWidth": "900px", "margin": "0 auto", "padding": "0.5rem"},
    )


# ── Flask routes for AJAX (fallback / test) ───────────────────────────────────

def register_alert_settings_routes(flask_app: Any, auth_engine: Any) -> None:
    """Register helper Flask routes for alert settings management."""
    from flask import request as flask_req, jsonify
    from flask_login import current_user as cu

    @flask_app.route("/api/alert-channels/add", methods=["POST"])
    def api_add_alert_channel():
        if not cu.is_authenticated:
            return jsonify({"ok": False, "msg": "Not authenticated"}), 401
        try:
            tid     = int(cu.tenant_id) if cu.tenant_id else None
            if not tid:
                return jsonify({"ok": False, "msg": "No tenant context"}), 400
            data    = flask_req.get_json(force=True)
            channel = data.get("channel", "")
            recipient = data.get("recipient", "")
            label   = data.get("label", "")
            ok, msg = _add_channel(auth_engine, tid, channel, recipient, label)
            return jsonify({"ok": ok, "msg": msg})
        except Exception as exc:
            return jsonify({"ok": False, "msg": str(exc)}), 500

    @flask_app.route("/api/alert-channels/delete/<int:channel_id>",
                     methods=["POST", "DELETE"])
    def api_delete_alert_channel(channel_id: int):
        if not cu.is_authenticated:
            return jsonify({"ok": False, "msg": "Not authenticated"}), 401
        try:
            tid = int(cu.tenant_id) if cu.tenant_id else None
            if not tid:
                return jsonify({"ok": False, "msg": "No tenant context"}), 400
            ok, msg = _delete_channel(auth_engine, channel_id, tid)
            return jsonify({"ok": ok, "msg": msg})
        except Exception as exc:
            return jsonify({"ok": False, "msg": str(exc)}), 500

    @flask_app.route("/api/alert-channels/toggle/<int:channel_id>", methods=["POST"])
    def api_toggle_alert_channel(channel_id: int):
        if not cu.is_authenticated:
            return jsonify({"ok": False, "msg": "Not authenticated"}), 401
        try:
            tid    = int(cu.tenant_id) if cu.tenant_id else None
            if not tid:
                return jsonify({"ok": False, "msg": "No tenant context"}), 400
            data   = flask_req.get_json(force=True)
            active = bool(data.get("active", True))
            ok, msg = _toggle_channel(auth_engine, channel_id, tid, active)
            return jsonify({"ok": ok, "msg": msg})
        except Exception as exc:
            return jsonify({"ok": False, "msg": str(exc)}), 500
