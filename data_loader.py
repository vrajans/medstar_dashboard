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

# -- Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH  = os.path.join(BASE_DIR, "medstar.db")

FILE_REGISTRY = [
    ("sales_jan26.xlsx",                 "sales",    "Keelkattalai", "Jan 2026"),
    ("sales_keelkattalai_mar26.xlsx",    "sales",    "Keelkattalai", "Mar 2026"),
    ("purchase_jan26.xlsx",              "purchase", "Keelkattalai", "Jan 2026"),
    ("purchase_keelkattalai_mar26.xlsx", "purchase", "Keelkattalai", "Mar 2026"),
    ("purchase_pallikaranai_mar26.xls",  "purchase", "Pallikaranai", "Mar 2026"),
]

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

def _fix_xlsx_paths(src_path, dst_path):
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
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    try:
        return _read_raw(tmp_path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

def detect_report_type(df_raw):
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

_GENERIC_REVENUE_HINTS = [
    "revenue", "sales", "income", "mrr", "arr", "billing", "subscription",
    "amount", "net", "total", "value", "receipts", "turnover", "gross",
]
_GENERIC_COST_HINTS = [
    "expense", "cost", "expenditure", "purchase", "payment", "invoice",
    "vendor", "supplier", "bill", "debit", "outflow", "spend",
]

def _find_header_row(df_raw, max_scan=20):
    for i in range(min(max_scan, len(df_raw) - 1)):
        row = df_raw.iloc[i].fillna("").astype(str)
        non_empty = [v.strip() for v in row if v.strip() not in ("", "nan")]
        if len(non_empty) < 3:
            continue
        label_cells = sum(
            1 for v in non_empty
            if len(v) < 60
            and not v.replace(".", "").replace("-", "").replace(",", "").isnumeric()
            and not (len(v) > 30 and " " in v)
        )
        if label_cells >= 3 and label_cells / len(non_empty) >= 0.5:
            for lookahead in range(1, min(4, len(df_raw) - i)):
                nxt = df_raw.iloc[i + lookahead].fillna("").astype(str)
                nxt_non_empty = [v.strip() for v in nxt if v.strip() not in ("", "nan")]
                if len(nxt_non_empty) >= 2:
                    return i
    return 0

def detect_generic_report(df_raw):
    try:
        hdr_idx = _find_header_row(df_raw)
        headers = df_raw.iloc[hdr_idx].fillna("").astype(str).str.lower().tolist()
        h = " ".join(headers)
        rev_score  = sum(1 for k in _GENERIC_REVENUE_HINTS if k in h)
        cost_score = sum(1 for k in _GENERIC_COST_HINTS    if k in h)
        if cost_score > rev_score:
            return "generic_purchases"
        return "generic_sales"
    except Exception:
        return "generic_sales"

def _find_col(columns, hints):
    cols_lower = [(c, c.lower()) for c in columns]
    for hint in hints:
        for col, col_l in cols_lower:
            if hint in col_l:
                return col
    return None

def _parse_generic_from_raw(df_raw, branch, month_label):
    hdr_idx = _find_header_row(df_raw)
    raw_headers = df_raw.iloc[hdr_idx].fillna("").astype(str).str.strip().tolist()
    data = df_raw.iloc[hdr_idx + 1:].copy().reset_index(drop=True)
    seen = {}
    deduped = []
    for h in raw_headers:
        if h in seen:
            seen[h] += 1
            deduped.append(f"{h}_{seen[h]}")
        else:
            seen[h] = 0
            deduped.append(h)
    data.columns = deduped
    data = data.loc[:, [c for c in data.columns if str(c).strip() not in ("", "nan")]]
    data = data.dropna(how="all")
    cols = list(data.columns)
    amount_hints = ["revenue", "amount", "total", "net", "sales", "mrr",
                    "value", "income", "billing", "price", "cost", "expense",
                    "gross", "receipts"]
    amount_col = _find_col(cols, amount_hints)
    date_hints = ["date", "month", "period", "week", "day", "time", "year"]
    date_col = _find_col(cols, date_hints)
    name_hints = ["name", "product", "customer", "client", "item",
                  "description", "category", "department", "segment", "channel"]
    name_col = _find_col(cols, name_hints)
    if amount_col:
        data["net_amount"] = (
            data[amount_col].astype(str)
            .str.replace(r"[\u20b9$\u20ac\xa3,\s]", "", regex=True)
            .pipe(pd.to_numeric, errors="coerce")
            .fillna(0)
        )
    else:
        data["net_amount"] = 0
    if date_col:
        data["bill_date"] = pd.to_datetime(data[date_col], errors="coerce")
        # Drop rows where date couldn't be parsed — these are usually
        # blank rows, sub-headers, or summary/total rows at the bottom.
        # Never fill with today's date — that creates false future data points.
        data = data.dropna(subset=["bill_date"])
        # Sanity-check: drop any dates that are in the future (more than 60 days ahead)
        future_cutoff = pd.Timestamp.now() + pd.Timedelta(days=60)
        data = data[data["bill_date"] <= future_cutoff]
    else:
        data["bill_date"] = pd.NaT
    if name_col:
        data["supplier_name"] = data[name_col].astype(str).str.strip()
    else:
        data["supplier_name"] = branch
    data.insert(0, "branch", branch)
    data.insert(1, "month_label", month_label)
    data = data[data["net_amount"] != 0].copy()
    return data

def get_detected_label(df_raw):
    try:
        header_row = [str(v) for v in df_raw.iloc[6].fillna("").tolist() if str(v).strip() not in ("", "nan")]
        return header_row[:6]
    except Exception:
        return []

def _parse_sales_from_raw(df_raw, branch, month_label):
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

def _parse_sales(filepath, branch, month_label):
    return _parse_sales_from_raw(_read_raw(filepath), branch, month_label)

def _parse_purchase(filepath, branch, month_label):
    return _parse_purchase_from_raw(_read_raw(filepath), branch, month_label)

def _detect_square_or_shopify(file_bytes: bytes, ext: str):
    """
    US-301/US-303: Detect and parse Square POS or Shopify CSV exports.
    Returns (df_raw_compatible, report_type) or (None, None).
    The returned df_raw is already in InsightHub sales schema (pre-parsed).
    """
    if ext != ".csv":
        return None, None
    try:
        header_row = pd.read_csv(io.BytesIO(file_bytes), nrows=0).columns.tolist()
        header_set = set(h.strip() for h in header_row)

        from integrations.square_shopify import parse_square_csv, parse_shopify_csv

        # Square fingerprint: unique column names from Square dashboard export
        if any(c in header_set for c in ["Gross Sales", "Net Sales", "Device Name",
                                          "Device Nickname", "Payment Type"]):
            df = parse_square_csv(io.BytesIO(file_bytes))
            if not df.empty:
                # Translate to generic_sales df_raw format so build_preview works
                df_out = df.rename(columns={
                    "bill_date":   "date",
                    "net_amount":  "amount",
                    "item_name":   "item",
                    "branch":      "location",
                    "tax_amount":  "tax",
                })
                return df_out, "square_sales"

        # Shopify fingerprint
        if any(c in header_set for c in ["Financial Status", "Fulfillment Status",
                                          "Lineitem quantity", "Paid at", "Lineitem name"]):
            df = parse_shopify_csv(io.BytesIO(file_bytes))
            if not df.empty:
                df_out = df.rename(columns={
                    "bill_date":  "date",
                    "net_amount": "amount",
                    "item_name":  "item",
                    "branch":     "location",
                    "tax_amount": "tax",
                    "state":      "state",
                })
                return df_out, "shopify_sales"
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("[parse_upload] Square/Shopify detect error: %s", exc)
    return None, None


def parse_upload(content_b64, filename, domain="pharmacy"):
    try:
        if "," in content_b64:
            content_b64 = content_b64.split(",", 1)[1]
        file_bytes = base64.b64decode(content_b64)
        ext = os.path.splitext(filename)[1].lower()
        if ext not in (".xlsx", ".xls", ".csv"):
            return None, None, f"Unsupported file type: {ext}. Use .xlsx, .xls, or .csv"
        if ext == ".csv":
            df_raw = pd.read_csv(io.BytesIO(file_bytes), header=None)
        else:
            df_raw = _read_raw_from_bytes(file_bytes, ext)

        # ── Pharmacy-specific detection (Marg / custom POS) ──
        report_type = detect_report_type(df_raw)
        if report_type:
            return df_raw, report_type, None

        # ── US-301/303: Square POS & Shopify before generic fallback ──
        sq_df, sq_type = _detect_square_or_shopify(file_bytes, ext)
        if sq_df is not None and sq_type:
            return sq_df, sq_type, None

        # ── Generic CSV/XLSX fallback ──
        if domain != "pharmacy":
            report_type = detect_generic_report(df_raw)
            if report_type:
                return df_raw, report_type, None
        # Allow generic fallback even for pharmacy domain if columns look right
        generic_type = detect_generic_report(df_raw)
        if generic_type:
            return df_raw, generic_type, None

        if domain == "pharmacy":
            msg = ("Could not detect report type. "
                   "Expected a Sales or Purchase report from the POS system.")
        else:
            msg = ("Could not read the file. "
                   "Please upload a standard CSV or Excel file with column headers in the first row.")
        return None, None, msg
    except Exception as e:
        return None, None, f"Error reading file: {str(e)}"

def build_preview(df_raw, report_type, branch, month_label):
    if report_type == "sales":
        df = _parse_sales_from_raw(df_raw, branch, month_label)
    elif report_type == "purchase":
        df = _parse_purchase_from_raw(df_raw, branch, month_label)
    elif report_type in ("generic_sales", "generic_purchases",
                         "square_sales", "shopify_sales"):
        df = _parse_generic_from_raw(df_raw, branch, month_label)
    else:
        df = _parse_generic_from_raw(df_raw, branch, month_label)
    preview_rows = df.head(5).copy()
    for col in preview_rows.select_dtypes(include=["datetime64[ns]"]).columns:
        preview_rows[col] = preview_rows[col].dt.strftime("%Y-%m-%d")
    _seen = {}
    _cols = []
    for c in df.columns:
        c = str(c)
        if c in _seen:
            _seen[c] += 1
            _cols.append(f"{c}_{_seen[c]}")
        else:
            _seen[c] = 0
            _cols.append(c)
    df.columns = _cols
    preview_rows.columns = _cols[:len(preview_rows.columns)]
    return {
        "report_type": report_type,
        "branch": branch,
        "month_label": month_label,
        "row_count": len(df),
        "columns": list(df.columns),
        "preview": preview_rows.to_dict("records"),
        "df_json": df.to_json(orient="records", date_format="iso"),
    }

def init_db(sales_df, purchase_df):
    engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
    # IMPORTANT: Only seed MedStar demo data if the table is empty.
    # Never replace existing data — tenant uploads must survive restarts.
    if not sales_df.empty:
        with engine.connect() as _c:
            try:
                existing = _c.execute(text("SELECT COUNT(*) FROM sales")).scalar()
            except Exception:
                existing = 0
        if existing == 0:
            sales_df.to_sql("sales", con=engine, if_exists="append", index=False)
            print(f"[DB] Seeded sales with {len(sales_df)} MedStar rows (first run only)")
        else:
            print(f"[DB] Sales table already has {existing} rows — skipping seed")
    if not purchase_df.empty:
        with engine.connect() as _c:
            try:
                existing = _c.execute(text("SELECT COUNT(*) FROM purchases")).scalar()
            except Exception:
                existing = 0
        if existing == 0:
            purchase_df.to_sql("purchases", con=engine, if_exists="append", index=False)
            print(f"[DB] Seeded purchases with {len(purchase_df)} MedStar rows (first run only)")
        else:
            print(f"[DB] Purchases table already has {existing} rows — skipping seed")
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
        for col, definition in [
            ("status",    "TEXT DEFAULT 'active'"),
            ("source",    "TEXT DEFAULT 'manual'"),
            ("tenant_id", "INTEGER DEFAULT NULL"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE upload_history ADD COLUMN {col} {definition}"))
            except Exception:
                pass
        # Ensure all tables have upload_id, tenant_id, and supplier_name
        # so generic tenant uploads can be stored alongside pharmacy data.
        _extra_cols = [
            ("upload_id",     "INTEGER DEFAULT NULL"),
            ("tenant_id",     "INTEGER DEFAULT NULL"),
            ("supplier_name", "TEXT    DEFAULT NULL"),
        ]
        for tbl in ("sales", "purchases"):
            for col, typedef in _extra_cols:
                try:
                    conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN {col} {typedef}"))
                except Exception:
                    pass  # column already exists
        conn.commit()
    print(f"[DB] Saved to {DB_PATH}")
    return engine

def append_upload_to_db(store_data, engine, tenant_id=None, source="manual"):
    try:
        df = pd.read_json(io.StringIO(store_data["df_json"]))
        report_type = store_data["report_type"]
        branch      = store_data["branch"]
        month_label = store_data["month_label"]
        filename    = store_data.get("filename", "uploaded_file")
        if report_type in ("sales", "generic_sales", "square_sales", "shopify_sales"):
            table = "sales"
        elif report_type in ("purchase", "generic_purchases"):
            table = "purchases"
        else:
            table = "sales"
        # ── Duplicate detection: same branch + period + tenant ────────────────
        # Build a scoped WHERE clause so tenant data never overlaps MedStar data.
        if tenant_id is not None:
            dup_sql = (f"SELECT id FROM upload_history "
                       f"WHERE branch=? AND month_label=? AND tenant_id=? AND status='active'")
            dup_params = (branch, month_label, tenant_id)
        else:
            dup_sql = (f"SELECT id FROM upload_history "
                       f"WHERE branch=? AND month_label=? AND tenant_id IS NULL AND status='active'")
            dup_params = (branch, month_label)

        duplicate = False
        old_upload_ids = []
        try:
            dup_rows = pd.read_sql_query(dup_sql, engine, params=dup_params)
            if not dup_rows.empty:
                duplicate = True
                old_upload_ids = dup_rows["id"].tolist()
        except Exception:
            pass

        # ── If duplicate: delete old data rows and mark old history entries replaced ──
        if duplicate and old_upload_ids:
            with engine.connect() as _dc:
                for _old_uid in old_upload_ids:
                    _dc.execute(text(f"DELETE FROM {table} WHERE upload_id=:uid"),
                                {"uid": _old_uid})
                    _dc.execute(text("UPDATE upload_history SET status='replaced' WHERE id=:uid"),
                                {"uid": _old_uid})
                _dc.commit()

        for col in ["bill_date", "grn_date", "invoice_date"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
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
            row = conn.execute(
                text("SELECT id FROM upload_history ORDER BY id DESC LIMIT 1")
            ).fetchone()
            upload_id = row[0] if row else None
        if upload_id:
            df["upload_id"] = upload_id
        if tenant_id:
            df["tenant_id"] = tenant_id

        # Ensure the table has all the columns we want to insert
        # (adds supplier_name / tenant_id to sales if missing from older DBs).
        with engine.connect() as _mc:
            for _col, _typedef in [
                ("upload_id",     "INTEGER DEFAULT NULL"),
                ("tenant_id",     "INTEGER DEFAULT NULL"),
                ("supplier_name", "TEXT    DEFAULT NULL"),
            ]:
                try:
                    _mc.execute(text(f"ALTER TABLE {table} ADD COLUMN {_col} {_typedef}"))
                    _mc.commit()
                except Exception:
                    pass  # already exists

        # Strip any columns the table doesn't know about (e.g. QuickBooks extras).
        from sqlalchemy import inspect as _sa_inspect
        _table_cols = {c["name"] for c in _sa_inspect(engine).get_columns(table)}
        df = df[[c for c in df.columns if c in _table_cols]]

        df.to_sql(table, con=engine, if_exists="append", index=False)
        return len(df), duplicate, None
    except Exception as e:
        return 0, False, str(e)

def load_from_db(engine):
    try:
        s = pd.read_sql_query("SELECT * FROM sales", engine)
        s["bill_date"] = pd.to_datetime(s["bill_date"], errors="coerce")
    except Exception:
        s = pd.DataFrame()
    try:
        p = pd.read_sql_query("SELECT * FROM purchases", engine)
        p["bill_date"] = pd.to_datetime(p["bill_date"], errors="coerce")
    except Exception:
        p = pd.DataFrame()
    return s, p

def _get_sqlite_engine():
    """Return a SQLAlchemy engine pointing at the local SQLite DB."""
    return create_engine(f"sqlite:///{DB_PATH}", echo=False)


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

    if not sales_df.empty or not purchase_df.empty:
        engine = init_db(sales_df, purchase_df)
    else:
        engine = _get_sqlite_engine()
        sales_df, purchase_df = load_from_db(engine)

    return sales_df, purchase_df, engine


def get_upload_history(engine, tenant_id=None):
    try:
        base_sql = (
            "SELECT id, filename, report_type, branch, month_label, row_count, "
            "uploaded_at, duplicate_warning, status, source, tenant_id "
            "FROM upload_history"
        )
        if tenant_id is not None:
            base_sql += " WHERE tenant_id = :tid AND status != 'replaced' ORDER BY id DESC"
            return pd.read_sql_query(text(base_sql), engine, params={"tid": tenant_id})
        return pd.read_sql_query(base_sql + " WHERE status != 'replaced' ORDER BY id DESC", engine)
    except Exception:
        return pd.DataFrame()

def rollback_upload(engine, upload_id):
    try:
        uid = int(upload_id)
    except (TypeError, ValueError):
        return "Invalid upload ID."
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT report_type FROM upload_history WHERE id=:uid"),
                {"uid": uid},
            ).fetchone()
            if not row:
                return f"Upload #{uid} not found."
            table = "sales" if row[0] in ("sales", "generic_sales") else "purchases"
            conn.execute(text(f"DELETE FROM {table} WHERE upload_id=:uid"), {"uid": uid})
            conn.execute(
                text("UPDATE upload_history SET status='rolled_back' WHERE id=:uid"),
                {"uid": uid},
            )
            conn.commit()
        return f"Upload #{uid} rolled back successfully."
    except Exception as e:
        return f"Rollback error: {str(e)}"


def cleanup_duplicate_uploads(engine, tenant_id=None):
    """
    Find all groups where the same branch+period+tenant has multiple active
    uploads, keep only the latest, delete older data rows and mark older
    history entries as 'replaced'.

    Returns (deleted_data_rows, replaced_history_entries, error_or_None)
    """
    try:
        with engine.connect() as conn:
            if tenant_id is not None:
                rows = conn.execute(text("""
                    SELECT branch, month_label, tenant_id,
                           COUNT(*) as cnt, MAX(id) as keep_id,
                           GROUP_CONCAT(id) as all_ids, report_type
                    FROM upload_history
                    WHERE status='active' AND tenant_id=:tid
                    GROUP BY branch, month_label, COALESCE(tenant_id,-1)
                    HAVING cnt > 1
                """), {"tid": tenant_id}).fetchall()
            else:
                rows = conn.execute(text("""
                    SELECT branch, month_label, tenant_id,
                           COUNT(*) as cnt, MAX(id) as keep_id,
                           GROUP_CONCAT(id) as all_ids, report_type
                    FROM upload_history
                    WHERE status='active'
                    GROUP BY branch, month_label, COALESCE(tenant_id,-1)
                    HAVING cnt > 1
                """)).fetchall()
            if not rows:
                return 0, 0, None
            total_data = 0
            total_hist = 0
            for r in rows:
                all_ids  = [int(x) for x in str(r[5]).split(",")]
                keep_id  = int(r[4])
                rt       = r[6] or "sales"
                table    = "purchases" if rt in ("purchase", "generic_purchases") else "sales"
                drop_ids = [i for i in all_ids if i != keep_id]
                for old_id in drop_ids:
                    res = conn.execute(text(f"DELETE FROM {table} WHERE upload_id=:uid"), {"uid": old_id})
                    total_data += res.rowcount
                    conn.execute(text("UPDATE upload_history SET status='replaced' WHERE id=:uid"), {"uid": old_id})
                    total_hist += 1
            conn.commit()
        return total_data, total_hist, None
    except Exception as e:
        return 0, 0, str(e)
