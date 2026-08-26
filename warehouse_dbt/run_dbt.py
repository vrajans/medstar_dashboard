"""
run_dbt.py — production invocation shim.

The orchestrator (or the Flask app's post-upload hook) calls this to run the dbt
transformation after new data lands, instead of the inline warehouse.py loader.

Usage:
    python run_dbt.py                 # dbt build against the prod (Postgres) target
    python run_dbt.py --target dev    # against local DuckDB
    python run_dbt.py --select fct_transaction+   # a subset of the DAG

Returns process exit code from dbt (0 = success), so a scheduler can gate on it.
"""
import subprocess, sys, os

def run_dbt(args=None) -> int:
    args = args or []
    target = os.getenv("DBT_TARGET", "prod")
    if "--target" not in args:
        args += ["--target", target]
    cmd = ["dbt", "build", "--profiles-dir", os.path.dirname(__file__) or ".",
           "--project-dir", os.path.dirname(__file__) or "."] + args
    print("[run_dbt]", " ".join(cmd))
    return subprocess.call(cmd)

if __name__ == "__main__":
    raise SystemExit(run_dbt(sys.argv[1:]))
