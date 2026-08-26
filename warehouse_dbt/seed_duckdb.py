"""
seed_duckdb.py — dev helper: copy the app's flat `sales` / `purchases` tables
from the local SQLite DB into a DuckDB file so `dbt build --target dev` can run
against realistic data without a Postgres instance.
"""
import sqlite3, sys
import duckdb
import pandas as pd

SQLITE_DB = sys.argv[1] if len(sys.argv) > 1 else "../medstar.db"
DUCKDB    = "dbt_dev.duckdb"

sq = sqlite3.connect(SQLITE_DB)
con = duckdb.connect(DUCKDB)
for t in ("sales", "purchases"):
    cur = sq.execute(f"SELECT * FROM {t}")
    cols = [d[0] for d in cur.description]
    pdf = pd.DataFrame(cur.fetchall(), columns=cols)          # noqa: F841 (used by duckdb)
    con.execute(f"CREATE OR REPLACE TABLE {t} AS SELECT * FROM pdf")
    print(f"{t}: {con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]} rows")
con.close(); sq.close()
print(f"Seeded {DUCKDB} from {SQLITE_DB}")
