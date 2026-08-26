# InsightHub — dbt Transformation Layer

This dbt project is the **formalized, version-controlled transformation layer** for the
InsightHub dimensional warehouse. It reproduces exactly what `warehouse.py` does today,
but as tested, documented SQL models with dependency management — the idiomatic way to
build and maintain a star schema.

## What it builds

```
raw sales / purchases            (sources: the Flask app's flat tables)
        │
        ▼
stg_sales / stg_purchases        normalize + cast each source to the conformed shape
        │
        ▼
stg_transactions                 union + business_key_hash (coordinate + occurrence)
        │                                 + row_hash (change detection)
        ├──────────────► stg_entities ──► entity_snapshot (SCD2) ──► dim_entity
        ├──────────────► dim_date
        ├──────────────► dim_tenant
        ▼
fct_transaction                  incremental, idempotent upsert on business_key_hash
        │
        ▼
mart_transaction                 flattened serving mart (OBT) the app reads
```

## How it maps to `warehouse.py`

| warehouse.py | dbt equivalent |
|---|---|
| `_scd2_entity()` valid_from/valid_to/is_current | **dbt snapshot** `entity_snapshot` (strategy=check) → `dim_entity` |
| business key = coordinate + occurrence | `row_number()` window in `stg_transactions` + `md5_of()` macro |
| `_upsert_fact()` idempotent upsert | **incremental** `fct_transaction` (delete+insert on `business_key_hash`) |
| `build_serving_mart()` | `mart_transaction` model |
| `health_check()` parity | dbt `unique` / `not_null` / `relationships` tests |

The two can run side by side; dbt is intended to **replace** the inline Python loader in
production (triggered by the orchestrator after ingestion), while `warehouse.py` remains a
convenient in-process path for local/SQLite dev.

## Running it

Dev (DuckDB — fast, no external DB):
```bash
python seed_duckdb.py                 # loads sales/purchases from ../medstar.db into DuckDB
dbt build --profiles-dir . --project-dir . --target dev
```

Prod (Postgres — the live Neon / Cloud SQL warehouse):
```bash
export PGHOST=... PGPORT=5432 PGUSER=... PGPASSWORD=... PGDATABASE=...
dbt build --profiles-dir . --project-dir . --target prod
```

Useful commands:
```bash
dbt run      # build models only
dbt test     # run the data-quality tests only
dbt snapshot # advance SCD2 history
dbt docs generate && dbt docs serve   # browse the lineage graph + docs
dbt build --full-refresh              # rebuild facts from scratch (hard-delete reconcile)
```

## Verified behavior (DuckDB, real data)

- **Parity:** `fct_transaction` sums/row-counts match the flat tables exactly
  (pharmacy sales 526,765 / 62; tenant-3 sales 2,029,503.88 / 328; purchases 752,112.47 / 554).
- **Idempotent:** re-running the incremental fact leaves row counts unchanged.
- **Change propagation:** a measure change updates exactly one fact row (count stable).
- **SCD2:** entity attribute changes open a new version and preserve history.
- **Tests:** 9 dbt tests pass (unique/not_null keys, accepted values, FK relationships).
- **Client dimension preserved:** tenant-3 mart keeps all 15 distinct clients.

## Notes

- SQL is portable across **DuckDB** (dev) and **Postgres** (prod) via the macros in
  `macros/warehouse_helpers.sql` (`date_key`, `to_date`, `md5_of`, `month_name`).
- Hard-delete reconciliation (a source row that disappears) is handled by a scheduled
  `--full-refresh`, or by promoting the fact to snapshot semantics — see `fct_transaction.sql`.
- Secrets are never committed; the prod profile reads Postgres credentials from env vars.
