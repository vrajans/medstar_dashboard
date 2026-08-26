"""
test_orchestrator.py — pipeline orchestration checks.
Run: python test_orchestrator.py
"""
import os, tempfile, warnings
warnings.filterwarnings("ignore")
import pandas as pd
from sqlalchemy import create_engine, text
import orchestrator as orch

FAILS = []
def check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond: FAILS.append(name)

def seed(engine):
    sales = pd.DataFrame([
        ("North","Jan","2025-01-01",1000,700,10,5,30.0,1,7),
        ("North","Jan","2025-01-02", 900,630, 9,5,30.0,1,7),
        ("South","Jan","2025-01-01", 800,560, 8,4,30.0,1,7),
    ], columns=["branch","month_label","bill_date","net_amount","cost_of_sales","discount","total_bills","margin_pct","upload_id","tenant_id"])
    purch = pd.DataFrame([
        ("SUP1","Acme","2025-01-01","GRN1",1200,1200,0,60,7),
    ], columns=["supplier_code","supplier_name","grn_date","grn_number","gross_amount","net_amount","adjustment_value","total_gst","tenant_id"])
    sales.to_sql("sales", engine, if_exists="replace", index=False)
    purch.to_sql("purchases", engine, if_exists="replace", index=False)

def main():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    eng = create_engine(f"sqlite:///{path}")
    try:
        seed(eng)

        print("1) Successful run records history + steps")
        r = orch.run_pipeline(eng, 7, "generic", trigger="upload")
        check("status success", r["status"] == "success")
        check("rows processed > 0", r["rows"] > 0)
        runs = orch.get_recent_runs(eng, 7)
        check("1 run recorded", len(runs) == 1)
        check("run marked success", runs.iloc[0]["status"] == "success")
        check("duration recorded", int(runs.iloc[0]["duration_ms"]) >= 0)
        steps = orch.get_run_steps(eng, int(runs.iloc[0]["run_id"]))
        check("3 steps recorded", len(steps) == 3)
        check("all steps success", (steps["status"] == "success").all())
        check("verify step present", "verify" in set(steps["step_name"]))

        print("2) Idempotent re-run also succeeds + verify gate passes")
        r2 = orch.run_pipeline(eng, 7, "generic", trigger="upload")
        check("second run success", r2["status"] == "success")
        check("now 2 runs in history", len(orch.get_recent_runs(eng, 7)) == 2)

        print("3) Validation failure (no data) is recorded as failed, not raised")
        r3 = orch.run_pipeline(eng, 999, "generic", trigger="upload")   # tenant with no rows
        check("empty tenant → failed status", r3["status"] == "failed")
        check("error mentions no source rows", "no source rows" in (r3.get("error") or ""))
        frun = orch.get_recent_runs(eng, 999)
        check("failed run recorded", len(frun) == 1 and frun.iloc[0]["status"] == "failed")
        fsteps = orch.get_run_steps(eng, int(frun.iloc[0]["run_id"]))
        check("validate step marked failed", (fsteps[fsteps.step_name=="validate"]["status"]=="failed").all())
        check("pipeline stopped after failed step (only 1 step)", len(fsteps) == 1)

        print("4) Retry wrapper retries then succeeds")
        calls = {"n": 0}
        def flaky():
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("transient")
            return "ok"
        out = orch._run_with_retry(flaky, attempts=3)
        check("retry eventually succeeds", out == "ok" and calls["n"] == 2)

        print("5) run_all sweeps every tenant")
        res = orch.run_all(eng, trigger="schedule")
        check("run_all returns a result per tenant", len(res) >= 1)

        print(f"\n{'ALL PASS ✅' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
        return 0 if not FAILS else 1
    finally:
        eng.dispose(); os.unlink(path)

if __name__ == "__main__":
    raise SystemExit(main())
