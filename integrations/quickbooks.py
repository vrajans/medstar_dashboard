"""
integrations/quickbooks.py  —  QuickBooks OAuth 2.0 Connector
==============================================================
Uses intuitlib for OAuth 2.0 flow and QuickBooks API calls.

Flow
----
  /connect/quickbooks          → redirect to Intuit OAuth
  /callback/quickbooks         → handle code, get tokens, save to DB
  /quickbooks/sync/<tenant_id> → pull P&L report, save to DB

After connection, data is available as sales_data / purchase_data
rows with source='quickbooks' for the dashboard to display.

Setup
-----
  QB_CLIENT_ID      = from Intuit Developer Portal
  QB_CLIENT_SECRET  = from Intuit Developer Portal
  QB_REDIRECT_URI   = https://yourapp.com/callback/quickbooks
  QB_ENVIRONMENT    = production | sandbox
"""

import os
import json
import logging
from datetime import datetime, date
from typing import Any, Optional

logger = logging.getLogger(__name__)

QB_CLIENT_ID    = os.getenv("QB_CLIENT_ID",     "")
QB_CLIENT_SECRET= os.getenv("QB_CLIENT_SECRET", "")
QB_REDIRECT_URI = os.getenv("QB_REDIRECT_URI",  "")
QB_ENVIRONMENT  = os.getenv("QB_ENVIRONMENT",   "production")  # or "sandbox"

try:
    from intuitlib.client import AuthClient
    from intuitlib.enums  import Scopes
    HAS_INTUITLIB = True
except ImportError:
    HAS_INTUITLIB = False
    logger.warning("[qb] intuitlib not installed — QuickBooks OAuth disabled. "
                   "Run: pip install intuit-oauth")


# ═════════════════════════════════════════════════════════════════════════════
# DB helpers — token storage
# ═════════════════════════════════════════════════════════════════════════════

def init_qb_tables(engine: Any) -> None:
    """Create QuickBooks token storage table."""
    ddl = """
    CREATE TABLE IF NOT EXISTS qb_connections (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id       INTEGER NOT NULL UNIQUE,
        realm_id        TEXT    NOT NULL,
        access_token    TEXT,
        refresh_token   TEXT,
        access_expiry   TEXT,
        refresh_expiry  TEXT,
        environment     TEXT    DEFAULT 'production',
        connected_at    TEXT    DEFAULT (datetime('now')),
        last_synced_at  TEXT
    );
    """
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text(ddl))
        logger.info("[qb] QB tables initialised.")
    except Exception as exc:
        logger.error("[qb] init_qb_tables: %s", exc)


def save_qb_tokens(engine: Any, tenant_id: int, realm_id: str,
                   access_token: str, refresh_token: str,
                   access_expiry: str = "", refresh_expiry: str = "") -> None:
    """Upsert QuickBooks tokens after successful OAuth."""
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO qb_connections
                    (tenant_id, realm_id, access_token, refresh_token,
                     access_expiry, refresh_expiry, environment)
                VALUES (:tid, :rid, :at, :rt, :ae, :re, :env)
                ON CONFLICT(tenant_id) DO UPDATE SET
                    realm_id       = excluded.realm_id,
                    access_token   = excluded.access_token,
                    refresh_token  = excluded.refresh_token,
                    access_expiry  = excluded.access_expiry,
                    refresh_expiry = excluded.refresh_expiry,
                    environment    = excluded.environment,
                    connected_at   = datetime('now')
            """), {"tid": tenant_id, "rid": realm_id, "at": access_token,
                   "rt": refresh_token, "ae": access_expiry, "re": refresh_expiry,
                   "env": QB_ENVIRONMENT})
        logger.info("[qb] Tokens saved for tenant %s", tenant_id)
    except Exception as exc:
        logger.error("[qb] save_qb_tokens: %s", exc)


def load_qb_tokens(engine: Any, tenant_id: int) -> Optional[dict]:
    """Load stored QB tokens for a tenant."""
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT realm_id, access_token, refresh_token,
                       access_expiry, refresh_expiry, environment
                FROM   qb_connections
                WHERE  tenant_id = :tid
            """), {"tid": tenant_id}).fetchone()
        if row:
            return {"realm_id": row[0], "access_token": row[1],
                    "refresh_token": row[2], "access_expiry": row[3],
                    "refresh_expiry": row[4], "environment": row[5]}
    except Exception as exc:
        logger.error("[qb] load_qb_tokens: %s", exc)
    return None


def mark_synced(engine: Any, tenant_id: int) -> None:
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE qb_connections SET last_synced_at = datetime('now')
                WHERE tenant_id = :tid
            """), {"tid": tenant_id})
    except Exception as exc:
        logger.warning("[qb] mark_synced: %s", exc)


# ═════════════════════════════════════════════════════════════════════════════
# OAuth helpers
# ═════════════════════════════════════════════════════════════════════════════

def _make_auth_client() -> Optional[Any]:
    """Create an Intuit AuthClient."""
    if not HAS_INTUITLIB:
        return None
    if not (QB_CLIENT_ID and QB_CLIENT_SECRET and QB_REDIRECT_URI):
        logger.warning("[qb] QuickBooks credentials not set.")
        return None
    return AuthClient(
        client_id     = QB_CLIENT_ID,
        client_secret = QB_CLIENT_SECRET,
        redirect_uri  = QB_REDIRECT_URI,
        environment   = QB_ENVIRONMENT,
    )


def get_auth_url(state: str = "") -> Optional[str]:
    """Return the Intuit OAuth authorisation URL."""
    client = _make_auth_client()
    if not client:
        return None
    url = client.get_authorization_url([Scopes.ACCOUNTING])
    return url


def exchange_code_for_tokens(code: str, realm_id: str,
                              engine: Any, tenant_id: int) -> bool:
    """Exchange an authorisation code for access + refresh tokens."""
    client = _make_auth_client()
    if not client:
        return False
    try:
        client.get_bearer_token(code, realm_id=realm_id)
        save_qb_tokens(
            engine, tenant_id, realm_id,
            access_token   = client.access_token,
            refresh_token  = client.refresh_token,
            access_expiry  = str(client.access_token_expiry  or ""),
            refresh_expiry = str(client.refresh_token_expiry or ""),
        )
        return True
    except Exception as exc:
        logger.error("[qb] exchange_code_for_tokens: %s", exc)
        return False


def refresh_access_token(engine: Any, tenant_id: int) -> bool:
    """Refresh a tenant's QuickBooks access token."""
    tokens = load_qb_tokens(engine, tenant_id)
    if not tokens:
        return False
    client = _make_auth_client()
    if not client:
        return False
    try:
        client.refresh_token = tokens["refresh_token"]
        client.refresh()
        save_qb_tokens(
            engine, tenant_id, tokens["realm_id"],
            access_token   = client.access_token,
            refresh_token  = client.refresh_token or tokens["refresh_token"],
            access_expiry  = str(client.access_token_expiry or ""),
            refresh_expiry = tokens["refresh_expiry"],
        )
        logger.info("[qb] Token refreshed for tenant %s", tenant_id)
        return True
    except Exception as exc:
        logger.error("[qb] refresh_access_token: %s", exc)
        return False


# ═════════════════════════════════════════════════════════════════════════════
# QuickBooks API calls
# ═════════════════════════════════════════════════════════════════════════════

QB_API_BASE = {
    "production": "https://quickbooks.api.intuit.com",
    "sandbox":    "https://sandbox-quickbooks.api.intuit.com",
}


def _qb_api_request(realm_id: str, access_token: str, path: str,
                     environment: str = "production") -> Optional[dict]:
    """Make a QuickBooks API GET request."""
    import urllib.request
    base_url = QB_API_BASE.get(environment, QB_API_BASE["production"])
    url      = f"{base_url}/v3/company/{realm_id}/{path}?minorversion=65"
    req      = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {access_token}",
                 "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        logger.error("[qb] API request failed (%s): %s", path, exc)
        return None


def fetch_profit_loss(engine: Any, tenant_id: int,
                      start_date: str = "", end_date: str = "") -> Optional[dict]:
    """
    Fetch the Profit & Loss report from QuickBooks.
    start_date / end_date: YYYY-MM-DD format
    """
    tokens = load_qb_tokens(engine, tenant_id)
    if not tokens:
        return None

    if not start_date:
        start_date = f"{date.today().year}-01-01"
    if not end_date:
        end_date = date.today().strftime("%Y-%m-%d")

    path   = (f"reports/ProfitAndLoss"
              f"?start_date={start_date}&end_date={end_date}&summarize_column_by=Month")
    result = _qb_api_request(tokens["realm_id"], tokens["access_token"],
                              "reports/ProfitAndLoss", tokens.get("environment","production"))
    # Append date params manually since we build the URL in _qb_api_request with ?minorversion
    # Quick fix: build full path
    import urllib.request
    base_url = QB_API_BASE.get(tokens.get("environment","production"), QB_API_BASE["production"])
    url = (f"{base_url}/v3/company/{tokens['realm_id']}/reports/ProfitAndLoss"
           f"?start_date={start_date}&end_date={end_date}"
           f"&summarize_column_by=Month&minorversion=65")
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {tokens['access_token']}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            mark_synced(engine, tenant_id)
            return data
    except Exception as exc:
        logger.error("[qb] fetch_profit_loss: %s", exc)
        # Try refreshing token and retrying
        if refresh_access_token(engine, tenant_id):
            logger.info("[qb] Retrying with refreshed token...")
            tokens = load_qb_tokens(engine, tenant_id)
            req.headers["Authorization"] = f"Bearer {tokens['access_token']}"
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read())
            except Exception as exc2:
                logger.error("[qb] Retry failed: %s", exc2)
        return None


def parse_pl_to_dataframe(pl_report: dict) -> tuple:
    """
    Parse a QuickBooks P&L JSON report into two DataFrames:
    (sales_df, purchase_df) compatible with InsightHub schema.
    """
    import pandas as pd
    if not pl_report or "Rows" not in pl_report:
        return pd.DataFrame(), pd.DataFrame()

    # Extract column headers (months)
    columns_raw = pl_report.get("Columns", {}).get("Column", [])
    col_names   = []
    for c in columns_raw:
        meta = c.get("MetaData", [])
        val  = next((m["Value"] for m in meta if m["Name"] == "StartDate"), c.get("ColTitle",""))
        col_names.append(val)

    sales_rows    = []
    purchase_rows = []

    def _traverse(rows_obj):
        for section in rows_obj.get("Row", []):
            row_type = section.get("type","")
            header   = section.get("Header", {})
            header_val = header.get("ColData", [{}])[0].get("value","")

            if row_type == "Section":
                _traverse(section.get("Rows", {}))
            elif row_type == "Data":
                cells = [c.get("value","0") for c in section.get("ColData", [])]
                if len(cells) > 1:
                    name  = cells[0]
                    vals  = cells[1:]
                    for i, v in enumerate(vals):
                        try:   amount = float(v.replace(",",""))
                        except: amount = 0
                        month = col_names[i+1] if i+1 < len(col_names) else f"col_{i}"
                        if "income" in header_val.lower() or "revenue" in header_val.lower():
                            sales_rows.append({"bill_date": month, "net_amount": amount,
                                               "account_name": name, "source": "quickbooks"})
                        elif "expense" in header_val.lower() or "cost" in header_val.lower():
                            purchase_rows.append({"grn_date": month, "net_amount": amount,
                                                  "supplier_name": name, "source": "quickbooks"})

    _traverse(pl_report.get("Rows", {}))
    return pd.DataFrame(sales_rows), pd.DataFrame(purchase_rows)


def sync_quickbooks_data(engine: Any, tenant_id: int,
                          start_date: str = "", end_date: str = "") -> tuple[bool, str]:
    """
    Full sync: fetch P&L, parse to DataFrames, save to DB.
    Returns (success, message).
    """
    import pandas as pd

    pl_report = fetch_profit_loss(engine, tenant_id, start_date, end_date)
    if pl_report is None:
        return False, "Could not fetch QuickBooks P&L. Check your connection."

    sales_df, purchase_df = parse_pl_to_dataframe(pl_report)
    if sales_df.empty and purchase_df.empty:
        return False, "QuickBooks P&L returned no rows."

    sales_df["tenant_id"]    = tenant_id
    purchase_df["tenant_id"] = tenant_id

    with engine.begin() as conn:
        if not sales_df.empty:
            # Remove old QB rows for this tenant to avoid duplicates
            from sqlalchemy import text
            conn.execute(text("""
                DELETE FROM sales_data WHERE tenant_id=:tid AND source='quickbooks'
            """), {"tid": tenant_id})
            sales_df.to_sql("sales_data", conn, if_exists="append", index=False)

        if not purchase_df.empty:
            conn.execute(text("""
                DELETE FROM purchase_data WHERE tenant_id=:tid AND source='quickbooks'
            """), {"tid": tenant_id})
            purchase_df.to_sql("purchase_data", conn, if_exists="append", index=False)

    mark_synced(engine, tenant_id)
    return True, (f"Synced {len(sales_df)} income rows and "
                  f"{len(purchase_df)} expense rows from QuickBooks.")


# ═════════════════════════════════════════════════════════════════════════════
# Flask routes
# ═════════════════════════════════════════════════════════════════════════════

def register_qb_routes(flask_app: Any, engine: Any) -> None:
    """
    Register QuickBooks OAuth Flask routes.

    /connect/quickbooks          → redirect to Intuit OAuth consent screen
    /callback/quickbooks         → handle OAuth callback, save tokens
    /quickbooks/sync             → manually trigger a P&L sync
    /quickbooks/disconnect       → revoke connection
    """
    from flask import request, redirect, session, jsonify
    from flask_login import current_user, login_required

    @flask_app.route("/connect/quickbooks")
    @login_required
    def qb_connect():
        if not HAS_INTUITLIB:
            return "<h3>intuitlib not installed. Run: pip install intuit-oauth</h3>", 503
        url = get_auth_url(state=str(current_user.id))
        if not url:
            return "<h3>QuickBooks credentials not configured.</h3>", 500
        return redirect(url)

    @flask_app.route("/callback/quickbooks")
    @login_required
    def qb_callback():
        code      = request.args.get("code",    "")
        realm_id  = request.args.get("realmId", "")
        error     = request.args.get("error",   "")

        if error:
            logger.warning("[qb] OAuth error: %s", error)
            return redirect("/?qb=error")

        if not (code and realm_id):
            return redirect("/?qb=error")

        tenant_id = current_user.id   # or look up from tenant context
        success   = exchange_code_for_tokens(code, realm_id, engine, tenant_id)
        if success:
            # Auto-trigger first sync
            ok, msg = sync_quickbooks_data(engine, tenant_id)
            logger.info("[qb] Initial sync: %s", msg)
            return redirect("/?qb=connected")
        return redirect("/?qb=error")

    @flask_app.route("/quickbooks/sync")
    @login_required
    def qb_sync():
        tenant_id = current_user.id
        start     = request.args.get("start", "")
        end       = request.args.get("end",   "")
        ok, msg   = sync_quickbooks_data(engine, tenant_id, start, end)
        return jsonify({"success": ok, "message": msg})

    @flask_app.route("/quickbooks/disconnect", methods=["POST"])
    @login_required
    def qb_disconnect():
        try:
            from sqlalchemy import text
            with engine.begin() as conn:
                conn.execute(text("""
                    DELETE FROM qb_connections WHERE tenant_id = :tid
                """), {"tid": current_user.id})
            return redirect("/?qb=disconnected")
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @flask_app.route("/quickbooks/status")
    @login_required
    def qb_status():
        tokens = load_qb_tokens(engine, current_user.id)
        if tokens:
            return jsonify({
                "connected":      True,
                "realm_id":       tokens["realm_id"],
                "environment":    tokens["environment"],
                "last_synced_at": tokens.get("last_synced_at"),
            })
        return jsonify({"connected": False})

    logger.info("[qb] QuickBooks routes registered.")
