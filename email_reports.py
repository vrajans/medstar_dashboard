"""
email_reports.py — InsightHub Weekly Report Engine
Sends a professional HTML email digest every Monday morning (configurable).
BRD US-207 proxy — automated insights delivery.

Usage:
    from email_reports import start_scheduler, send_report_now

    # In app.py startup (after app is created):
    start_scheduler(engine)

    # Manual trigger (for testing):
    send_report_now(engine, tenant_id=3, to_email="owner@company.com")

Configuration (environment variables or email_config.py):
    SMTP_HOST        — SMTP server host (default: smtp.gmail.com)
    SMTP_PORT        — SMTP port (default: 587)
    SMTP_USER        — Sender email address
    SMTP_PASS        — SMTP password / App Password
    SMTP_FROM_NAME   — Display name (default: "InsightHub")
    REPORT_HOUR      — UTC hour to send weekly report (default: 13 = 8am US Eastern)
"""

import os
import smtplib
import threading
import time
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta
import pandas as pd
from sqlalchemy import text

log = logging.getLogger("email_reports")

# ── SMTP configuration ────────────────────────────────────────

SMTP_HOST      = os.environ.get("SMTP_HOST",      "smtp.gmail.com")
SMTP_PORT      = int(os.environ.get("SMTP_PORT",  "587"))
SMTP_USER      = os.environ.get("SMTP_USER",      "")
SMTP_PASS      = os.environ.get("SMTP_PASS",      "")
SMTP_FROM_NAME = os.environ.get("SMTP_FROM_NAME", "InsightHub")
REPORT_HOUR    = int(os.environ.get("REPORT_HOUR", "13"))   # UTC

# ── Month abbreviations ───────────────────────────────────────
MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]


# ── Data helpers ──────────────────────────────────────────────

def _load_tenant_data(engine, tenant_id: int):
    """Load sales + tenant info for email. Returns (tenant_row, sales_df)."""
    try:
        with engine.connect() as conn:
            t_row = conn.execute(
                text("SELECT name, email, currency, domain_type "
                     "FROM local_tenants WHERE id=:tid"),
                {"tid": tenant_id}
            ).fetchone()
            if not t_row:
                return None, pd.DataFrame()

            # Direct tenant_id match
            df = pd.read_sql_query(
                text("SELECT bill_date, net_amount, supplier_name "
                     "FROM sales WHERE CAST(tenant_id AS INTEGER)=:tid"),
                conn, params={"tid": int(tenant_id)}
            )
            if df.empty:
                # Fallback via upload_history
                uid_rows = conn.execute(
                    text("SELECT id FROM upload_history "
                         "WHERE CAST(tenant_id AS INTEGER)=:tid AND status='active'"),
                    {"tid": int(tenant_id)}
                ).fetchall()
                uid_list = [r[0] for r in uid_rows]
                if uid_list:
                    placeholders = ",".join(str(i) for i in uid_list)
                    df = pd.read_sql_query(
                        text(f"SELECT bill_date, net_amount, supplier_name "
                             f"FROM sales WHERE upload_id IN ({placeholders})"),
                        conn
                    )
            if not df.empty:
                df["bill_date"] = pd.to_datetime(df["bill_date"], errors="coerce")
                df["net_amount"] = pd.to_numeric(df["net_amount"], errors="coerce").fillna(0)
                df = df.dropna(subset=["bill_date"])
            return t_row, df
    except Exception as exc:
        log.error(f"[email] load error tenant {tenant_id}: {exc}")
        return None, pd.DataFrame()


def _currency_symbol(currency_code: str) -> str:
    sym_map = {
        "USD": "$", "INR": "₹", "EUR": "€", "GBP": "£",
        "CAD": "CA$", "AUD": "A$", "SGD": "S$",
    }
    return sym_map.get(str(currency_code).upper(), "$")


def _fmt(v, cur="$") -> str:
    if v >= 1_000_000:
        return f"{cur}{v/1_000_000:.2f}M"
    if v >= 10_000:
        return f"{cur}{v/1_000:.1f}K"
    return f"{cur}{v:,.0f}"


def _trend_indicator(change_pct) -> str:
    if change_pct is None:
        return ""
    arrow = "↑" if change_pct >= 0 else "↓"
    color = "#059669" if change_pct >= 0 else "#DC2626"
    return f'<span style="color:{color};font-weight:600">{arrow} {abs(change_pct):.1f}%</span>'


# ── HTML email template ───────────────────────────────────────

def _build_email_html(tenant_name: str, period_label: str, kpis: dict,
                      monthly_table: list, top_clients: list, cur: str = "$") -> str:
    """
    Build professional HTML email with KPI cards + monthly table + top clients.
    Inline CSS for email client compatibility.
    """
    # KPI cards
    kpi_html = ""
    for label, value, change in kpis:
        ind = _trend_indicator(change)
        kpi_html += f"""
        <td style="width:25%;padding:0 8px">
          <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-top:3px solid #2563EB;
                      border-radius:8px;padding:16px 14px;text-align:center">
            <div style="font-size:22px;font-weight:700;color:#2563EB;font-family:Inter,Arial,sans-serif;
                        letter-spacing:-0.5px;line-height:1.2">{value}</div>
            <div style="font-size:10px;color:#64748B;text-transform:uppercase;letter-spacing:0.07em;
                        margin-top:4px;font-family:Inter,Arial,sans-serif">{label}</div>
            <div style="font-size:11px;margin-top:4px">{ind}</div>
          </div>
        </td>"""

    # Monthly table rows
    monthly_rows = ""
    for row in monthly_table[-12:]:  # Last 12 months
        monthly_rows += f"""
        <tr>
          <td style="padding:8px 12px;font-size:13px;color:#334155;border-bottom:1px solid #F1F5F9">{row['month']}</td>
          <td style="padding:8px 12px;font-size:13px;color:#1E293B;font-weight:600;
                     text-align:right;border-bottom:1px solid #F1F5F9">{_fmt(row['revenue'], cur)}</td>
          <td style="padding:8px 12px;font-size:12px;text-align:right;border-bottom:1px solid #F1F5F9">{_trend_indicator(row.get('change'))}</td>
        </tr>"""

    # Top clients table
    client_rows = ""
    for i, c in enumerate(top_clients[:5]):
        bg = "#FAFBFC" if i % 2 == 1 else "#FFFFFF"
        client_rows += f"""
        <tr style="background:{bg}">
          <td style="padding:8px 12px;font-size:13px;color:#334155;border-bottom:1px solid #F1F5F9">{c['name'][:35]}</td>
          <td style="padding:8px 12px;font-size:13px;color:#1E293B;font-weight:600;
                     text-align:right;border-bottom:1px solid #F1F5F9">{_fmt(c['revenue'], cur)}</td>
          <td style="padding:8px 12px;font-size:12px;color:#64748B;text-align:right;
                     border-bottom:1px solid #F1F5F9">{c['share']:.1f}%</td>
        </tr>"""

    now_str = datetime.now().strftime("%B %d, %Y")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>InsightHub Weekly Report</title>
</head>
<body style="margin:0;padding:0;background:#F1F5F9;font-family:Inter,Arial,Helvetica,sans-serif">

<table width="100%" cellpadding="0" cellspacing="0" style="background:#F1F5F9;padding:24px 0">
<tr><td align="center">
<table width="620" cellpadding="0" cellspacing="0" style="max-width:620px;width:100%">

  <!-- Header -->
  <tr>
    <td style="background:#1E293B;border-radius:10px 10px 0 0;padding:24px 28px">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td>
            <div style="font-size:18px;font-weight:700;color:#FFFFFF;letter-spacing:-0.3px">
              InsightHub
            </div>
            <div style="font-size:12px;color:#94A3B8;margin-top:2px">Business Analytics Platform</div>
          </td>
          <td align="right">
            <div style="font-size:11px;color:#64748B">{now_str}</div>
            <div style="font-size:13px;color:#E2E8F0;font-weight:600;margin-top:2px">
              Weekly Report
            </div>
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- Tenant + period banner -->
  <tr>
    <td style="background:#2563EB;padding:14px 28px">
      <div style="font-size:15px;font-weight:600;color:#FFFFFF">{tenant_name}</div>
      <div style="font-size:11px;color:#BFDBFE;margin-top:2px">{period_label}</div>
    </td>
  </tr>

  <!-- Body -->
  <tr>
    <td style="background:#FFFFFF;padding:24px 28px;border-radius:0 0 10px 10px;
               border:1px solid #E2E8F0;border-top:none">

      <!-- KPI cards -->
      <div style="font-size:11px;color:#64748B;text-transform:uppercase;letter-spacing:0.07em;
                  font-weight:600;margin-bottom:12px">Key Metrics</div>
      <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px">
        <tr>{kpi_html}</tr>
      </table>

      <!-- Monthly breakdown -->
      <div style="font-size:11px;color:#64748B;text-transform:uppercase;letter-spacing:0.07em;
                  font-weight:600;margin-bottom:10px">Monthly Revenue</div>
      <table width="100%" cellpadding="0" cellspacing="0"
             style="border:1px solid #E2E8F0;border-radius:8px;overflow:hidden;margin-bottom:24px">
        <tr style="background:#F8FAFC">
          <th style="padding:9px 12px;font-size:11px;color:#64748B;text-align:left;
                     letter-spacing:0.05em;font-weight:600;border-bottom:1px solid #E2E8F0">Month</th>
          <th style="padding:9px 12px;font-size:11px;color:#64748B;text-align:right;
                     letter-spacing:0.05em;font-weight:600;border-bottom:1px solid #E2E8F0">Revenue</th>
          <th style="padding:9px 12px;font-size:11px;color:#64748B;text-align:right;
                     letter-spacing:0.05em;font-weight:600;border-bottom:1px solid #E2E8F0">Change</th>
        </tr>
        {monthly_rows}
      </table>

      <!-- Top clients -->
      <div style="font-size:11px;color:#64748B;text-transform:uppercase;letter-spacing:0.07em;
                  font-weight:600;margin-bottom:10px">Top Clients</div>
      <table width="100%" cellpadding="0" cellspacing="0"
             style="border:1px solid #E2E8F0;border-radius:8px;overflow:hidden;margin-bottom:24px">
        <tr style="background:#F8FAFC">
          <th style="padding:9px 12px;font-size:11px;color:#64748B;text-align:left;
                     letter-spacing:0.05em;font-weight:600;border-bottom:1px solid #E2E8F0">Client</th>
          <th style="padding:9px 12px;font-size:11px;color:#64748B;text-align:right;
                     letter-spacing:0.05em;font-weight:600;border-bottom:1px solid #E2E8F0">Revenue</th>
          <th style="padding:9px 12px;font-size:11px;color:#64748B;text-align:right;
                     letter-spacing:0.05em;font-weight:600;border-bottom:1px solid #E2E8F0">Share</th>
        </tr>
        {client_rows}
      </table>

      <!-- CTA -->
      <div style="text-align:center;padding:4px 0 8px">
        <a href="http://localhost:8050" style="display:inline-block;background:#2563EB;color:#FFFFFF;
           text-decoration:none;font-size:13px;font-weight:600;padding:11px 28px;
           border-radius:7px;letter-spacing:0.02em">
          View Full Dashboard →
        </a>
      </div>

    </td>
  </tr>

  <!-- Footer -->
  <tr>
    <td style="padding:16px 0;text-align:center">
      <div style="font-size:11px;color:#94A3B8">
        InsightHub · Powered by Anthropic Claude
        <br>To unsubscribe, contact your account admin.
      </div>
    </td>
  </tr>

</table>
</td></tr>
</table>
</body>
</html>"""


# ── Report data assembly ──────────────────────────────────────

def _build_report_data(tenant_row, df: pd.DataFrame, cur: str):
    """Compute KPIs, monthly table, top clients from sales DataFrame."""
    name = tenant_row[0]

    if df.empty:
        return None

    # Overall KPIs
    total_rev = df["net_amount"].sum()
    txns      = len(df)
    avg_txn   = total_rev / txns if txns else 0
    n_clients = df["supplier_name"].nunique() if "supplier_name" in df.columns else 0

    # Period delta (compare last 30 days vs prior 30 days)
    now   = df["bill_date"].max()
    cut30 = now - pd.Timedelta(days=30)
    cut60 = now - pd.Timedelta(days=60)
    rev_30 = df[df["bill_date"] >= cut30]["net_amount"].sum()
    rev_pr = df[(df["bill_date"] >= cut60) & (df["bill_date"] < cut30)]["net_amount"].sum()
    d_30   = ((rev_30 - rev_pr) / rev_pr * 100) if rev_pr > 0 else None

    kpis = [
        ("Total Revenue",    _fmt(total_rev, cur),  None),
        ("Last 30 Days",     _fmt(rev_30, cur),     d_30),
        ("Transactions",     f"{txns:,}",            None),
        ("Active Clients",   f"{n_clients:,}",       None),
    ]

    # Monthly table with MoM change
    df2 = df.copy()
    df2["month"] = df2["bill_date"].dt.to_period("M").astype(str)
    mon = df2.groupby("month", as_index=False)["net_amount"].sum().sort_values("month")
    monthly_rows = []
    for i, row in mon.iterrows():
        rev_val = row["net_amount"]
        change  = None
        if i > 0:
            prev_val = mon.iloc[i - 1]["net_amount"] if i > mon.index[0] else None
            if prev_val and prev_val > 0:
                change = (rev_val - prev_val) / prev_val * 100
        monthly_rows.append({"month": row["month"], "revenue": rev_val, "change": change})

    # Top clients
    top_clients = []
    if "supplier_name" in df.columns:
        top_c = (df.groupby("supplier_name", as_index=False)["net_amount"]
                   .sum().sort_values("net_amount", ascending=False).head(5))
        for _, c in top_c.iterrows():
            share = c["net_amount"] / total_rev * 100 if total_rev else 0
            top_clients.append({"name": c["supplier_name"], "revenue": c["net_amount"], "share": share})

    # Date range label
    min_d = df["bill_date"].min()
    max_d = df["bill_date"].max()
    period_label = f"Data: {min_d.strftime('%b %d, %Y')} – {max_d.strftime('%b %d, %Y')}"

    html_body = _build_email_html(name, period_label, kpis, monthly_rows, top_clients, cur)
    return html_body


# ── Send email ────────────────────────────────────────────────

def send_email(to_email: str, subject: str, html_body: str, from_name: str = None) -> bool:
    """Send an HTML email via SMTP. Returns True on success."""
    if not SMTP_USER or not SMTP_PASS:
        log.warning("[email] SMTP_USER / SMTP_PASS not configured — email not sent")
        log.info(f"[email] Would have sent to: {to_email}")
        log.info(f"[email] Subject: {subject}")
        return False

    from_name = from_name or SMTP_FROM_NAME
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{from_name} <{SMTP_USER}>"
    msg["To"]      = to_email
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, [to_email], msg.as_string())
        log.info(f"[email] Sent weekly report to {to_email}")
        return True
    except Exception as exc:
        log.error(f"[email] Send failed: {exc}")
        return False


def send_report_now(engine, tenant_id: int, to_email: str = None) -> dict:
    """
    Build and send (or preview) the weekly report for a tenant.
    Returns {"status", "to", "preview_html"}.
    """
    tenant_row, df = _load_tenant_data(engine, tenant_id)
    if tenant_row is None:
        return {"status": "error", "reason": "tenant not found"}

    cur      = _currency_symbol(tenant_row[2] or "USD")
    name     = tenant_row[0]
    email    = to_email or tenant_row[1] or ""
    subject  = f"InsightHub Weekly Report — {name} ({datetime.now().strftime('%b %d')})"

    html_body = _build_report_data(tenant_row, df, cur)
    if html_body is None:
        return {"status": "no_data", "reason": "no sales data found"}

    sent = False
    if email:
        sent = send_email(email, subject, html_body)

    return {
        "status": "sent" if sent else "preview_only",
        "to": email,
        "preview_html": html_body,
    }


# ── Background scheduler ──────────────────────────────────────

_scheduler_running = False
_scheduler_thread  = None


def _get_all_active_tenants(engine):
    """Return list of (id, email, currency) for tenants with email_reports=1 or trial active."""
    try:
        with engine.connect() as conn:
            # Try with email_reports column first; fall back to all tenants
            try:
                rows = conn.execute(
                    text("SELECT id, email, currency FROM local_tenants "
                         "WHERE email IS NOT NULL AND email != '' "
                         "AND (email_reports = 1 OR trial_active = 1)")
                ).fetchall()
            except Exception:
                rows = conn.execute(
                    text("SELECT id, email, currency FROM local_tenants "
                         "WHERE email IS NOT NULL AND email != ''")
                ).fetchall()
            return rows
    except Exception as exc:
        log.error(f"[email_scheduler] Failed to fetch tenants: {exc}")
        return []


def _scheduler_loop(engine):
    """Background thread: fires at REPORT_HOUR UTC every Monday (weekday 0)."""
    log.info("[email_scheduler] Weekly report scheduler started")
    while _scheduler_running:
        now   = datetime.utcnow()
        # Find next Monday at REPORT_HOUR UTC
        days_ahead = (7 - now.weekday()) % 7  # days until next Monday
        if days_ahead == 0 and now.hour >= REPORT_HOUR:
            days_ahead = 7
        next_run = (now + timedelta(days=days_ahead)).replace(
            hour=REPORT_HOUR, minute=0, second=0, microsecond=0
        )
        sleep_secs = (next_run - datetime.utcnow()).total_seconds()
        log.info(f"[email_scheduler] Next run: {next_run} UTC ({sleep_secs/3600:.1f}h away)")

        # Sleep until then, checking every 60s
        while _scheduler_running and datetime.utcnow() < next_run:
            time.sleep(60)

        if not _scheduler_running:
            break

        # Time to send!
        log.info("[email_scheduler] Sending weekly reports...")
        tenants = _get_all_active_tenants(engine)
        for row in tenants:
            tid, email, currency = row[0], row[1], row[2]
            try:
                result = send_report_now(engine, tenant_id=tid, to_email=email)
                log.info(f"[email_scheduler] tenant={tid} result={result['status']}")
            except Exception as exc:
                log.error(f"[email_scheduler] tenant={tid} error: {exc}")

        # Sleep 1 hour to avoid duplicate sends
        time.sleep(3600)


def start_scheduler(engine):
    """Start the background weekly report scheduler (call once at app startup)."""
    global _scheduler_running, _scheduler_thread

    if _scheduler_thread and _scheduler_thread.is_alive():
        log.info("[email_scheduler] Already running — skipping start")
        return

    _scheduler_running = True
    _scheduler_thread  = threading.Thread(
        target=_scheduler_loop, args=(engine,),
        daemon=True, name="email_report_scheduler",
    )
    _scheduler_thread.start()
    log.info("[email_scheduler] Started background report scheduler")


def stop_scheduler():
    global _scheduler_running
    _scheduler_running = False
    log.info("[email_scheduler] Stopping scheduler...")
