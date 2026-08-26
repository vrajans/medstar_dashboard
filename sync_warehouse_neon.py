"""
sync_warehouse_neon.py — build the InsightHub warehouse + serving marts in your
Neon Postgres database, so the FastAPI backend (and the :3000 customer app) have
data to serve.

It connects to Neon using PG_DSN_SYNC from .env (the sync/psycopg2 DSN), then:
  1. reports how much source data Neon currently has,
  2. creates the warehouse / orchestrator / LLM-config tables,
  3. backfills every tenant into the star schema + serving marts,
  4. prints a parity health check.

Run (from repo root, venv active):
    python sync_warehouse_neon.py
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from sqlalchemy import create_engine, text, inspect as sa_inspect

import warehouse as wh
import orchestrator as orch
from ai import llm_gateway


def _ensure_columns(eng) -> None:
    """The FastAPI-created sales/purchases tables may lack the warehouse's
    scoping columns. Add any that are missing (existing rows default to NULL =
    the reference/pharmacy tenant). Each ALTER runs in its own transaction so a
    Postgres error can't poison the batch."""
    wanted = [("tenant_id", "INTEGER"), ("upload_id", "INTEGER"), ("supplier_name", "TEXT")]
    insp = sa_inspect(eng)
    for tbl in ("sales", "purchases"):
        try:
            existing = {c["name"] for c in insp.get_columns(tbl)}
        except Exception:
            continue
        for col, typ in wanted:
            if col not in existing:
                try:
                    with eng.begin() as c:
                        c.execute(text(f"ALTER TABLE {tbl} ADD COLUMN {col} {typ}"))
                    print(f"   added {tbl}.{col}")
                except Exception as e:
                    print(f"   could not add {tbl}.{col}: {str(e)[:80]}")


def _dsn() -> str:
    dsn = os.getenv("PG_DSN_SYNC") or os.getenv("PG_DSN")
    if not dsn:
        sys.exit("ERROR: PG_DSN_SYNC (or PG_DSN) not set in .env")
    # psycopg2 wants sslmode=..., not the asyncpg-style ssl=...
    dsn = dsn.replace("+asyncpg", "+psycopg2").replace("ssl=require", "sslmode=require")
    return dsn


def main() -> int:
    eng = create_engine(_dsn(), pool_pre_ping=True)

    # 1) what's in Neon?
    print("── Source data in Neon ──")
    counts = {}
    with eng.connect() as c:
        for t in ("sales", "purchases"):
            try:
                counts[t] = c.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() or 0
            except Exception as e:
                counts[t] = f"(table missing: {str(e)[:60]})"
            print(f"   {t:10}: {counts[t]}")

    if not any(isinstance(counts[t], int) and counts[t] > 0 for t in counts):
        print("\n⚠  No sales/purchases rows found in Neon.")
        print("   The marts will be empty until your data is in Neon. Options:")
        print("     • migrate existing local data:  python migrate_to_pg.py")
        print("     • or upload through the app once it's pointed at Neon.")
        # still create the tables so the API doesn't 500 on empty marts
        wh.init_warehouse(eng)
        orch.init_orchestrator_tables(eng)
        llm_gateway.init_llm_config_table(eng)
        print("   (Created empty warehouse tables so the API runs cleanly.)")
        return 0

    # 1b) ensure the warehouse's scoping columns exist on the source tables
    print("\n── Ensuring scoping columns on source tables ──")
    _ensure_columns(eng)

    # 1c) assign any un-tenanted reference rows to a concrete tenant (id=1) so the
    #     warehouse scoping is clean and idempotent (avoids NULL-tenant edge cases).
    print("── Assigning reference data to tenant_id=1 ──")
    with eng.begin() as c:
        for tbl in ("sales", "purchases"):
            res = c.execute(text(f"UPDATE {tbl} SET tenant_id = 1 WHERE tenant_id IS NULL"))
            print(f"   {tbl}: tagged {res.rowcount} rows")

    # 2) create tables
    print("\n── Creating warehouse / orchestrator / LLM tables ──")
    wh.init_warehouse(eng)
    orch.init_orchestrator_tables(eng)
    llm_gateway.init_llm_config_table(eng)
    print("   done")

    # 3) backfill every tenant → star schema + marts
    print("\n── Building star schema + serving marts ──")
    results = wh.backfill_all(eng)
    for tid, stats in results.items():
        print(f"   tenant {tid}: inserted={stats.get('inserted')} "
              f"mart_rows={stats.get('mart_rows')}")

    # 4) parity check
    print("\n── Parity health check (flat vs warehouse) ──")
    ok_all = True
    for tid in results:
        tid_val = None if tid in ("None", None) else int(tid)
        h = wh.health_check(eng, tid_val)
        for tt, r in h.items():
            if isinstance(r, dict):
                flag = "OK" if r.get("match") else "MISMATCH"
                ok_all &= bool(r.get("match"))
                print(f"   tenant {tid} {tt:9}: flat={r['flat_rows']} wh={r['wh_rows']} → {flag}")

    print("\n✅ Neon warehouse ready." if ok_all else "\n⚠ Completed with mismatches — check above.")
    print("Next: restart FastAPI, then log in at http://localhost:3000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
