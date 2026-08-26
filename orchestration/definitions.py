"""
orchestration/definitions.py  —  Dagster graduation path
========================================================
The same pipeline as orchestrator.py, expressed as Dagster assets + a nightly
schedule + an upload sensor, so a team that wants the full orchestration UI
(run history, retries, backfills, lineage, alerting) can adopt it WITHOUT
rewriting logic — the assets call the exact same step functions.

Run locally:
    pip install dagster dagster-webserver
    dagster dev -f orchestration/definitions.py
    # open http://localhost:3000

In production the same file is loaded by the Dagster daemon + webserver services.
"""
from __future__ import annotations
import os
import sys

# make the parent app package importable when Dagster loads this file
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dagster import (  # noqa: E402
    asset, Definitions, define_asset_job,
    ScheduleDefinition, RunRequest, SkipReason, sensor,
    RetryPolicy, MaterializeResult, MetadataValue,
)


def _engine():
    from data_loader import _get_sqlite_engine
    return _get_sqlite_engine()


# ── assets: validate → transform/load → verify (all tenants) ──────────────────
@asset(retry_policy=RetryPolicy(max_retries=2, delay=1))
def raw_sources(context) -> MaterializeResult:
    """Confirm there is source data to process."""
    import pandas as pd
    from sqlalchemy import text
    eng = _engine()
    counts = {}
    for tbl in ("sales", "purchases"):
        counts[tbl] = int(pd.read_sql_query(text(f"SELECT COUNT(*) n FROM {tbl}"), eng).n[0])
    total = sum(counts.values())
    if total == 0:
        raise Exception("no source rows present")
    return MaterializeResult(metadata={"sales": counts["sales"], "purchases": counts["purchases"]})


@asset(deps=[raw_sources], retry_policy=RetryPolicy(max_retries=2, delay=1))
def warehouse(context) -> MaterializeResult:
    """Transform every tenant's data into the star schema + serving marts."""
    import orchestrator as orch
    eng = _engine()
    results = orch.run_all(eng, trigger="dagster")
    ok = sum(1 for r in results if r.get("status") == "success")
    context.log.info(f"warehouse: {ok}/{len(results)} tenants ok")
    if ok == 0 and results:
        raise Exception(f"all tenant loads failed: {results}")
    return MaterializeResult(metadata={
        "tenants_ok": ok, "tenants_total": len(results),
        "detail": MetadataValue.json(results),
    })


@asset(deps=[warehouse])
def marts_verified(context) -> MaterializeResult:
    """Data-quality gate: warehouse must match the flat source per tenant."""
    import warehouse as wh
    import pandas as pd
    from sqlalchemy import text
    eng = _engine()
    ids = set()
    for tbl in ("sales", "purchases"):
        df = pd.read_sql_query(text(f"SELECT DISTINCT tenant_id FROM {tbl}"), eng)
        ids.update(df["tenant_id"].tolist())
    mismatches = []
    for tid in ids:
        tid_val = None if (tid is None or pd.isna(tid)) else int(tid)
        h = wh.health_check(eng, tid_val)
        for tt, r in h.items():
            if isinstance(r, dict) and not r.get("match", True):
                mismatches.append({"tenant": tid_val, "type": tt, **r})
    if mismatches:
        raise Exception(f"parity check failed: {mismatches}")
    return MaterializeResult(metadata={"checked_tenants": len(ids), "mismatches": 0})


# ── job, schedule, sensor ─────────────────────────────────────────────────────
pipeline_job = define_asset_job("insighthub_pipeline", selection="*")

nightly_schedule = ScheduleDefinition(
    job=pipeline_job,
    cron_schedule="30 2 * * *",   # 02:30 daily
    name="nightly_warehouse_rebuild",
)


@sensor(job=pipeline_job, minimum_interval_seconds=60)
def upload_sensor(context):
    """Trigger the pipeline whenever a new upload lands (event-driven ingestion)."""
    import pandas as pd
    from sqlalchemy import text
    eng = _engine()
    try:
        last_id = int(pd.read_sql_query(
            text("SELECT COALESCE(MAX(id),0) m FROM upload_history"), eng).m[0])
    except Exception:
        return SkipReason("upload_history not available")
    cursor = int(context.cursor) if context.cursor else 0
    if last_id > cursor:
        context.update_cursor(str(last_id))
        return RunRequest(run_key=f"upload-{last_id}")
    return SkipReason("no new uploads")


defs = Definitions(
    assets=[raw_sources, warehouse, marts_verified],
    jobs=[pipeline_job],
    schedules=[nightly_schedule],
    sensors=[upload_sensor],
)
