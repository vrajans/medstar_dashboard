# InsightHub Orchestration

Two layers, one pipeline. Both run `validate → transform/load → verify` with run
history and retries — pick the one that matches your stage.

## 1. Lightweight in-process orchestrator (default, shipping today)

`orchestrator.py` in the app root. No extra services. It:

- runs on every upload (background thread from `confirm_upload`),
- runs nightly across all tenants (APScheduler job `warehouse_rebuild`, 02:30),
- records every run + step to `pipeline_runs` / `pipeline_step_runs`,
- retries transient failures and gates on a parity `verify` step,
- never blocks or breaks the app (failures are recorded, not raised).

Inspect run history:
```python
import orchestrator, data_loader
orchestrator.get_recent_runs(data_loader._get_sqlite_engine())      # recent runs
orchestrator.get_run_steps(engine, run_id)                          # steps of a run
```

Switch the transform engine to dbt (instead of the inline loader):
```bash
export ORCHESTRATOR_TRANSFORM=dbt   # runs `dbt build` in warehouse_dbt/
```

## 2. Dagster (graduation path, when you want the full UI)

`orchestration/definitions.py` expresses the **same** steps as Dagster assets
(`raw_sources → warehouse → marts_verified`) plus a nightly **schedule** and an
**upload sensor** (event-driven). It calls the identical `orchestrator.run_all`
and `warehouse.health_check` functions — graduating is wrapping, not rewriting.

```bash
pip install dagster dagster-webserver
dagster dev -f orchestration/definitions.py
# open http://localhost:3000  → Assets, Runs, Schedules, Sensors
```

What Dagster adds over the in-process layer: a run/asset UI, lineage graph,
backfills, richer retry/alerting, and horizontal execution — worth it once you
have many connectors and want operational visibility. Until then the in-process
orchestrator is the right SMB-stage choice (BRD §5.6).

### Wiring dbt into Dagster (optional)
For asset-level lineage over the dbt models, add `dagster-dbt` and load the
`warehouse_dbt` project with `@dbt_assets`; the `warehouse` asset above becomes
the dbt asset group. Kept out of the default scaffold to minimize dependencies.
