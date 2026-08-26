"""
test_end_to_end.py — exercises the COMPLETE data flow in-process, one command:

    upload (data_loader.append_upload_to_db)
      → orchestrator.run_pipeline  (validate → transform/load → verify)
        → warehouse star schema + serving marts
          → analytics overview SQL (same query the FastAPI /analytics/overview uses)
            → re-upload idempotency

Run:  python test_end_to_end.py
Uses a throwaway temp DB — never touches medstar.db.
"""
import io, os, tempfile, warnings
warnings.filterwarnings("ignore")
import pandas as pd
from sqlalchemy import create_engine, text

import data_loader, orchestrator as orch, warehouse as wh

FAILS = []
def check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond: FAILS.append(name)

TID = 55

def precreate_tables(eng):
    """Create upload_history + empty sales/purchases with the expected columns."""
    with eng.begin() as c:
        c.execute(text("""
            CREATE TABLE IF NOT EXISTS upload_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT, report_type TEXT,
                branch TEXT, month_label TEXT, row_count INTEGER, uploaded_at TEXT,
                duplicate_warning INTEGER DEFAULT 0, status TEXT DEFAULT 'active',
                source TEXT DEFAULT 'manual', tenant_id INTEGER DEFAULT NULL)
        """))
    pd.DataFrame(columns=["branch","month_label","bill_date","net_amount","cost_of_sales",
                          "discount","total_bills","margin_pct","supplier_name","upload_id","tenant_id"]
                 ).to_sql("sales", eng, if_exists="replace", index=False)
    pd.DataFrame(columns=["branch","month_label","supplier_code","supplier_name","gross_amount",
                          "net_amount","adjustment_value","total_gst","grn_number","grn_date",
                          "upload_id","tenant_id"]
                 ).to_sql("purchases", eng, if_exists="replace", index=False)

def make_sales(extra=0.0):
    return pd.DataFrame([
        ("Americas","Jan","2025-01-05", 1000+extra, 700, 10, 5, 30.0, "Acme Corp"),
        ("Americas","Jan","2025-01-05",  500,        350,  5, 3, 30.0, "Beta LLC"),
        ("Americas","Feb","2025-02-08",  800,        560,  8, 4, 30.0, "Acme Corp"),
        ("Americas","Mar","2025-03-10",  900,        630,  9, 5, 30.0, "Gamma Inc"),
    ], columns=["branch","month_label","bill_date","net_amount","cost_of_sales",
                "discount","total_bills","margin_pct","supplier_name"])

def upload(eng, df, report_type="generic_sales", month="Q1"):
    store = {"df_json": df.to_json(), "report_type": report_type,
             "branch": "Americas", "month_label": month, "filename": "test_upload.xlsx"}
    return data_loader.append_upload_to_db(store, eng, tenant_id=TID)

OVERVIEW_SQL = """
    SELECT COALESCE(SUM(CASE WHEN txn_type='sale' THEN net_amount END),0) revenue,
           COALESCE(SUM(CASE WHEN txn_type='sale' THEN txn_count  END),0) txns,
           COUNT(DISTINCT CASE WHEN txn_type='sale' THEN party_name END) customers
    FROM wh_mart_transaction WHERE tenant_id = :t
"""

def main():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    eng = create_engine(f"sqlite:///{path}")
    try:
        precreate_tables(eng)

        print("STEP 1 — Upload (ingestion path)")
        rows, dup, err = upload(eng, make_sales())
        check("upload succeeded", err is None and rows == 4)
        check("not flagged duplicate", dup is False)
        n = pd.read_sql_query(text("SELECT COUNT(*) n FROM sales WHERE tenant_id=:t"),
                              eng, params={"t": TID}).n[0]
        check("4 sales rows landed in flat table", int(n) == 4)

        print("STEP 2 — Orchestrated pipeline (validate → transform → verify)")
        res = orch.run_pipeline(eng, TID, "generic", trigger="upload")
        check("pipeline status success", res["status"] == "success")
        steps = orch.get_run_steps(eng, res["run_id"])
        check("all 3 steps ran + passed", len(steps) == 3 and (steps["status"] == "success").all())
        check("verify (parity) step passed", "verify" in set(steps["step_name"]))

        print("STEP 3 — Warehouse + serving mart populated")
        h = wh.health_check(eng, TID)
        check("warehouse matches flat source (parity)", h["sale"]["match"])
        ms, mp = wh.load_tenant_df_from_mart(eng, TID)
        check("mart-backed sales frame has 4 rows", len(ms) == 4)
        check("client dimension preserved (3 clients)", ms["supplier_name"].nunique() == 3)

        print("STEP 4 — Analytics API query (same SQL as /analytics/overview)")
        with eng.connect() as c:
            row = c.execute(text(OVERVIEW_SQL), {"t": TID}).mappings().first()
        kpi = wh.warehouse_kpis(eng, TID)
        check("API revenue == warehouse KPI", abs(row["revenue"] - kpi["sales"]) < 0.01)
        check("revenue == 3200", abs(row["revenue"] - 3200) < 0.01)
        check("customers == 3", int(row["customers"]) == 3)

        print("STEP 5 — Re-upload is idempotent (no duplicates)")
        rows2, dup2, err2 = upload(eng, make_sales())     # same data again
        res2 = orch.run_pipeline(eng, TID, "generic", trigger="upload")
        check("second pipeline success", res2["status"] == "success")
        ms2, _ = wh.load_tenant_df_from_mart(eng, TID)
        check("mart still 4 rows (no duplication)", len(ms2) == 4)
        with eng.connect() as c:
            rev2 = c.execute(text(OVERVIEW_SQL), {"t": TID}).mappings().first()["revenue"]
        check("revenue unchanged at 3200", abs(rev2 - 3200) < 0.01)

        print("STEP 6 — Changed re-upload propagates one update")
        upload(eng, make_sales(extra=1000))               # one row +1000
        orch.run_pipeline(eng, TID, "generic", trigger="upload")
        with eng.connect() as c:
            rev3 = c.execute(text(OVERVIEW_SQL), {"t": TID}).mappings().first()["revenue"]
        check("revenue now 4200 (one +1000 change)", abs(rev3 - 4200) < 0.01)
        check("run history has >=3 runs", len(orch.get_recent_runs(eng, TID)) >= 3)

        print(f"\n{'✅ COMPLETE FLOW PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
        return 0 if not FAILS else 1
    finally:
        eng.dispose(); os.unlink(path)

if __name__ == "__main__":
    raise SystemExit(main())
