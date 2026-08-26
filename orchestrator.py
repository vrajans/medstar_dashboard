"""
orchestrator.py  —  InsightHub Pipeline Orchestrator (Phase 1)
==============================================================
A lightweight, dependency-light orchestration layer that runs the data pipeline
as ordered, observable, retryable steps — event-driven on upload and on a
schedule — with full run history for observability (the "pipeline run history"
and "data-freshness SLA" the architecture calls for).

Consistent with the BRD's SMB-right-sized principle: start lightweight and
in-process; graduate to Dagster (see ./orchestration) when many connectors and a
full orchestration UI are warranted — the step functions here are reused verbatim
as Dagster assets, so graduating is wrapping, not rewriting.

Pipeline (per tenant):
    validate  →  transform_load  →  verify
      │              │                 │
   source rows   warehouse load     parity gate
   present?      (inline or dbt)    (flat == warehouse)

Public API
----------
    run_pipeline(engine, tenant_id, domain="generic", trigger="upload") -> dict
    run_all(engine, trigger="schedule") -> list[dict]
    get_recent_runs(engine, tenant_id=None, limit=20) -> DataFrame
    init_orchestrator_tables(engine)
"""

from __future__ import annotations

import os
import time
import logging
import traceback
from datetime import datetime
from typing import Callable, Optional

import pandas as pd
from sqlalchemy import text

logger = logging.getLogger(__name__)

RETRY_ATTEMPTS = int(os.getenv("ORCHESTRATOR_RETRIES", "2"))
RETRY_BACKOFF  = float(os.getenv("ORCHESTRATOR_BACKOFF", "0.5"))
TRANSFORM_ENGINE = os.getenv("ORCHESTRATOR_TRANSFORM", "inline")  # "inline" | "dbt"


# ─────────────────────────────────────────────────────────────────────────────
# schema (run history)
# ─────────────────────────────────────────────────────────────────────────────
def init_orchestrator_tables(engine) -> None:
    pk = "INTEGER PRIMARY KEY AUTOINCREMENT" if engine.dialect.name == "sqlite" else "SERIAL PRIMARY KEY"
    with engine.begin() as c:
        c.execute(text(f"""
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                run_id         {pk},
                tenant_id      INTEGER,
                domain         TEXT,
                trigger        TEXT,
                engine         TEXT,
                status         TEXT,
                started_at     TEXT,
                finished_at    TEXT,
                duration_ms    INTEGER,
                rows_processed INTEGER,
                error          TEXT
            )
        """))
        c.execute(text(f"""
            CREATE TABLE IF NOT EXISTS pipeline_step_runs (
                id          {pk},
                run_id      INTEGER,
                step_name   TEXT,
                status      TEXT,
                started_at  TEXT,
                finished_at TEXT,
                duration_ms INTEGER,
                detail      TEXT,
                error       TEXT
            )
        """))


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _run_with_retry(fn: Callable, *args, attempts: int = RETRY_ATTEMPTS, **kwargs):
    last = None
    for i in range(1, attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            last = e
            logger.warning("[orchestrator] attempt %d/%d failed: %s", i, attempts, e)
            if i < attempts:
                time.sleep(RETRY_BACKOFF * i)
    raise last


# ─────────────────────────────────────────────────────────────────────────────
# pipeline steps  (also reused as Dagster assets — see ./orchestration)
# ─────────────────────────────────────────────────────────────────────────────
def step_validate(engine, tenant_id, domain) -> dict:
    """Confirm the tenant has source rows to process."""
    tc = "tenant_id = :t" if tenant_id is not None else "tenant_id IS NULL"
    params = {"t": tenant_id} if tenant_id is not None else {}
    counts = {}
    for tbl in ("sales", "purchases"):
        try:
            counts[tbl] = int(pd.read_sql_query(
                text(f"SELECT COUNT(*) n FROM {tbl} WHERE {tc}"), engine, params=params).n[0])
        except Exception:
            counts[tbl] = 0
    total = counts.get("sales", 0) + counts.get("purchases", 0)
    if total == 0:
        raise ValueError(f"no source rows for tenant {tenant_id}")
    return {"detail": f"sales={counts['sales']} purchases={counts['purchases']}", "rows": total}


def step_transform_load(engine, tenant_id, domain) -> dict:
    """Run the transformation into the star-schema warehouse + serving marts."""
    if TRANSFORM_ENGINE == "dbt":
        import subprocess
        proj = os.path.join(os.path.dirname(__file__), "warehouse_dbt")
        rc = subprocess.call(["dbt", "build", "--profiles-dir", proj, "--project-dir", proj])
        if rc != 0:
            raise RuntimeError(f"dbt build failed (exit {rc})")
        return {"detail": "dbt build ok"}
    # default: inline in-process loader
    import warehouse as wh
    stats = wh.load_tenant_to_warehouse(engine, tenant_id, domain)
    return {"detail": f"inserted={stats['inserted']} updated={stats['updated']} "
                      f"deleted={stats['deleted']} mart={stats['mart_rows']}",
            "rows": stats.get("mart_rows", 0)}


def step_verify(engine, tenant_id, domain) -> dict:
    """Data-quality gate: warehouse fact totals must match the flat source."""
    import warehouse as wh
    h = wh.health_check(engine, tenant_id)
    bad = [tt for tt, r in h.items() if isinstance(r, dict) and not r.get("match", False)]
    if bad:
        raise ValueError(f"parity check failed for: {', '.join(bad)} — {h}")
    return {"detail": "parity ok (" + ", ".join(
        f"{tt}:{r['wh_rows']}rows" for tt, r in h.items() if isinstance(r, dict)) + ")"}


PIPELINE = [
    ("validate",        step_validate),
    ("transform_load",  step_transform_load),
    ("verify",          step_verify),
]


# ─────────────────────────────────────────────────────────────────────────────
# run
# ─────────────────────────────────────────────────────────────────────────────
def run_pipeline(engine, tenant_id, domain: str = "generic", trigger: str = "upload") -> dict:
    """
    Execute the pipeline for one tenant with per-step tracking + retries.
    Never raises — records failure to run history and returns a summary.
    """
    init_orchestrator_tables(engine)
    t0 = time.time()
    started = _now()
    with engine.begin() as c:
        c.execute(text("""
            INSERT INTO pipeline_runs
                (tenant_id, domain, trigger, engine, status, started_at, rows_processed)
            VALUES (:t, :d, :trg, :eng, 'running', :st, 0)
        """), {"t": tenant_id, "d": domain, "trg": trigger,
               "eng": TRANSFORM_ENGINE, "st": started})
        run_id = c.execute(text(
            "SELECT run_id FROM pipeline_runs ORDER BY run_id DESC LIMIT 1")).scalar()

    rows_total = 0
    try:
        for name, fn in PIPELINE:
            s0 = time.time()
            s_start = _now()
            try:
                out = _run_with_retry(fn, engine, tenant_id, domain) or {}
                rows_total = max(rows_total, int(out.get("rows", 0) or 0))
                _record_step(engine, run_id, name, "success", s_start,
                             int((time.time() - s0) * 1000), out.get("detail", ""), None)
            except Exception as se:  # step failed after retries
                _record_step(engine, run_id, name, "failed", s_start,
                             int((time.time() - s0) * 1000), None, str(se))
                raise
        # success
        dur = int((time.time() - t0) * 1000)
        with engine.begin() as c:
            c.execute(text("""UPDATE pipeline_runs SET status='success', finished_at=:f,
                              duration_ms=:d, rows_processed=:r WHERE run_id=:id"""),
                      {"f": _now(), "d": dur, "r": rows_total, "id": run_id})
        logger.info("[orchestrator] run %s SUCCESS tenant=%s rows=%s %dms",
                    run_id, tenant_id, rows_total, dur)
        return {"run_id": run_id, "status": "success", "rows": rows_total, "duration_ms": dur}
    except Exception as e:  # noqa: BLE001
        dur = int((time.time() - t0) * 1000)
        with engine.begin() as c:
            c.execute(text("""UPDATE pipeline_runs SET status='failed', finished_at=:f,
                              duration_ms=:d, error=:e WHERE run_id=:id"""),
                      {"f": _now(), "d": dur, "e": str(e)[:500], "id": run_id})
        logger.error("[orchestrator] run %s FAILED tenant=%s: %s", run_id, tenant_id, e)
        logger.debug(traceback.format_exc())
        return {"run_id": run_id, "status": "failed", "error": str(e), "duration_ms": dur}


def _record_step(engine, run_id, name, status, started, dur_ms, detail, error) -> None:
    with engine.begin() as c:
        c.execute(text("""
            INSERT INTO pipeline_step_runs
                (run_id, step_name, status, started_at, finished_at, duration_ms, detail, error)
            VALUES (:r, :n, :s, :st, :fin, :d, :det, :err)
        """), {"r": run_id, "n": name, "s": status, "st": started, "fin": _now(),
               "d": dur_ms, "det": detail, "err": error})


def run_all(engine, trigger: str = "schedule", tenant_domains: Optional[dict] = None) -> list[dict]:
    """Run the pipeline for every tenant present in the flat tables (scheduled sweep)."""
    init_orchestrator_tables(engine)
    tenant_domains = tenant_domains or {}
    ids: set = set()
    for tbl in ("sales", "purchases"):
        try:
            df = pd.read_sql_query(text(f"SELECT DISTINCT tenant_id FROM {tbl}"), engine)
            ids.update(df["tenant_id"].tolist())
        except Exception:
            pass
    results = []
    for tid in ids:
        tid_val = None if (tid is None or pd.isna(tid)) else int(tid)
        dom = tenant_domains.get(tid_val, "pharmacy" if tid_val is None else "generic")
        results.append(run_pipeline(engine, tid_val, dom, trigger=trigger))
    return results


def get_recent_runs(engine, tenant_id=None, limit: int = 20) -> pd.DataFrame:
    """Recent pipeline runs for observability (freshness, success rate, timings)."""
    try:
        init_orchestrator_tables(engine)
        if tenant_id is not None:
            sql = ("SELECT * FROM pipeline_runs WHERE tenant_id = :t "
                   "ORDER BY run_id DESC LIMIT :lim")
            return pd.read_sql_query(text(sql), engine, params={"t": tenant_id, "lim": limit})
        return pd.read_sql_query(text(
            "SELECT * FROM pipeline_runs ORDER BY run_id DESC LIMIT :lim"),
            engine, params={"lim": limit})
    except Exception:
        return pd.DataFrame()


def get_run_steps(engine, run_id: int) -> pd.DataFrame:
    try:
        return pd.read_sql_query(text(
            "SELECT step_name, status, duration_ms, detail, error "
            "FROM pipeline_step_runs WHERE run_id = :r ORDER BY id"),
            engine, params={"r": run_id})
    except Exception:
        return pd.DataFrame()


if __name__ == "__main__":
    from data_loader import _get_sqlite_engine
    eng = _get_sqlite_engine()
    print("Running pipeline for all tenants...")
    for r in run_all(eng, trigger="manual"):
        print(" ", r)
