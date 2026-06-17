"""
alerts.py  —  InsightHub unified alert system
==============================================
Supports four channels (all optional, controlled by env vars):

  1. Email      — SendGrid SMTP / REST API
  2. WhatsApp   — Interakt BSP (India) or WATI as fallback
  3. SMS        — Twilio (USA / Global)
  4. Slack      — Incoming Webhook

All public functions are fire-and-forget: they log errors but never raise,
so a missing API key or network glitch never crashes the dashboard.

Usage
-----
from alerts import send_alert, send_digest, send_expiry_alert, send_stock_alert

send_alert(
    channel="email",
    recipient="owner@example.com",
    subject="Margin below threshold",
    body="Today's average margin is 14.2%, below the 20% minimum.",
    tenant_id=3,
)
"""

import os
import json
import logging
from datetime import datetime, date
from typing import Literal, Optional, Any

logger = logging.getLogger(__name__)

# ── Environment keys ──────────────────────────────────────────────────────────
SENDGRID_API_KEY   = os.getenv("SENDGRID_API_KEY", "")
SENDGRID_FROM      = os.getenv("SENDGRID_FROM_EMAIL", "noreply@insighthub.ai")
INTERAKT_API_KEY   = os.getenv("INTERAKT_API_KEY", "")
WATI_API_KEY       = os.getenv("WATI_API_KEY", "")
WATI_BASE_URL      = os.getenv("WATI_BASE_URL", "")        # e.g. https://live-mt-server.wati.io/XXXXX
TWILIO_SID         = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN       = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM        = os.getenv("TWILIO_FROM_NUMBER", "")   # e.g. +14155238886
SLACK_WEBHOOK_URL  = os.getenv("SLACK_WEBHOOK_URL", "")


# ═════════════════════════════════════════════════════════════════════════════
# 1.  EMAIL  —  SendGrid
# ═════════════════════════════════════════════════════════════════════════════

def _send_email(recipient: str, subject: str, body: str,
                html_body: Optional[str] = None) -> bool:
    """
    Send a transactional email via SendGrid REST API.
    Returns True on success, False on any failure.
    """
    if not SENDGRID_API_KEY:
        logger.warning("[alerts.email] SENDGRID_API_KEY not set — skipping email.")
        return False
    try:
        import urllib.request
        payload = {
            "personalizations": [{"to": [{"email": recipient}]}],
            "from": {"email": SENDGRID_FROM, "name": "InsightHub"},
            "subject": subject,
            "content": [
                {"type": "text/plain", "value": body},
            ],
        }
        if html_body:
            payload["content"].append({"type": "text/html", "value": html_body})

        data    = json.dumps(payload).encode("utf-8")
        req     = urllib.request.Request(
            "https://api.sendgrid.com/v3/mail/send",
            data    = data,
            headers = {
                "Authorization": f"Bearer {SENDGRID_API_KEY}",
                "Content-Type":  "application/json",
            },
            method  = "POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            ok = resp.status in (200, 202)
            if not ok:
                logger.error("[alerts.email] SendGrid HTTP %s", resp.status)
            return ok
    except Exception as exc:
        logger.error("[alerts.email] Exception: %s", exc)
        return False


def _build_email_html(subject: str, body: str,
                      tenant_name: str = "Your Business",
                      alert_level: str = "info") -> str:
    """Build a clean HTML email template."""
    COLOR_MAP = {
        "danger":  "#dc3545",
        "warning": "#fd7e14",
        "success": "#1e7e4b",
        "info":    "#0d6efd",
    }
    accent = COLOR_MAP.get(alert_level, "#0d6efd")
    body_escaped = body.replace("\n", "<br>")
    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;font-family:Arial,sans-serif;background:#f4f6f9">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:32px 16px">
      <table width="560" cellpadding="0" cellspacing="0"
             style="background:#fff;border-radius:12px;
                    box-shadow:0 2px 12px rgba(0,0,0,0.08);overflow:hidden">
        <!-- Header -->
        <tr>
          <td style="background:{accent};padding:20px 32px">
            <span style="color:#fff;font-size:18px;font-weight:700">InsightHub Alert</span>
            <span style="color:rgba(255,255,255,0.75);font-size:13px;
                         float:right;line-height:28px">{tenant_name}</span>
          </td>
        </tr>
        <!-- Subject -->
        <tr>
          <td style="padding:24px 32px 12px">
            <h2 style="margin:0;font-size:16px;color:#1a1a2e">{subject}</h2>
          </td>
        </tr>
        <!-- Body -->
        <tr>
          <td style="padding:0 32px 24px;font-size:14px;
                     color:#374151;line-height:1.6">
            {body_escaped}
          </td>
        </tr>
        <!-- Footer -->
        <tr>
          <td style="background:#f8fafc;padding:14px 32px;
                     border-top:1px solid #e8eaf0">
            <span style="font-size:11px;color:#94a3b8">
              Sent by InsightHub · {datetime.utcnow().strftime("%d %b %Y %H:%M UTC")}
              · <a href="{{{{unsubscribe}}}}" style="color:#94a3b8">Unsubscribe</a>
            </span>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""


# ═════════════════════════════════════════════════════════════════════════════
# 2.  WHATSAPP  —  Interakt (primary) / WATI (fallback)
# ═════════════════════════════════════════════════════════════════════════════

def _send_whatsapp_interakt(phone: str, template_name: str,
                            body_values: list[str]) -> bool:
    """
    Send a WhatsApp template message via Interakt BSP.

    phone          : E.164 format, e.g. "919876543210"
    template_name  : Pre-approved template name in Interakt dashboard
    body_values    : List of {{1}}, {{2}} … variable values
    """
    if not INTERAKT_API_KEY:
        logger.warning("[alerts.wa] INTERAKT_API_KEY not set — skipping WhatsApp.")
        return False
    try:
        import urllib.request
        payload = {
            "countryCode": phone[:2] if phone.startswith("91") else "91",
            "phoneNumber": phone[2:] if phone.startswith("91") else phone,
            "callbackData": "insighthub_alert",
            "type": "Template",
            "template": {
                "name":     template_name,
                "languageCode": "en",
                "bodyValues": body_values,
            },
        }
        data = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(
            "https://api.interakt.ai/v1/public/message/",
            data    = data,
            headers = {
                "Authorization": f"Basic {INTERAKT_API_KEY}",
                "Content-Type":  "application/json",
            },
            method  = "POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            ok = result.get("result") is True
            if not ok:
                logger.error("[alerts.wa] Interakt error: %s", result)
            return ok
    except Exception as exc:
        logger.error("[alerts.wa] Interakt exception: %s", exc)
        return False


def _send_whatsapp_wati(phone: str, template_name: str,
                        params: list[dict]) -> bool:
    """Fallback: WATI BSP for WhatsApp template messages."""
    if not (WATI_API_KEY and WATI_BASE_URL):
        return False
    try:
        import urllib.request
        url     = f"{WATI_BASE_URL.rstrip('/')}/api/v1/sendTemplateMessage?whatsappNumber={phone}"
        payload = {"template_name": template_name, "broadcast_name": "insighthub", "parameters": params}
        data    = json.dumps(payload).encode("utf-8")
        req     = urllib.request.Request(
            url, data=data,
            headers={"Authorization": f"Bearer {WATI_API_KEY}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            return result.get("result") is True
    except Exception as exc:
        logger.error("[alerts.wa] WATI exception: %s", exc)
        return False


def _send_whatsapp(phone: str, template_name: str,
                   body_values: list[str]) -> bool:
    """Try Interakt first, fall back to WATI."""
    if _send_whatsapp_interakt(phone, template_name, body_values):
        return True
    # Convert body_values to WATI param format
    params = [{"name": str(i+1), "value": v} for i, v in enumerate(body_values)]
    return _send_whatsapp_wati(phone, template_name, params)


# ═════════════════════════════════════════════════════════════════════════════
# 3.  SMS  —  Twilio
# ═════════════════════════════════════════════════════════════════════════════

def _send_sms(to_number: str, message: str) -> bool:
    """Send SMS via Twilio REST API (no SDK required)."""
    if not (TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM):
        logger.warning("[alerts.sms] Twilio credentials not set — skipping SMS.")
        return False
    try:
        import urllib.request, urllib.parse
        url  = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json"
        data = urllib.parse.urlencode({
            "From": TWILIO_FROM, "To": to_number, "Body": message,
        }).encode("utf-8")
        creds   = f"{TWILIO_SID}:{TWILIO_TOKEN}"
        encoded = __import__("base64").b64encode(creds.encode()).decode()
        req     = urllib.request.Request(
            url, data=data,
            headers={"Authorization": f"Basic {encoded}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            sid = result.get("sid", "")
            ok  = sid.startswith("SM")
            if not ok:
                logger.error("[alerts.sms] Twilio error: %s", result.get("message"))
            return ok
    except Exception as exc:
        logger.error("[alerts.sms] Twilio exception: %s", exc)
        return False


# ═════════════════════════════════════════════════════════════════════════════
# 4.  SLACK  —  Incoming Webhook
# ═════════════════════════════════════════════════════════════════════════════

def _send_slack(message: str, title: Optional[str] = None,
                color: str = "#0d6efd") -> bool:
    """Post a Slack message via an incoming webhook URL."""
    if not SLACK_WEBHOOK_URL:
        logger.warning("[alerts.slack] SLACK_WEBHOOK_URL not set — skipping.")
        return False
    try:
        import urllib.request
        blocks = []
        if title:
            blocks.append({
                "type": "header",
                "text": {"type": "plain_text", "text": title, "emoji": True},
            })
        blocks.append({
            "type":    "section",
            "text":    {"type": "mrkdwn", "text": message},
        })
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn",
                          "text": f"_InsightHub · {datetime.utcnow().strftime('%d %b %Y %H:%M UTC')}_"}],
        })
        payload = {"blocks": blocks, "attachments": [{"color": color}]}
        data    = json.dumps(payload).encode("utf-8")
        req     = urllib.request.Request(
            SLACK_WEBHOOK_URL, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            ok = resp.read().decode() == "ok"
            if not ok:
                logger.error("[alerts.slack] Slack webhook returned non-ok.")
            return ok
    except Exception as exc:
        logger.error("[alerts.slack] Slack exception: %s", exc)
        return False


# ═════════════════════════════════════════════════════════════════════════════
# 5.  PUBLIC API
# ═════════════════════════════════════════════════════════════════════════════

AlertChannel = Literal["email", "whatsapp", "sms", "slack"]

def send_alert(
    channel:       AlertChannel,
    recipient:     str,
    subject:       str,
    body:          str,
    tenant_id:     Optional[int]  = None,
    tenant_name:   str            = "Your Business",
    alert_level:   str            = "info",
    # WhatsApp-specific
    wa_template:   str            = "insighthub_generic_alert",
    wa_values:     Optional[list] = None,
) -> bool:
    """
    Send an alert via a single channel.

    channel    : "email" | "whatsapp" | "sms" | "slack"
    recipient  : email address, phone number (+919xxxxxxxx), or ignored for slack
    subject    : Short alert title (email subject / WA template header)
    body       : Full message body
    """
    logger.info("[alerts] send_alert channel=%s recipient=%s subject='%s'",
                channel, recipient, subject)
    if channel == "email":
        html = _build_email_html(subject, body, tenant_name, alert_level)
        return _send_email(recipient, subject, body, html)
    elif channel == "whatsapp":
        vals = wa_values or [subject, body]
        return _send_whatsapp(recipient, wa_template, vals)
    elif channel == "sms":
        sms_text = f"InsightHub Alert: {subject}\n{body}"
        return _send_sms(recipient, sms_text[:1600])
    elif channel == "slack":
        color_map = {"danger":"#dc3545","warning":"#fd7e14","success":"#1e7e4b","info":"#0d6efd"}
        return _send_slack(body, title=subject, color=color_map.get(alert_level,"#0d6efd"))
    else:
        logger.error("[alerts] Unknown channel: %s", channel)
        return False


def send_multi_channel(
    channels:    list[tuple[AlertChannel, str]],   # [(channel, recipient), ...]
    subject:     str,
    body:        str,
    tenant_id:   Optional[int] = None,
    tenant_name: str           = "Your Business",
    alert_level: str           = "info",
) -> dict[str, bool]:
    """Send the same alert across multiple channels simultaneously."""
    results = {}
    for channel, recipient in channels:
        results[f"{channel}:{recipient}"] = send_alert(
            channel=channel, recipient=recipient,
            subject=subject, body=body,
            tenant_id=tenant_id, tenant_name=tenant_name,
            alert_level=alert_level,
        )
    return results


# ── Specific alert builders ───────────────────────────────────────────────────

def send_digest(
    email:       str,
    tenant_name: str,
    sales_total: float,
    purchase_total: float,
    margin_pct:  float,
    alerts:      list[dict],
    report_date: Optional[date] = None,
) -> bool:
    """
    Send the daily morning digest email.
    alerts: list of {"level": "danger"|"warning", "msg": "..."}
    """
    if report_date is None:
        report_date = date.today()

    def fmt(v):
        if v >= 100000: return f"₹{v/100000:.2f}L"
        if v >= 1000:   return f"₹{v/1000:.1f}K"
        return f"₹{v:.0f}"

    alert_text = ""
    if alerts:
        alert_text = "\n\nAlerts:\n" + "\n".join(
            f"  {'⚠️' if a['level']=='danger' else '📉'} {a['msg']}" for a in alerts
        )
    else:
        alert_text = "\n\n✅ All KPIs within thresholds."

    body = (
        f"Good morning! Here is your daily summary for {report_date.strftime('%d %b %Y')}.\n\n"
        f"  Sales:     {fmt(sales_total)}\n"
        f"  Purchases: {fmt(purchase_total)}\n"
        f"  Avg Margin: {margin_pct:.1f}%"
        f"{alert_text}\n\n"
        f"Log in to InsightHub for detailed charts and drill-downs."
    )
    return send_alert(
        channel="email", recipient=email,
        subject=f"InsightHub Daily Digest — {report_date.strftime('%d %b %Y')}",
        body=body,
        tenant_name=tenant_name, alert_level="info",
    )


def send_expiry_alert(
    channels:    list[tuple[AlertChannel, str]],
    tenant_name: str,
    items:       list[dict],          # [{"name","batch","expiry_date","qty","value"}, ...]
    days_window: int = 30,
) -> dict[str, bool]:
    """Alert for items expiring within `days_window` days."""
    if not items:
        return {}
    subject = f"⚠️ {len(items)} item(s) expiring within {days_window} days — {tenant_name}"
    lines   = []
    for i, it in enumerate(items[:10], 1):
        exp  = it.get("expiry_date", "?")
        name = it.get("name", "?")
        batch= it.get("batch", "")
        qty  = it.get("qty",  0)
        val  = it.get("value", 0)
        lines.append(f"  {i}. {name} (Batch {batch}) — Exp: {exp}, Qty: {qty}, Value: ₹{val:,.0f}")
    if len(items) > 10:
        lines.append(f"  … and {len(items)-10} more items.")
    body = "\n".join([
        f"The following items expire within {days_window} days:",
        "",
        *lines,
        "",
        "Please take action: return to supplier, apply discounts, or write off stock.",
    ])
    return send_multi_channel(channels, subject, body,
                              tenant_name=tenant_name, alert_level="warning")


def send_stock_alert(
    channels:    list[tuple[AlertChannel, str]],
    tenant_name: str,
    items:       list[dict],          # [{"name","current_qty","reorder_level","unit"}, ...]
) -> dict[str, bool]:
    """Alert for items below reorder level."""
    if not items:
        return {}
    subject = f"📦 {len(items)} item(s) below reorder level — {tenant_name}"
    lines   = []
    for i, it in enumerate(items[:10], 1):
        name  = it.get("name", "?")
        curr  = it.get("current_qty", 0)
        reord = it.get("reorder_level", 0)
        unit  = it.get("unit", "units")
        lines.append(f"  {i}. {name} — Stock: {curr} {unit} (Reorder at: {reord})")
    if len(items) > 10:
        lines.append(f"  … and {len(items)-10} more items.")
    body = "\n".join([
        "The following items have fallen below reorder level:",
        "",
        *lines,
        "",
        "Please raise a purchase order to avoid stockouts.",
    ])
    return send_multi_channel(channels, subject, body,
                              tenant_name=tenant_name, alert_level="warning")


def send_threshold_breach(
    channels:    list[tuple[AlertChannel, str]],
    tenant_name: str,
    metric:      str,
    current_val: float,
    threshold:   float,
    direction:   Literal["below", "above"] = "below",
) -> dict[str, bool]:
    """Alert when a KPI crosses a threshold."""
    emoji   = "📉" if direction == "below" else "📈"
    subject = f"{emoji} KPI Alert: {metric} is {direction} threshold — {tenant_name}"
    body    = (
        f"Your metric '{metric}' is currently {current_val:.2f}, "
        f"which is {direction} the configured threshold of {threshold:.2f}.\n\n"
        f"Please review your operations and take corrective action."
    )
    level = "danger" if direction == "below" else "warning"
    return send_multi_channel(channels, subject, body,
                              tenant_name=tenant_name, alert_level=level)


# ── Alert channel config helpers ──────────────────────────────────────────────

def get_tenant_channels(tenant_id: int, engine: Any) -> list[tuple[AlertChannel, str]]:
    """
    Load a tenant's configured alert channels from the DB.
    Table: tenant_alert_channels(tenant_id, channel, recipient, is_active)
    Returns list of (channel, recipient) tuples for active channels.
    """
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT channel, recipient FROM tenant_alert_channels
                WHERE tenant_id = :tid AND is_active = 1
            """), {"tid": tenant_id}).fetchall()
        return [(r[0], r[1]) for r in rows]
    except Exception as exc:
        logger.error("[alerts] get_tenant_channels failed: %s", exc)
        return []


def init_alert_tables(engine: Any) -> None:
    """Create alert config and alert log tables if they don't exist."""
    ddl = """
    CREATE TABLE IF NOT EXISTS tenant_alert_channels (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id   INTEGER NOT NULL,
        channel     TEXT    NOT NULL,   -- email|whatsapp|sms|slack
        recipient   TEXT    NOT NULL,   -- email / phone / webhook_url
        label       TEXT,               -- friendly name e.g. "Owner Mobile"
        is_active   INTEGER DEFAULT 1,
        created_at  TEXT    DEFAULT (datetime('now')),
        UNIQUE (tenant_id, channel, recipient)
    );

    CREATE TABLE IF NOT EXISTS alert_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id   INTEGER,
        channel     TEXT,
        recipient   TEXT,
        subject     TEXT,
        status      TEXT,               -- sent|failed
        sent_at     TEXT    DEFAULT (datetime('now'))
    );
    """
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            for stmt in ddl.strip().split(";"):
                s = stmt.strip()
                if s:
                    conn.execute(text(s))
        logger.info("[alerts] Alert tables initialised.")
    except Exception as exc:
        logger.error("[alerts] init_alert_tables failed: %s", exc)


def log_alert(engine: Any, tenant_id: Optional[int], channel: str,
              recipient: str, subject: str, status: str) -> None:
    """Append a row to the alert_log table."""
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO alert_log (tenant_id, channel, recipient, subject, status)
                VALUES (:tid, :ch, :rec, :sub, :st)
            """), {"tid": tenant_id, "ch": channel, "rec": recipient,
                   "sub": subject,   "st": status})
    except Exception as exc:
        logger.warning("[alerts] log_alert failed: %s", exc)
