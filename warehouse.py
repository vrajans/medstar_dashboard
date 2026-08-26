"""
warehouse.py  —  InsightHub Dimensional Warehouse (Phase 1, shadow mode)
========================================================================
Builds and maintains a conformed STAR SCHEMA alongside the existing flat
`sales` / `purchases` tables. This module is additive and non-destructive:
it never modifies the flat tables, so the current Dash demo keeps working
untouched while the warehouse is validated in parallel.

Design (see BRD §5.1–5.3)
-------------------------
Facts + dimensions, keyed by tenant_id + domain + customer_code, with:
  • Idempotent, hash-keyed upsert loading  → re-uploading unchanged data is a no-op
  • Slowly Changing Dimension Type 2       → dimension attribute changes preserve history
  • Serving marts                          → flattened OBT per tenant for fast analytics/AI

Tables (all prefixed `wh_` to stay clearly separate from the live schema)
-------------------------------------------------------------------------
  wh_dim_date          calendar dimension
  wh_dim_tenant        tenant / customer_code / domain
  wh_dim_entity        SCD2 — conformed grouping entity (branch / supplier / customer)
  wh_dim_product       SCD2 — item / product (nullable on aggregate grains)
  wh_fct_transaction   conformed fact — one row per source measure event
  wh_mart_transaction  serving mart — flattened fact for analytics + AI context

Public API
----------
  init_warehouse(engine)
  load_tenant_to_warehouse(engine, tenant_id, domain="generic") -> dict stats
  build_serving_mart(engine, tenant_id) -> int rows
  read_mart(engine, tenant_id, txn_type=None) -> DataFrame
  backfill_all(engine) -> dict   (loads every tenant found in the flat tables)
"""

from __future__ import annotations

import hashlib
from datetime import datetime, date
from typing import Optional

import pandas as pd
from sqlalchemy import text

_NULL_TS = "9999-12-31"
_TODAY   = lambda: date.today().isoformat()
_NOW     = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────
def _dialect(engine) -> str:
    try:
        return engine.dialect.name
    except Exception:
        return "sqlite"


def _pk_clause(engine) -> str:
    """Portable auto-increment surrogate-key column."""
    return ("INTEGER PRIMARY KEY AUTOINCREMENT" if _dialect(engine) == "sqlite"
            else "SERIAL PRIMARY KEY")


def _hash(*parts) -> str:
    """Stable SHA1 over the string form of the given parts."""
    joined = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


def _num(v, default=0.0) -> float:
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return float(default)
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _date_key(d) -> Optional[int]:
    if d is None or (isinstance(d, float) and pd.isna(d)):
        return None
    try:
        ts = pd.to_datetime(d, errors="coerce")
        if pd.isna(ts):
            return None
        return int(ts.strftime("%Y%m%d"))
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# schema
# ─────────────────────────────────────────────────────────────────────────────
def init_warehouse(engine) -> None:
    """Create all warehouse tables if they do not already exist."""
    pk = _pk_clause(engine)
    ddl = [
        f"""CREATE TABLE IF NOT EXISTS wh_dim_date (
                date_key    INTEGER PRIMARY KEY,
                full_date   TEXT,
                year        INTEGER,
                quarter     INTEGER,
                month       INTEGER,
                month_name  TEXT,
                day         INTEGER,
                weekday     INTEGER,
                is_weekend  INTEGER
        )""",
        f"""CREATE TABLE IF NOT EXISTS wh_dim_tenant (
                tenant_key    {pk},
                tenant_id     INTEGER,
                customer_code TEXT,
                domain        TEXT,
                created_at    TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS wh_dim_entity (
                entity_key   {pk},
                tenant_id    INTEGER,
                entity_type  TEXT,
                entity_name  TEXT,
                entity_code  TEXT,
                attr_hash    TEXT,
                valid_from   TEXT,
                valid_to     TEXT,
                is_current   INTEGER DEFAULT 1,
                created_at   TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS wh_dim_product (
                product_key  {pk},
                tenant_id    INTEGER,
                product_name TEXT,
                category     TEXT,
                attr_hash    TEXT,
                valid_from   TEXT,
                valid_to     TEXT,
                is_current   INTEGER DEFAULT 1,
                created_at   TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS wh_fct_transaction (
                fct_id            {pk},
                tenant_id         INTEGER,
                customer_code     TEXT,
                domain            TEXT,
                txn_type          TEXT,
                date_key          INTEGER,
                entity_key        INTEGER,
                product_key       INTEGER,
                branch_name       TEXT,
                party_name        TEXT,
                gross_amount      REAL,
                net_amount        REAL,
                cost_amount       REAL,
                tax_amount        REAL,
                discount_amount   REAL,
                margin_pct        REAL,
                quantity          REAL,
                txn_count         REAL,
                source_upload_id  INTEGER,
                business_key_hash TEXT,
                row_hash          TEXT,
                load_batch_id     TEXT,
                created_at        TEXT,
                updated_at        TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS wh_mart_transaction (
                tenant_id     INTEGER,
                domain        TEXT,
                txn_type      TEXT,
                date_key      INTEGER,
                full_date     TEXT,
                year          INTEGER,
                month         INTEGER,
                month_name    TEXT,
                entity_type   TEXT,
                entity_name   TEXT,
                branch_name   TEXT,
                party_name    TEXT,
                product_name  TEXT,
                gross_amount  REAL,
                net_amount    REAL,
                cost_amount   REAL,
                tax_amount    REAL,
                margin_pct    REAL,
                txn_count     REAL
        )""",
    ]
    idx = [
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_fct_bk ON wh_fct_transaction(business_key_hash)",
        "CREATE INDEX IF NOT EXISTS ix_fct_tenant ON wh_fct_transaction(tenant_id, txn_type)",
        "CREATE INDEX IF NOT EXISTS ix_entity_lookup ON wh_dim_entity(tenant_id, entity_type, entity_name, is_current)",
        "CREATE INDEX IF NOT EXISTS ix_product_lookup ON wh_dim_product(tenant_id, product_name, is_current)",
        "CREATE INDEX IF NOT EXISTS ix_mart_tenant ON wh_mart_transaction(tenant_id, txn_type)",
    ]
    # additive columns for warehouses created before branch_name/party_name existed
    alters = [
        "ALTER TABLE wh_fct_transaction  ADD COLUMN branch_name TEXT",
        "ALTER TABLE wh_fct_transaction  ADD COLUMN party_name  TEXT",
        "ALTER TABLE wh_mart_transaction ADD COLUMN branch_name TEXT",
        "ALTER TABLE wh_mart_transaction ADD COLUMN party_name  TEXT",
    ]
    # Run each statement in its OWN transaction. On PostgreSQL a single failed
    # statement aborts the whole transaction, so a shared transaction would roll
    # back every table when one ALTER/INDEX errors. Per-statement isolation works
    # identically on SQLite and is the portable-safe approach.
    for stmt in ddl + idx:
        try:
            with engine.begin() as c:
                c.execute(text(stmt))
        except Exception as e:
            print(f"[warehouse] DDL skipped: {e}")
    for stmt in alters:
        try:
            with engine.begin() as c:
                c.execute(text(stmt))
        except Exception:
            pass  # column already exists


# ─────────────────────────────────────────────────────────────────────────────
# dimension upserts
# ─────────────────────────────────────────────────────────────────────────────
def _upsert_date(conn, dk: int) -> None:
    exists = conn.execute(text("SELECT 1 FROM wh_dim_date WHERE date_key=:k"),
                          {"k": dk}).fetchone()
    if exists:
        return
    ts = pd.to_datetime(str(dk), format="%Y%m%d")
    conn.execute(text("""
        INSERT INTO wh_dim_date
            (date_key, full_date, year, quarter, month, month_name, day, weekday, is_weekend)
        VALUES (:k, :fd, :y, :q, :m, :mn, :d, :wd, :we)
    """), {
        "k": dk, "fd": ts.date().isoformat(), "y": int(ts.year),
        "q": int((ts.month - 1) // 3 + 1), "m": int(ts.month),
        "mn": ts.strftime("%b"), "d": int(ts.day),
        "wd": int(ts.weekday()), "we": 1 if ts.weekday() >= 5 else 0,
    })


def _upsert_tenant(conn, tenant_id, customer_code, domain) -> None:
    row = conn.execute(text("SELECT tenant_key FROM wh_dim_tenant WHERE tenant_id=:t"),
                       {"t": tenant_id}).fetchone()
    if row:
        conn.execute(text("UPDATE wh_dim_tenant SET domain=:d, customer_code=:cc WHERE tenant_id=:t"),
                     {"d": domain, "cc": customer_code, "t": tenant_id})
    else:
        conn.execute(text("""INSERT INTO wh_dim_tenant (tenant_id, customer_code, domain, created_at)
                             VALUES (:t, :cc, :d, :now)"""),
                     {"t": tenant_id, "cc": customer_code, "d": domain, "now": _NOW()})


def _scd2_entity(conn, tenant_id, entity_type, entity_name, entity_code) -> Optional[int]:
    """Return the current entity_key, applying SCD2 if attributes changed."""
    if not entity_name:
        entity_name = "(unknown)"
    attr_hash = _hash(entity_code, entity_type)
    cur = conn.execute(text("""
        SELECT entity_key, attr_hash FROM wh_dim_entity
        WHERE tenant_id=:t AND entity_type=:et AND entity_name=:en AND is_current=1
    """), {"t": tenant_id, "et": entity_type, "en": entity_name}).fetchone()
    if cur:
        if cur[1] == attr_hash:
            return cur[0]
        # attribute changed → close current version, open a new one (SCD2)
        conn.execute(text("""UPDATE wh_dim_entity SET valid_to=:vt, is_current=0
                             WHERE entity_key=:k"""),
                     {"vt": _TODAY(), "k": cur[0]})
    conn.execute(text("""
        INSERT INTO wh_dim_entity
            (tenant_id, entity_type, entity_name, entity_code, attr_hash,
             valid_from, valid_to, is_current, created_at)
        VALUES (:t, :et, :en, :ec, :ah, :vf, :vt, 1, :now)
    """), {"t": tenant_id, "et": entity_type, "en": entity_name, "ec": entity_code,
           "ah": attr_hash, "vf": _TODAY(), "vt": _NULL_TS, "now": _NOW()})
    row = conn.execute(text("""
        SELECT entity_key FROM wh_dim_entity
        WHERE tenant_id=:t AND entity_type=:et AND entity_name=:en AND is_current=1
    """), {"t": tenant_id, "et": entity_type, "en": entity_name}).fetchone()
    return row[0] if row else None


def _scd2_product(conn, tenant_id, product_name, category) -> Optional[int]:
    if not product_name:
        return None
    attr_hash = _hash(category)
    cur = conn.execute(text("""
        SELECT product_key, attr_hash FROM wh_dim_product
        WHERE tenant_id=:t AND product_name=:pn AND is_current=1
    """), {"t": tenant_id, "pn": product_name}).fetchone()
    if cur:
        if cur[1] == attr_hash:
            return cur[0]
        conn.execute(text("""UPDATE wh_dim_product SET valid_to=:vt, is_current=0
                             WHERE product_key=:k"""),
                     {"vt": _TODAY(), "k": cur[0]})
    conn.execute(text("""
        INSERT INTO wh_dim_product
            (tenant_id, product_name, category, attr_hash, valid_from, valid_to, is_current, created_at)
        VALUES (:t, :pn, :cat, :ah, :vf, :vt, 1, :now)
    """), {"t": tenant_id, "pn": product_name, "cat": category, "ah": attr_hash,
           "vf": _TODAY(), "vt": _NULL_TS, "now": _NOW()})
    row = conn.execute(text("""
        SELECT product_key FROM wh_dim_product
        WHERE tenant_id=:t AND product_name=:pn AND is_current=1
    """), {"t": tenant_id, "pn": product_name}).fetchone()
    return row[0] if row else None


# ─────────────────────────────────────────────────────────────────────────────
# fact upsert
# ─────────────────────────────────────────────────────────────────────────────
def _upsert_fact(conn, rec: dict, stats: dict) -> None:
    """Idempotent fact upsert keyed by business_key_hash."""
    existing = conn.execute(text("""
        SELECT fct_id, row_hash FROM wh_fct_transaction WHERE business_key_hash=:bk
    """), {"bk": rec["business_key_hash"]}).fetchone()

    if existing:
        if existing[1] == rec["row_hash"]:
            stats["unchanged"] += 1
            return
        conn.execute(text("""
            UPDATE wh_fct_transaction SET
                date_key=:dk, entity_key=:ek, product_key=:pk,
                branch_name=:branch_name, party_name=:party_name,
                gross_amount=:gross, net_amount=:net, cost_amount=:cost,
                tax_amount=:tax, discount_amount=:disc, margin_pct=:margin,
                quantity=:qty, txn_count=:cnt, source_upload_id=:uid,
                row_hash=:row_hash, load_batch_id=:batch, updated_at=:now
            WHERE fct_id=:fid
        """), {**rec, "fid": existing[0], "now": _NOW()})
        stats["updated"] += 1
        return

    conn.execute(text("""
        INSERT INTO wh_fct_transaction
            (tenant_id, customer_code, domain, txn_type, date_key, entity_key, product_key,
             branch_name, party_name,
             gross_amount, net_amount, cost_amount, tax_amount, discount_amount,
             margin_pct, quantity, txn_count, source_upload_id,
             business_key_hash, row_hash, load_batch_id, created_at, updated_at)
        VALUES
            (:tenant_id, :customer_code, :domain, :txn_type, :dk, :ek, :pk,
             :branch_name, :party_name,
             :gross, :net, :cost, :tax, :disc, :margin, :qty, :cnt, :uid,
             :business_key_hash, :row_hash, :batch, :now, :now)
    """), {**rec, "now": _NOW()})
    stats["inserted"] += 1


# ─────────────────────────────────────────────────────────────────────────────
# main load
# ─────────────────────────────────────────────────────────────────────────────
def load_tenant_to_warehouse(engine, tenant_id, domain: str = "generic",
                             customer_code: Optional[str] = None) -> dict:
    """
    Transform this tenant's flat `sales` + `purchases` rows into the star schema.
    Idempotent: re-running with unchanged source produces zero inserts/updates.
    Returns per-run statistics.
    """
    init_warehouse(engine)
    if customer_code is None:
        customer_code = f"CUST{tenant_id}" if tenant_id is not None else "CUST0"
    batch = _hash(tenant_id, _NOW())[:12]
    stats = {"inserted": 0, "updated": 0, "unchanged": 0, "sales_rows": 0, "purchase_rows": 0}

    # scope by tenant (None → the seeded MedStar pharmacy rows)
    if tenant_id is not None:
        s_sql = "SELECT * FROM sales WHERE tenant_id=:t"
        p_sql = "SELECT * FROM purchases WHERE tenant_id=:t"
        params = {"t": tenant_id}
    else:
        s_sql = "SELECT * FROM sales WHERE tenant_id IS NULL"
        p_sql = "SELECT * FROM purchases WHERE tenant_id IS NULL"
        params = {}

    try:
        sales = pd.read_sql_query(text(s_sql), engine, params=params)
    except Exception:
        sales = pd.DataFrame()
    try:
        purch = pd.read_sql_query(text(p_sql), engine, params=params)
    except Exception:
        purch = pd.DataFrame()

    # occurrence counter: makes the business key unique even when several source
    # rows share the same dimensional coordinate (e.g. many transactions on the
    # same day/branch). Deterministic per unchanged source → idempotent re-runs.
    _occ: dict[str, int] = {}
    def _bk(*coord) -> str:
        base = _hash(*coord)
        _occ[base] = _occ.get(base, 0) + 1
        return _hash(base, _occ[base])

    seen_keys: list[str] = []

    with engine.begin() as conn:
        _upsert_tenant(conn, tenant_id, customer_code, domain)

        # ---- SALES ----
        for _, r in sales.iterrows():
            dk = _date_key(r.get("bill_date"))
            if dk is None:
                continue
            _upsert_date(conn, dk)
            branch_name = r.get("branch")
            party_name  = r.get("supplier_name")     # client/customer for generic tenants
            # primary grouping entity: client when present (generic), else branch (pharmacy)
            entity_name = party_name or branch_name or "(all)"
            entity_type = "customer" if party_name else "branch"
            ek = _scd2_entity(conn, tenant_id, entity_type, entity_name, branch_name)
            net   = _num(r.get("net_amount"))
            cost  = _num(r.get("cost_of_sales"))
            disc  = _num(r.get("discount"))
            cnt   = _num(r.get("total_bills"), 1)
            marg  = _num(r.get("margin_pct"))
            # coordinate includes BOTH branch and client so distinct rows never collide
            bk = _bk(tenant_id, "sale", branch_name, party_name, dk)
            rh = _hash(net, cost, disc, cnt, marg)                # measures
            seen_keys.append(bk)
            rec = {
                "tenant_id": tenant_id, "customer_code": customer_code, "domain": domain,
                "txn_type": "sale", "dk": dk, "ek": ek, "pk": None,
                "branch_name": branch_name, "party_name": party_name,
                "gross": net + disc, "net": net, "cost": cost, "tax": 0.0,
                "disc": disc, "margin": marg, "qty": 0.0, "cnt": cnt,
                "uid": int(r["upload_id"]) if pd.notna(r.get("upload_id")) else None,
                "business_key_hash": bk, "row_hash": rh, "batch": batch,
            }
            _upsert_fact(conn, rec, stats)
            stats["sales_rows"] += 1

        # ---- PURCHASES ----
        for _, r in purch.iterrows():
            dk = _date_key(r.get("grn_date") or r.get("invoice_date"))
            if dk is None:
                continue
            _upsert_date(conn, dk)
            supplier = r.get("supplier_name") or "(unknown supplier)"
            ek = _scd2_entity(conn, tenant_id, "supplier", supplier, r.get("supplier_code"))
            net   = _num(r.get("net_amount"))
            gross = _num(r.get("gross_amount"), net)
            tax   = _num(r.get("total_gst"))
            disc  = _num(r.get("adjustment_value"))
            grn   = r.get("grn_number") or r.get("invoice_number") or ""
            bk = _bk(tenant_id, "purchase", r.get("supplier_code") or supplier, grn, dk)
            rh = _hash(gross, net, tax, disc)
            seen_keys.append(bk)
            rec = {
                "tenant_id": tenant_id, "customer_code": customer_code, "domain": domain,
                "txn_type": "purchase", "dk": dk, "ek": ek, "pk": None,
                "branch_name": None, "party_name": supplier,
                "gross": gross, "net": net, "cost": net, "tax": tax,
                "disc": disc, "margin": 0.0, "qty": 0.0, "cnt": 1.0,
                "uid": int(r["upload_id"]) if pd.notna(r.get("upload_id")) else None,
                "business_key_hash": bk, "row_hash": rh, "batch": batch,
            }
            _upsert_fact(conn, rec, stats)
            stats["purchase_rows"] += 1

        # ---- reconcile: remove warehouse facts whose source row no longer exists ----
        stats["deleted"] = _reconcile_deletes(conn, tenant_id, seen_keys)

    # rebuild the serving mart for this tenant
    stats["mart_rows"] = build_serving_mart(engine, tenant_id)
    return stats


def _reconcile_deletes(conn, tenant_id, seen_keys: list[str]) -> int:
    """Delete facts for this tenant whose business key is absent from the current source."""
    where_t = "tenant_id=:t" if tenant_id is not None else "tenant_id IS NULL"
    params = {"t": tenant_id} if tenant_id is not None else {}
    existing = conn.execute(
        text(f"SELECT business_key_hash FROM wh_fct_transaction WHERE {where_t}"), params
    ).fetchall()
    seen = set(seen_keys)
    stale = [row[0] for row in existing if row[0] not in seen]
    for bk in stale:
        conn.execute(text("DELETE FROM wh_fct_transaction WHERE business_key_hash=:bk"), {"bk": bk})
    return len(stale)


# ─────────────────────────────────────────────────────────────────────────────
# serving mart
# ─────────────────────────────────────────────────────────────────────────────
def build_serving_mart(engine, tenant_id) -> int:
    """Materialize a flattened per-tenant serving mart from the star schema."""
    init_warehouse(engine)
    where_t = "f.tenant_id = :t" if tenant_id is not None else "f.tenant_id IS NULL"
    params = {"t": tenant_id} if tenant_id is not None else {}
    with engine.begin() as conn:
        if tenant_id is not None:
            conn.execute(text("DELETE FROM wh_mart_transaction WHERE tenant_id=:t"), {"t": tenant_id})
        else:
            conn.execute(text("DELETE FROM wh_mart_transaction WHERE tenant_id IS NULL"))
        conn.execute(text(f"""
            INSERT INTO wh_mart_transaction
                (tenant_id, domain, txn_type, date_key, full_date, year, month, month_name,
                 entity_type, entity_name, branch_name, party_name, product_name,
                 gross_amount, net_amount, cost_amount, tax_amount, margin_pct, txn_count)
            SELECT f.tenant_id, f.domain, f.txn_type, f.date_key,
                   d.full_date, d.year, d.month, d.month_name,
                   e.entity_type, e.entity_name, f.branch_name, f.party_name, p.product_name,
                   f.gross_amount, f.net_amount, f.cost_amount, f.tax_amount, f.margin_pct, f.txn_count
            FROM wh_fct_transaction f
            LEFT JOIN wh_dim_date    d ON f.date_key   = d.date_key
            LEFT JOIN wh_dim_entity  e ON f.entity_key = e.entity_key
            LEFT JOIN wh_dim_product p ON f.product_key = p.product_key
            WHERE {where_t}
        """), params)
        n = conn.execute(text(
            f"SELECT COUNT(*) FROM wh_mart_transaction WHERE {where_t.replace('f.', '')}"
        ), params).scalar()
    return int(n or 0)


def read_mart(engine, tenant_id, txn_type: Optional[str] = None) -> pd.DataFrame:
    """Read the serving mart for analytics/AI consumption."""
    sql = "SELECT * FROM wh_mart_transaction WHERE " + (
        "tenant_id = :t" if tenant_id is not None else "tenant_id IS NULL")
    params = {"t": tenant_id} if tenant_id is not None else {}
    if txn_type:
        sql += " AND txn_type = :tt"
        params["tt"] = txn_type
    try:
        return pd.read_sql_query(text(sql), engine, params=params)
    except Exception:
        return pd.DataFrame()


def load_tenant_df_from_mart(engine, tenant_id):
    """
    Return (sales_df, purchases_df) reconstructed from the serving mart, shaped to
    match the exact column contract that tenant_analytics renderers expect:
        sales:     net_amount, bill_date, branch, supplier_name, margin_pct
        purchases: net_amount, bill_date, supplier_name
    This is the mart-backed drop-in replacement for load_tenant_df().
    """
    m = read_mart(engine, tenant_id)
    if m.empty:
        return pd.DataFrame(), pd.DataFrame()
    m["bill_date"] = pd.to_datetime(m["full_date"], errors="coerce")
    m["net_amount"] = pd.to_numeric(m["net_amount"], errors="coerce").fillna(0)

    sale = m[m["txn_type"] == "sale"].copy()
    if not sale.empty:
        sale["branch"]        = sale["branch_name"]
        sale["supplier_name"] = sale["party_name"]
        sale = sale[["net_amount", "bill_date", "branch", "supplier_name", "margin_pct",
                     "gross_amount", "cost_amount", "txn_count"]]

    pur = m[m["txn_type"] == "purchase"].copy()
    if not pur.empty:
        # for purchases the party is the supplier
        pur["supplier_name"] = pur["party_name"].fillna(pur["entity_name"])
        pur["branch"]        = pur["branch_name"]
        pur = pur[["net_amount", "bill_date", "supplier_name", "branch",
                   "gross_amount", "cost_amount", "tax_amount", "txn_count"]]
    return sale, pur


def warehouse_kpis(engine, tenant_id) -> dict:
    """Headline KPIs computed from the serving mart (for analytics/AI adoption)."""
    m = read_mart(engine, tenant_id)
    if m.empty:
        return {"sales": 0.0, "purchases": 0.0, "margin": 0.0, "txns": 0,
                "top_entity": "—", "currency": "USD"}
    sale = m[m["txn_type"] == "sale"]
    pur  = m[m["txn_type"] == "purchase"]
    sales_v = float(sale["net_amount"].sum()) if not sale.empty else 0.0
    pur_v   = float(pur["net_amount"].sum())  if not pur.empty  else 0.0
    margin  = float(sale["margin_pct"].mean()) if not sale.empty and sale["margin_pct"].notna().any() else 0.0
    txns    = int(sale["txn_count"].sum()) if not sale.empty else 0
    top_entity = "—"
    if not sale.empty:
        try:
            top_entity = sale.groupby("entity_name")["net_amount"].sum().idxmax()
        except Exception:
            pass
    return {"sales": sales_v, "purchases": pur_v, "margin": margin,
            "txns": txns, "top_entity": top_entity}


def health_check(engine, tenant_id) -> dict:
    """Compare warehouse fact totals to the flat tables — a data-quality gate."""
    tc = "tenant_id = :t" if tenant_id is not None else "tenant_id IS NULL"
    params = {"t": tenant_id} if tenant_id is not None else {}
    out = {}
    for tt, tbl in (("sale", "sales"), ("purchase", "purchases")):
        try:
            flat = pd.read_sql_query(text(f"SELECT COALESCE(SUM(net_amount),0) v, COUNT(*) n FROM {tbl} WHERE {tc}"), engine, params=params)
            whf  = pd.read_sql_query(text(f"SELECT COALESCE(SUM(net_amount),0) v, COUNT(*) n FROM wh_fct_transaction WHERE txn_type='{tt}' AND {tc}"), engine, params=params)
            out[tt] = {"flat_sum": float(flat.v[0]), "wh_sum": float(whf.v[0]),
                       "flat_rows": int(flat.n[0]), "wh_rows": int(whf.n[0]),
                       "match": abs(float(flat.v[0]) - float(whf.v[0])) < 0.01 and int(flat.n[0]) == int(whf.n[0])}
        except Exception as e:
            out[tt] = {"error": str(e)}
    return out


def backfill_all(engine, tenant_domains: Optional[dict] = None) -> dict:
    """
    Load every tenant present in the flat tables into the warehouse.
    tenant_domains: optional {tenant_id: domain} map; defaults to 'generic'
    (and 'pharmacy' for the seeded NULL-tenant MedStar rows).
    """
    init_warehouse(engine)
    tenant_domains = tenant_domains or {}
    ids = set()
    for tbl in ("sales", "purchases"):
        try:
            df = pd.read_sql_query(text(f"SELECT DISTINCT tenant_id FROM {tbl}"), engine)
            ids.update(df["tenant_id"].tolist())
        except Exception:
            pass
    results = {}
    for tid in ids:
        tid_val = None if (tid is None or pd.isna(tid)) else int(tid)
        domain = tenant_domains.get(tid_val, "pharmacy" if tid_val is None else "generic")
        results[str(tid_val)] = load_tenant_to_warehouse(engine, tid_val, domain)
    return results


if __name__ == "__main__":
    # Manual backfill / smoke test against the local SQLite DB
    from data_loader import _get_sqlite_engine
    eng = _get_sqlite_engine()
    init_warehouse(eng)
    print("Backfilling all tenants from flat tables...")
    res = backfill_all(eng)
    for tid, st in res.items():
        print(f"  tenant={tid}: {st}")
