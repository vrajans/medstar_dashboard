"""
test_warehouse.py — correctness checks for the dimensional warehouse loader.
Run:  python test_warehouse.py
Uses an in-memory-style temp SQLite DB seeded with synthetic rows so the
checks are deterministic and independent of the live medstar.db.
"""
import os, tempfile, warnings
warnings.filterwarnings("ignore")
import pandas as pd
from sqlalchemy import create_engine, text
import warehouse as wh

FAILS = []
def check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        FAILS.append(name)

def seed(engine):
    """Two branches, multiple rows per (branch, day) to exercise the occurrence key."""
    sales = pd.DataFrame([
        # branch, month_label, bill_date, net_amount, cost_of_sales, discount, total_bills, margin_pct, upload_id, tenant_id
        ("North","Jan","2025-01-01",1000, 700, 10, 5, 30.0, 1, 7),
        ("North","Jan","2025-01-01", 500, 350,  5, 3, 30.0, 1, 7),  # same branch+day → occurrence 2
        ("South","Jan","2025-01-01", 800, 560,  8, 4, 30.0, 1, 7),
        ("North","Jan","2025-01-02", 900, 630,  9, 5, 30.0, 1, 7),
    ], columns=["branch","month_label","bill_date","net_amount","cost_of_sales","discount","total_bills","margin_pct","upload_id","tenant_id"])
    purch = pd.DataFrame([
        ("SUP1","Acme","2025-01-01","GRN1",1200,1200,0,60,7),
        ("SUP2","Beta","2025-01-02","GRN2", 700, 700,0,35,7),
    ], columns=["supplier_code","supplier_name","grn_date","grn_number","gross_amount","net_amount","adjustment_value","total_gst","tenant_id"])
    sales.to_sql("sales", engine, if_exists="replace", index=False)
    purch.to_sql("purchases", engine, if_exists="replace", index=False)

def sum_fact(engine, tt):
    return pd.read_sql_query(text("SELECT COALESCE(SUM(net_amount),0) v, COUNT(*) n FROM wh_fct_transaction WHERE txn_type=:t AND tenant_id=7"),
                             engine, params={"t": tt})

def main():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    eng = create_engine(f"sqlite:///{path}")
    try:
        seed(eng)

        print("1) Initial load")
        s1 = wh.load_tenant_to_warehouse(eng, 7, "generic")
        check("4 sales facts inserted", s1["inserted"] == 6 and s1["sales_rows"] == 4)  # 4 sales + 2 purch = 6
        check("sales sum parity 3200", abs(sum_fact(eng,"sale").v[0] - 3200) < 0.01)
        check("sales row-count parity 4", int(sum_fact(eng,"sale").n[0]) == 4)
        check("purchase sum parity 1900", abs(sum_fact(eng,"purchase").v[0] - 1900) < 0.01)
        check("occurrence key kept both same-day rows", int(sum_fact(eng,"sale").n[0]) == 4)

        print("2) Idempotent re-run (no source change)")
        s2 = wh.load_tenant_to_warehouse(eng, 7, "generic")
        check("0 inserted", s2["inserted"] == 0)
        check("0 updated", s2["updated"] == 0)
        check("0 deleted", s2["deleted"] == 0)
        check("all unchanged", s2["unchanged"] == 6)

        print("3) Change a measure → exactly 1 update")
        with eng.begin() as c:
            c.execute(text("UPDATE sales SET net_amount=1111 WHERE branch='North' AND bill_date='2025-01-02'"))
        s3 = wh.load_tenant_to_warehouse(eng, 7, "generic")
        check("1 updated", s3["updated"] == 1)
        check("0 inserted", s3["inserted"] == 0)
        check("new sum reflects change", abs(sum_fact(eng,"sale").v[0] - (3200-900+1111)) < 0.01)

        print("4) Delete a source row → reconcile removes 1 fact")
        with eng.begin() as c:
            c.execute(text("DELETE FROM sales WHERE branch='South'"))
        s4 = wh.load_tenant_to_warehouse(eng, 7, "generic")
        check("1 deleted", s4["deleted"] == 1)
        check("row count now 3", int(sum_fact(eng,"sale").n[0]) == 3)

        print("5) SCD2 — change an entity attribute (supplier_code) → new version, history kept")
        with eng.begin() as c:
            c.execute(text("UPDATE purchases SET supplier_code='SUP1X' WHERE supplier_name='Acme'"))
        wh.load_tenant_to_warehouse(eng, 7, "generic")
        vers = pd.read_sql_query(text("SELECT COUNT(*) n FROM wh_dim_entity WHERE tenant_id=7 AND entity_name='Acme'"), eng)
        cur = pd.read_sql_query(text("SELECT COUNT(*) n FROM wh_dim_entity WHERE tenant_id=7 AND entity_name='Acme' AND is_current=1"), eng)
        check("2 entity versions exist (SCD2 history)", int(vers.n[0]) == 2)
        check("exactly 1 current version", int(cur.n[0]) == 1)

        print("6) Serving mart parity")
        mart = wh.read_mart(eng, 7)
        fct = pd.read_sql_query(text("SELECT COUNT(*) n FROM wh_fct_transaction WHERE tenant_id=7"), eng)
        check("mart row count == fact row count", len(mart) == int(fct.n[0]))
        check("mart has flattened dims", set(["full_date","entity_name","month_name"]).issubset(mart.columns))

        print(f"\n{'ALL PASS ✅' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
        return 0 if not FAILS else 1
    finally:
        eng.dispose(); os.unlink(path)

if __name__ == "__main__":
    raise SystemExit(main())
