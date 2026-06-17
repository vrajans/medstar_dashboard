"""
data_loader.py - MedStar Pharmacy Analytics
Handles all data ingestion: startup file registry + live uploads.

Key functions:
  get_data()              - load from FILE_REGISTRY at startup
  detect_report_type()    - auto-detect Sales vs Purchase from headers
  parse_upload()          - parse base64 upload from dcc.Upload
  append_upload_to_db()   - save uploaded data + log history
  load_from_db()          - re-read sales + purchases from SQLite
  get_upload_history()    - fetch upload_history table
"""

import io
import os
import base64
import tempfile
import zipfile
import pandas as pd
from datetime import datetime
from sqlalchemy import create_engine, text

# ── Paths ──────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH  = os.path.join(BASE_DIR, "medstar.db")

# ── Startup file registry ──────────────────────────────────────
FILE_REGISTRY = [
    ("sales_jan26.xlsx",                 "sales",    "Keelkattalai", "Jan 2026"),
    ("sales_keelkattalai_mar26.xlsx",    "sales",    "Keelkattalai", "Mar 2026"),
    ("purchase_jan26.xlsx",              "purchase", "Keelkattalai", "Jan 2026"),
    ("purchase_keelkattalai_mar26.xlsx", "purchase", "Keelkattalai", "Mar 2026"),
    ("purchase_pallikaranai_mar26.xls",  "purchase", "Pallikaranai", "Mar 2026"),
]

# ── Column maps ────────────────────────────────────────────────
SALES_COLS = {
    0: "bill_date",       1: "net_amount",       2: "cash_bill_count",
    3: "cash_sales",      4: "credit_bill_count", 5: "credit_sales",
    6: "card_bill_count", 7: "card_sales",       10: "return_count",
    11: "cash_return",   15: "discount",         17: "total_bills",
    21: "pharma_sales",  22: "non_pharma_sales", 23: "cash_in_hand",
    24: "cost_of_sales", 25: "value",            26: "margin_pct",
}
PURCHASE_COLS = {
    0:  "supplier_code",    1:  "supplier_name",    2:  "gross_amount",
    3:  "discount_pct",     4:  "adjustment_value",  5:  "net_amount",
    6:  "vat_amount",       7:  "grn_number",        8:  "grn_date",
    9:  "invoice_number",  10:  "invoice_date",     12:  "base_amount",
    13: "sgst",            15:  "cgst",             16:  "igst",
    18: "total_gst",       19:  "amount",           20:  "dealer_type",
}

# ── Low-level helpers ──────────────────────────────────────────
def _fix_xlsx_paths(src_path, dst_path):
    """Re-package xlsx with Windows backslash paths to proper forward-slash paths."""
    with zipfile.ZipFile(src_path, "r") as zin:
        with zipfile.ZipFile(dst_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for member in zin.namelist():
                data = zin.read(member)
                fixed = member.replace("\\", "/")
                if fixed.lower() == "[content_types].xml":
                    fixed = "[Content_Types].xml"
                if fixed.lower() == "xl/sharedstrings.xml":
                    fixed = "xl/sharedStrings.xml"
                zout.writestr(fixed, data)


def _read_raw(filepath):
    """Read any Excel file (xls or xlsx) as raw DataFrame with no header."""
    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".xls":
        return pd.read_excel(filepath, engine="xlrd", header=None)
    else:
        with tempfile.NamedTemporaryFile(suffix="_fixed.xlsx", delete=False) as tmp:
            fixed = tmp.name
        try:
            _fix_xlsx_paths(filepath, fixed)
            return pd.read_excel(fixed, engine="openpyxl", header=None)
        finally:
            if os.path.exists(fixed):
                os.unlink(fixed)


def _read_raw_from_bytes(file_bytes, ext):
    """Read Excel file from bytes — used for uploaded files."""
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    try:
        return _read_raw(tmp_path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ── Auto-detection ─────────────────────────────────────────────
def detect_report_type(df_raw):
    """
    Detect Sales vs Purchase report from column headers (row index 6).
    Returns 'sales', 'purchase', or None.
    """
    try:
        header_row = df_raw.iloc[6].fillna("").astype(str).str.lower().tolist()
        h = " ".join(header_row)
        if any(k in h for k in ["supplier code", "grn number", "supplier name", "grn date"]):
            return "purchase"
        if any(k in h for k in ["bill date", "pharmasales", "total noofbills", "cash bill"]):
            return "sales"
    except Exception:
        pass
    return None


def get_detected_label(df_raw):
    """Return human-readable header info for UI display after detection."""
    try:
        header_row = [str(v) for v in df_raw.iloc[6].fillna("").tolist() if str(v).strip() not in ("", "nan")]
        return header_row[:6]
    except Exception:
        return []


# ── Core parsers (from raw DataFrame) ─────────────────────────
def _parse_sales_from_raw(df_raw, branch, month_label):
    """Parse daily sales report from a raw (header=None) DataFrame."""
    data = df_raw.iloc[7:-3].copy().reset_index(drop=True)
    cols = {k: v for k, v in SALES_COLS.items() if k < data.shape[1]}
    df = data[list(cols.keys())].copy()
    df.columns = list(cols.values())
    df["bill_date"] = pd.to_datetime(df["bill_date"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["bill_date"])
    for col in [c for c in df.columns if c != "bill_date"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    df.insert(0, "branch", branch)
    df.insert(1, "month_label", month_label)
    return df


def _parse_purchase_from_raw(df_raw, branch, month_label):
    """Parse purchase/GRN report from a raw (header=None) DataFrame."""
    data = df_raw.iloc[7:-1].copy().reset_index(drop=True)
    cols = {k: v for k, v in PURCHASE_COLS.items() if k < data.shape[1]}
    df = data[list(cols.keys())].copy()
    df.columns = list(cols.values())
    df = df.dropna(subset=["supplier_name"])
    df = df[df["supplier_name"].astype(str).str.strip() != ""]
    for date_col in ["grn_date", "invoice_date"]:
        if date_col in df.columns:
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    num_cols = ["gross_amount", "discount_pct", "adjustment_value", "net_amount",
                "vat_amount", "base_amount", "sgst", "cgst", "igst", "total_gst", "amount"]
    for col in num_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    df["supplier_name"] = df["supplier_name"].astype(str).str.strip()
    df["supplier_code"] = df["supplier_code"].astype(str).str.strip()
    df.insert(0, "branch", branch)
    df.insert(1, "month_label", month_label)
    return df


# ── File-path wrappers (startup registry) ─────────────────────
def _parse_sales(filepath, branch, month_label):
    return _parse_sales_from_raw(_read_raw(filepath), branch, month_label)


def _parse_purchase(filepath, branch, month_label):
    return _parse_purchase_from_raw(_read_raw(filepath), branch, month_label)


# ── Upload parser (from dcc.Upload base64) ─────────────────────
def parse_upload(content_b64, filename):
    """
    Parse an uploaded file from dcc.Upload base64 string.
    Returns (df_raw, report_type, error_message).
    df_raw is the raw unprocessed DataFrame — caller adds branch/month.
    """
    try:
        if "," in content_b64:
            content_b64 = content_b64.split(",", 1)[1]
        file_bytes = base64.b64decode(content_b64)
        ext = os.path.splitext(filename)[1].lower()
        if ext not in (".xlsx", ".xls", ".csv"):
            return None, None, f"Unsupported file type: {ext}. Use .xlsx, .xls, or .csv"

        if ext == ".csv":
            # CSV: try reading directly
            import io
            df_raw = pd.read_csv(io.BytesIO(file_bytes), header=None)
        else:
            df_raw = _read_raw_from_bytes(file_bytes, ext)

        report_type = detect_report_type(df_raw)
        if not report_type:
            return None, None, "Could not detect report type. Expected a Sales or Purchase report from the POS system."
        return df_raw, report_type, None

    except Exception as e:
        return None, None, f"Error reading file: {str(e)}"


def build_preview(df_raw, report_type, branch, month_label):
    """
    Parse df_raw with given branch/month and return a preview dict
    suitable for dcc.Store (JSON-serialisable).
    """
    if report_type == "sales":
        df = _parse_sales_from_raw(df_raw, branch, month_label)
    else:
        df = _parse_purchase_from_raw(df_raw, branch, month_label)

    preview_rows = df.head(5).copy()
    for col in preview_rows.select_dtypes(include=["datetime64[ns]"]).columns:
        preview_rows[col] = preview_rows[col].dt.strftime("%Y-%m-%d")

    return {
        "report_type": report_type,
        "branch": branch,
        "month_label": month_label,
        "row_count": len(df),
        "columns": list(preview_rows.columns),
        "preview": preview_rows.to_dict("records"),
        "df_json": df.to_json(date_format="iso"),
    }


# ── Database helpers ───────────────────────────────────────────
def init_db(sales_df, purchase_df):
    """Create/update SQLite DB from startup registry data."""
    engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
    if not sales_df.empty:
        sales_df.to_sql("sales", con=engine, if_exists="replace", index=False)
    if not purchase_df.empty:
        purchase_df.to_sql("purchases", con=engine, if_exists="replace", index=False)

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS upload_history (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                filename          TEXT,
                report_type       TEXT,
                branch            TEXT,
                month_label       TEXT,
                row_count         INTEGER,
                uploaded_at       TEXT,
                duplicate_warning INTEGER DEFAULT 0,
                status            TEXT    DEFAULT 'active',
                source            TEXT    DEFAULT 'manual',
                tenant_id         INTEGER DEFAULT NULL
            )
        """))
        # Migrate existing tables to add new columns if missing (SQLite ALTER TABLE)
        for col, definition in [
            ("status",    "TEXT DEFAULT 'active'"),
            ("source",    "TEXT DEFAULT 'manual'"),
            ("tenant_id", "INTEGER DEFAULT NULL"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE upload_history ADD COLUMN {col} {definition}"))
            except Exception:
                pass  # Column already exists
        # Ensure sales + purchases tables have upload_id for rollback tracking
        for tbl in ("sales", "purchases"):
            try:
                conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN upload_id INTEGER DEFAULT NULL"))
            except Exception:
                pass  # Column already exists or table not yet created
        conn.commit()

    print(f"[DB] Saved to {DB_PATH}")
    return engine


def append_upload_to_db(store_data, engine, tenant_id=None, source="manual"):
    """
    Save a confirmed upload (from dcc.Store dict) into SQLite/PostgreSQL.
    Tracks upload_id in data rows for rollback support.
    Returns (row_count, duplicate_warning_bool, error_str).
    """
    try:
        df = pd.read_json(io.StringIO(store_data["df_json"]))
        report_type = store_data["report_type"]
        branch      = store_data["branch"]
        month_label = store_data["month_label"]
        filename    = store_data.get("filename", "uploaded_file")
        table       = "sales" if report_type == "sales" else "purchases"

        duplicate = False
        try:
            existing = pd.read_sql_query(
                f"SELECT COUNT(*) AS cnt FROM {table} WHERE branch=? AND month_label=?",
                engine, params=(branch, month_label)
            )
            duplicate = int(existing["cnt"].iloc[0]) > 0
        except Exception:
            pass

        for col in ["bill_date", "grn_date", "invoice_date"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")

        # Write history row FIRST so we can get its auto-generated id
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO upload_history
                    (filename, report_type, branch, month_label, row_count,
                     uploaded_at, duplicate_warning, status, source, tenant_id)
                VALUES
                    (:fn, :rt, :br, :ml, :rc, :ua, :dw, 'active', :src, :tid)
            """), {
                "fn":  filename,
                "rt":  report_type,
                "br":  branch,
                "ml":  month_label,
                "rc":  len(df),
                "ua":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "dw":  int(duplicate),
                "src": source,
                "tid": tenant_id,
            })
            conn.commit()
            # Fetch the new upload_history id
            row = conn.execute(
                text("SELECT id FROM upload_history ORDER BY id DESC LIMIT 1")
            ).fetchone()
            upload_id = row[0] if row else None

        # Tag each data row with upload_id + tenant_id for rollback
        if upload_id:
            df["upload_id"] = upload_id
        if tenant_id:
            df["tenant_id"] = tenant_id

        df.to_sql(table, con=engine, if_exists="append", index=False)

        return len(df), duplicate, None

    except Exception as e:
        return 0, False, str(e)


def load_from_db(engine):
    """Re-read sales and purchases from SQLite after an upload."""
    try:
        s = pd.read_sql_query("SELECT * FROM sales", engine)
        s["bill_date"] = pd.to_datetime(s["bill_date"], errors="coerce")
    except Exception:
        s = pd.DataFrame()
    try:
        p = pd.read_sql_query("SELECT * FROM purchases", engine)
        for col in ["grn_date", "invoice_date"]:
            if col in p.columns:
                p[col] = pd.to_datetime(p[col], errors="coerce")
    except Exception:
        p = pd.DataFrame()
    return s, p


def get_upload_history(engine, tenant_id=None):
    """Return upload_history table as a DataFrame, optionally filtered by tenant."""
    try:
        base_sql = (
            "SELECT id, filename, report_type, branch, month_label, row_count, "
            "uploaded_at, duplicate_warning, "
            "COALESCE(status, 'active') AS status, "
            "COALESCE(source, 'manual') AS source, "
            "tenant_id "
            "FROM upload_history"
        )
        if tenant_id is not None:
            base_sql += " WHERE tenant_id = :tid ORDER BY id DESC"
            return pd.read_sql_query(text(base_sql), engine, params={"tid": tenant_id})
        return pd.read_sql_query(base_sql + " ORDER BY id DESC", engine)
    except Exception:
        return pd.DataFrame()


# ── Startup loader ─────────────────────────────────────────────
def load_all_data():
    all_sales, all_purchase = [], []
    engine = _get_sqlite_engine()
    try:
        s = pd.read_sql_query("SELECT * FROM sales", engine)
        s["bill_date"] = pd.to_datetime(s["bill_date"], errors="coerce")
        all_sales.append(s)
    except Exception:
        pass
    try:
        p = pd.read_sql_query("SELECT * FROM purchases", engine)
        for col in ["grn_date", "invoice_date"]:
            if col in p.columns:
                p[col] = pd.to_datetime(p[col], errors="coerce")
        all_purchase.append(p)
    except Exception:
        pass
    sales_df    = pd.concat(all_sales,    ignore_index=True) if all_sales    else pd.DataFrame()
    purchase_df = pd.concat(all_purchase, ignore_index=True) if all_purchase else pd.DataFrame()
    return sales_df, purchase_df, engine


def _get_sqlite_engine():
    """Return a SQLAlchemy engine pointing at the local SQLite DB."""
    engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
    return engine


def get_data():
    """
    Public entry point used by app.py at startup.
    Reads the FILE_REGISTRY, parses each file, seeds the DB, returns
    (sales_df, purchase_df, engine).
    Falls back to loading from an existing DB if files are missing.
    """
    all_sales, all_purchase = [], []

    for filename, rtype, branch, month_label in FILE_REGISTRY:
        filepath = os.path.join(DATA_DIR, filename)
        if not os.path.exists(filepath):
            continue
        try:
            if rtype == "sales":
                all_sales.append(_parse_sales(filepath, branch, month_label))
            else:
                all_purchase.append(_parse_purchase(filepath, branch, month_label))
        except Exception as exc:
            print(f"[data_loader] Skipping {filename}: {exc}")

    sales_df    = pd.concat(all_sales,    ignore_index=True) if all_sales    else pd.DataFrame()
    purchase_df = pd.concat(all_purchase, ignore_index=True) if all_purchase else pd.DataFrame()

    # Seed DB (or load from existing DB if no files found)
    if not sales_df.empty or not purchase_df.empty:
        engine = init_db(sales_df, purchase_df)
    else:
        engine = _get_sqlite_engine()
        sales_df, purchase_df = load_from_db(engine)

    return sales_df, purchase_df, engine
