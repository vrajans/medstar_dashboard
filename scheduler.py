"""
scheduler.py  —  InsightHub background job scheduler
=====================================================
Uses APScheduler (BackgroundScheduler) — starts as a daemon thread
inside the Dash/Flask process so no separate worker process is needed.

Jobs
----
  morning_digest     — 08:00 AM daily: email KPI summary to each tenant owner
  weekly_pdf_report  — Monday 07:00 AM: email full PDF report
  expiry_check       — 09:00 AM daily: WhatsApp/email items expiring ≤ 30 days
  stock_check        — 09:30 AM daily: WhatsApp/email items below reorder level
  threshold_check    — every 4 hours: check KPI thresholds, fire alerts
  marg_file_watch    — continuous: watchdog for Marg ERP file-drop folder

Usage
-----
from scheduler import start_scheduler
start_scheduler(engine)       # call once, after app and DB are initialised
"""

import os
import logging
import threading
from datetime import datetime, date
from typing import Optional, Any

logger = logging.getLogger(__name__)

# Optional: import APScheduler
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron        import CronTrigger
    from apscheduler.triggers.interval    import IntervalTrigger
    HAS_APSCHEDULER = True
except ImportError:
    HAS_APSCHEDULER = False
    logger.warning("[scheduler] APScheduler not installed — scheduled jobs disabled. "
                   "Run: pip install apscheduler")

# ── Config from environment ────────────────────────────────────────────────────
MARG_WATCH_DIR  = os.getenv("MARG_WATCH_DIR", "")       # e.g. /mnt/marg_exports
DIGEST_HOUR     = int(os.getenv("DIGEST_HOUR",    "8"))  # 08:00 AM local time
WEEKLY_DAY      = os.getenv("WEEKLY_REPORT_DAY",  "mon") # day of week for weekly PDF
EXPIRY_DAYS     = int(os.getenv("EXPIRY_WARN_DAYS", "30"))
THRESHOLD_HOURS = int(os.getenv("THRESHOLD_CHECK_HOURS", "4"))

_scheduler: Optional[Any] = None
_engine:    Optional[Any] = None


# ═════════════════════════════════════════════════════════════════════════════
# Helpers — load tenant list
# ═════════════════════════════════════════════════════════════════════════════

def _get_active_tenants() -> list[dict]:
    """Return active tenants with their owner email and phone."""
    if _engine is None:
        return []
    try:
        from sqlalchemy import text
        with _engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT t.id, t.name, t.owner_email, t.owner_phone,
                       t.timezone, t.currency
                FROM   tenants t
                WHERE  t.is_active = 1
            """)).fetchall()
        return [{"id": r[0], "name": r[1], "email": r[2],
                 "phone": r[3], "tz": r[4], "currency": r[5]}
                for r in rows]
    except Exception as exc:
        logger.error("[scheduler] _get_active_tenants: %s", exc)
        return []


def _get_latest_kpis(tenant_id: int) -> dict:
    """Pull the most recent daily KPIs for a tenant from the DB."""
    if _engine is None:
        return {}
    try:
        from sqlalchemy import text
        with _engine.connect() as conn:
            row = conn.execute(text("""
                SELECT sales_total, purchase_total, avg_margin_pct
                FROM   tenant_daily_kpi
                WHERE  tenant_id = :tid
                ORDER  BY kpi_date DESC
                LIMIT  1
            """), {"tid": tenant_id}).fetchone()
        if row:
            return {"sales": row[0] or 0, "purchases": row[1] or 0, "margin": row[2] or 0}
    except Exception:
        pass
    return {"sales": 0, "purchases": 0, "margin": 0}


def _get_expiring_items(tenant_id: int, days: int = 30) -> list[dict]:
    """Fetch items expiring within `days` days for a tenant."""
    if _engine is None:
        return []
    try:
        from sqlalchemy import text
        with _engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT item_name, batch_no, expiry_date, quantity, stock_value
                FROM   tenant_inventory
                WHERE  tenant_id   = :tid
                  AND  expiry_date BETWEEN date('now') AND date('now', :days)
                  AND  quantity    > 0
                ORDER  BY expiry_date ASC
                LIMIT  50
            """), {"tid": tenant_id, "days": f"+{days} days"}).fetchall()
        return [{"name": r[0], "batch": r[1], "expiry_date": str(r[2]),
                 "qty": r[3], "value": r[4] or 0}
                for r in rows]
    except Exception as exc:
        logger.error("[scheduler] _get_expiring_items: %s", exc)
        return []


def _get_low_stock_items(tenant_id: int) -> list[dict]:
    """Fetch items below reorder level for a tenant."""
    if _engine is None:
        return []
    try:
        from sqlalchemy import text
        with _engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT item_name, quantity, reorder_level, unit
                FROM   tenant_inventory
                WHERE  tenant_id = :tid
                  AND  quantity  < reorder_level
                ORDER  BY (reorder_level - quantity) DESC
                LIMIT  50
            """), {"tid": tenant_id}).fetchall()
        return [{"name": r[0], "current_qty": r[1], "reorder_level": r[2], "unit": r[3] or "units"}
                for r in rows]
    except Exception as exc:
        logger.error("[scheduler] _get_low_stock_items: %s", exc)
        return []


def _get_check_alerts(tenant_id: int) -> list[dict]:
    """Re-use the check_alerts logic on the latest data."""
    kpis = _get_latest_kpis(tenant_id)
    alerts_out = []
    if kpis.get("margin", 0) < 20:
        alerts_out.append({"level": "danger",
                           "msg": f"Avg Margin {kpis['margin']:.1f}% is below 20% minimum."})
    if kpis.get("sales", 0) < 50000:
        alerts_out.append({"level": "warning",
                           "msg": f"Daily sales ₹{kpis['sales']:,.0f} below ₹50K target."})
    return alerts_out


# ═════════════════════════════════════════════════════════════════════════════
# JOB 1 — Morning digest email
# ═════════════════════════════════════════════════════════════════════════════

def job_morning_digest():
    """Send daily KPI digest to each active tenant's owner email."""
    logger.info("[scheduler] Running morning digest...")
    from alerts import send_digest, get_tenant_channels
    for tenant in _get_active_tenants():
        if not tenant.get("email"):
            continue
        kpis   = _get_latest_kpis(tenant["id"])
        alerts = _get_check_alerts(tenant["id"])
        try:
            send_digest(
                email=tenant["email"],
                tenant_name=tenant["name"],
                sales_total=kpis["sales"],
                purchase_total=kpis["purchases"],
                margin_pct=kpis["margin"],
                alerts=alerts,
            )
            logger.info("[scheduler] Digest sent to %s (%s)", tenant["email"], tenant["name"])
        except Exception as exc:
            logger.error("[scheduler] Digest failed for tenant %s: %s", tenant["id"], exc)


# ═════════════════════════════════════════════════════════════════════════════
# JOB 2 — Weekly PDF report email
# ═════════════════════════════════════════════════════════════════════════════

def job_weekly_pdf_report():
    """Generate and email the weekly PDF report to each tenant."""
    logger.info("[scheduler] Running weekly PDF report...")
    try:
        from pdf_report import generate_pdf
        import base64, tempfile, os as _os
    except ImportError as e:
        logger.error("[scheduler] pdf_report import failed: %s", e)
        return

    from alerts import _send_email, _build_email_html

    for tenant in _get_active_tenants():
        if not tenant.get("email"):
            continue
        try:
            # Generate PDF bytes
            pdf_bytes = generate_pdf(tenant_id=tenant["id"], engine=_engine)
            if not pdf_bytes:
                continue

            # Write to temp file and send as attachment via SendGrid
            today = date.today().strftime("%Y-%m-%d")
            _send_weekly_pdf_email(
                tenant["email"], tenant["name"], pdf_bytes, today
            )
        except Exception as exc:
            logger.error("[scheduler] Weekly PDF failed for tenant %s: %s", tenant["id"], exc)


def _send_weekly_pdf_email(recipient: str, tenant_name: str,
                            pdf_bytes: bytes, report_date: str) -> bool:
    """Send weekly PDF via SendGrid with attachment."""
    sg_key = os.getenv("SENDGRID_API_KEY", "")
    if not sg_key:
        logger.warning("[scheduler] SENDGRID_API_KEY not set — PDF email skipped.")
        return False
    try:
        import urllib.request, base64, json
        filename   = f"InsightHub_Weekly_{report_date}.pdf"
        b64_content= base64.b64encode(pdf_bytes).decode()
        payload = {
            "personalizations": [{"to": [{"email": recipient}]}],
            "from":    {"email": os.getenv("SENDGRID_FROM_EMAIL","noreply@insighthub.ai"),
                        "name": "InsightHub"},
            "subject": f"InsightHub Weekly Report — {report_date}",
            "content": [{"type": "text/plain",
                         "value": f"Dear {tenant_name},\n\nPlease find your weekly analytics report attached.\n\nBest,\nInsightHub Team"}],
            "attachments": [{
                "content":     b64_content,
                "type":        "application/pdf",
                "filename":    filename,
                "disposition": "attachment",
            }],
        }
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(
            "https://api.sendgrid.com/v3/mail/send",
            data=data,
            headers={"Authorization": f"Bearer {sg_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status in (200, 202)
    except Exception as exc:
        logger.error("[scheduler] _send_weekly_pdf_email: %s", exc)
        return False


# ═════════════════════════════════════════════════════════════════════════════
# JOB 3 — Expiry check
# ═════════════════════════════════════════════════════════════════════════════

def job_expiry_check():
    """Check expiring inventory and alert tenants."""
    logger.info("[scheduler] Running expiry check...")
    from alerts import send_expiry_alert, get_tenant_channels
    for tenant in _get_active_tenants():
        items    = _get_expiring_items(tenant["id"], days=EXPIRY_DAYS)
        if not items:
            continue
        channels = get_tenant_channels(tenant["id"], _engine)
        if not channels and tenant.get("email"):
            channels = [("email", tenant["email"])]
        if channels:
            send_expiry_alert(channels, tenant["name"], items, days_window=EXPIRY_DAYS)
            logger.info("[scheduler] Expiry alert: %d items for tenant %s",
                        len(items), tenant["name"])


# ═════════════════════════════════════════════════════════════════════════════
# JOB 4 — Stock check
# ═════════════════════════════════════════════════════════════════════════════

def job_stock_check():
    """Check low stock and alert tenants."""
    logger.info("[scheduler] Running stock check...")
    from alerts import send_stock_alert, get_tenant_channels
    for tenant in _get_active_tenants():
        items    = _get_low_stock_items(tenant["id"])
        if not items:
            continue
        channels = get_tenant_channels(tenant["id"], _engine)
        if not channels and tenant.get("email"):
            channels = [("email", tenant["email"])]
        if channels:
            send_stock_alert(channels, tenant["name"], items)
            logger.info("[scheduler] Stock alert: %d items for tenant %s",
                        len(items), tenant["name"])


# ═════════════════════════════════════════════════════════════════════════════
# JOB 5 — KPI threshold check (runs every N hours)
# ═════════════════════════════════════════════════════════════════════════════

def job_threshold_check():
    """Check KPI thresholds and fire alerts if crossed."""
    logger.info("[scheduler] Running threshold check...")
    from alerts import send_threshold_breach, get_tenant_channels
    for tenant in _get_active_tenants():
        kpis     = _get_latest_kpis(tenant["id"])
        channels = get_tenant_channels(tenant["id"], _engine)
        if not channels and tenant.get("email"):
            channels = [("email", tenant["email"])]
        if not channels:
            continue
        # Margin check
        if kpis.get("margin", 100) < 20:
            send_threshold_breach(channels, tenant["name"],
                                  "Average Margin %", kpis["margin"], 20.0, "below")
        # Sales check
        if kpis.get("sales", 999999) < 50000:
            send_threshold_breach(channels, tenant["name"],
                                  "Daily Sales", kpis["sales"], 50000.0, "below")


# ═════════════════════════════════════════════════════════════════════════════
# JOB 6 — Marg file watcher (watchdog, runs in own thread)
# ═════════════════════════════════════════════════════════════════════════════

def _start_marg_watcher():
    """Start the Marg ERP file-drop watcher in a daemon thread."""
    if not MARG_WATCH_DIR:
        logger.info("[scheduler] MARG_WATCH_DIR not set — Marg watcher disabled.")
        return
    try:
        from integrations.marg_watcher import MargFileHandler
        from watchdog.observers import Observer
        observer = Observer()
        handler  = MargFileHandler(engine=_engine)
        observer.schedule(handler, path=MARG_WATCH_DIR, recursive=False)
        observer.daemon = True
        observer.start()
        logger.info("[scheduler] Marg file watcher started on %s", MARG_WATCH_DIR)
    except ImportError:
        logger.warning("[scheduler] watchdog not installed — Marg watcher disabled.")
    except Exception as exc:
        logger.error("[scheduler] Marg watcher failed to start: %s", exc)


# ═════════════════════════════════════════════════════════════════════════════
# DB schema for KPI cache
# ═════════════════════════════════════════════════════════════════════════════

def init_scheduler_tables(engine: Any) -> None:
    """Create tables needed by the scheduler."""
    ddl = """
    CREATE TABLE IF NOT EXISTS tenant_daily_kpi (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id       INTEGER NOT NULL,
        kpi_date        TEXT    NOT NULL,
        sales_total     REAL    DEFAULT 0,
        purchase_total  REAL    DEFAULT 0,
        avg_margin_pct  REAL    DEFAULT 0,
        total_bills     INTEGER DEFAULT 0,
        created_at      TEXT    DEFAULT (datetime('now')),
        UNIQUE (tenant_id, kpi_date)
    );

    CREATE TABLE IF NOT EXISTS tenant_inventory (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id       INTEGER NOT NULL,
        item_name       TEXT    NOT NULL,
        batch_no        TEXT,
        expiry_date     TEXT,
        quantity        REAL    DEFAULT 0,
        reorder_level   REAL    DEFAULT 0,
        unit            TEXT    DEFAULT 'units',
        stock_value     REAL    DEFAULT 0,
        updated_at      TEXT    DEFAULT (datetime('now'))
    );
    """
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            for stmt in ddl.strip().split(";"):
                s = stmt.strip()
                if s:
                    conn.execute(text(s))
        logger.info("[scheduler] Scheduler tables initialised.")
    except Exception as exc:
        logger.error("[scheduler] init_scheduler_tables failed: %s", exc)


# ═════════════════════════════════════════════════════════════════════════════
# PUBLIC ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

def start_scheduler(engine: Any) -> None:
    """
    Initialise DB tables, register all jobs, start the APScheduler,
    and launch the Marg file watcher.

    Call once after your Dash app and DB engine are ready:
        from scheduler import start_scheduler
        start_scheduler(engine)
    """
    global _scheduler, _engine
    _engine = engine

    # Initialise tables
    init_scheduler_tables(engine)

    from alerts import init_alert_tables
    init_alert_tables(engine)

    if not HAS_APSCHEDULER:
        logger.warning("[scheduler] APScheduler not available — jobs not scheduled.")
        # Still start Marg watcher
        threading.Thread(target=_start_marg_watcher, daemon=True).start()
        return

    _scheduler = BackgroundScheduler(timezone="Asia/Kolkata")

    # Morning digest — daily at DIGEST_HOUR:00
    _scheduler.add_job(
        job_morning_digest,
        CronTrigger(hour=DIGEST_HOUR, minute=0),
        id="morning_digest", replace_existing=True,
        name="Morning KPI Digest",
    )

    # Weekly PDF — every WEEKLY_DAY at 07:00
    _scheduler.add_job(
        job_weekly_pdf_report,
        CronTrigger(day_of_week=WEEKLY_DAY, hour=7, minute=0),
        id="weekly_pdf", replace_existing=True,
        name="Weekly PDF Report",
    )

    # Expiry check — daily at 09:00
    _scheduler.add_job(
        job_expiry_check,
        CronTrigger(hour=9, minute=0),
        id="expiry_check", replace_existing=True,
        name="Expiry Check",
    )

    # Stock check — daily at 09:30
    _scheduler.add_job(
        job_stock_check,
        CronTrigger(hour=9, minute=30),
        id="stock_check", replace_existing=True,
        name="Stock Level Check",
    )

    # Threshold check — every N hours
    _scheduler.add_job(
        job_threshold_check,
        IntervalTrigger(hours=THRESHOLD_HOURS),
        id="threshold_check", replace_existing=True,
        name=f"KPI Threshold Check (every {THRESHOLD_HOURS}h)",
    )

    _scheduler.start()
    logger.info("[scheduler] APScheduler started with %d jobs.", len(_scheduler.get_jobs()))

    # Start Marg file watcher in background thread
    threading.Thread(target=_start_marg_watcher, daemon=True).start()


def stop_scheduler() -> None:
    """Gracefully stop the scheduler (call on app shutdown)."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[scheduler] APScheduler stopped.")


def get_job_status() -> list[dict]:
    """Return current job status for the admin UI."""
    if not (_scheduler and HAS_APSCHEDULER):
        return []
    jobs = []
    for job in _scheduler.get_jobs():
        next_run = job.next_run_time
        jobs.append({
            "id":       job.id,
            "name":     job.name,
            "next_run": next_run.strftime("%d %b %Y %H:%M %Z") if next_run else "—",
        })
    return jobs
