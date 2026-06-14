"""
migrate_to_pg.py
One-shot migration: SQLite (medstar.db) --> PostgreSQL.

Usage:
    python migrate_to_pg.py                   # safe (skips if rows exist)
    python migrate_to_pg.py --truncate        # wipe PG tables first, re-insert all
    python migrate_to_pg.py --dry-run         # show counts without writing

Reads PG_DSN from environment / .env (same as the FastAPI service).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# ── Env / config ──────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

SQLITE_PATH = Path(__file__).parent / "medstar.db"
PG_DSN_SYNC = os.getenv(
    "PG_DSN_SYNC",
    os.getenv("PG_DSN", "postgresql+asyncpg://postgres:postgres@localhost:5432/medstar")
    .replace("postgresql+asyncpg://", "postgresql+psycopg2://"),
)

# ── Arg parsing ───────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="Migrate MedStar SQLite data to PostgreSQL")
parser.add_argument("--truncate", action="store_true", help="Truncate PG tables before inserting")
parser.add_argument("--dry-run",  action="store_true", help="Show counts only, no writes")
args = parser.parse_args()

# ── Imports after arg parse (so --help works without heavy deps) ──────────────
import pandas as pd
import sqlalchemy as sa
from sqlalchemy import text

print("=" * 60)
print("InsightHub  --  SQLite -> PostgreSQL Migration")
print("=" * 60)

# ── Source: SQLite ────────────────────────────────────────────────────────────
if not SQLITE_PATH.exists():
    print(f"ERROR: SQLite database not found at {SQLITE_PATH}")
    print("Run 'python app.py' once to populate medstar.db first.")
    sys.exit(1)

sqlite_engine = sa.create_engine(f"sqlite:///{SQLITE_PATH}")

def read_sqlite(table: str) -> pd.DataFrame:
    # Use read_sql_query (not read_sql_table) — works with Engine in all pandas versions
    try:
        with sqlite_engine.connect() as conn:
            df = pd.read_sql_query(f"SELECT * FROM \"{table}\"", conn)
        print(f"  SQLite [{table}]: {len(df):,} rows")
        return df
    except Exception as exc:
        print(f"  SQLite [{table}]: MISSING or ERROR -- {exc}")
        return pd.DataFrame()

print("\n[1/4] Reading SQLite tables...")
sales_df    = read_sqlite("sales")
purchase_df = read_sqlite("purchases")
users_df    = read_sqlite("users")

if args.dry_run:
    print("\n-- DRY RUN: no changes written to PostgreSQL --")
    sys.exit(0)

# ── Target: PostgreSQL ────────────────────────────────────────────────────────
print(f"\n[2/4] Connecting to PostgreSQL...")
print(f"  DSN: {PG_DSN_SYNC.split('@')[-1]}")   # hide credentials

try:
    pg_engine = sa.create_engine(PG_DSN_SYNC, pool_pre_ping=True)
    with pg_engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    print("  Connected OK")
except Exception as exc:
    print(f"  ERROR: cannot connect to PostgreSQL -- {exc}")
    print("\nTips:")
    print("  1. Make sure PostgreSQL is running")
    print("  2. Create the database:  createdb medstar")
    print("  3. Set PG_DSN_SYNC in .env if credentials differ from defaults")
    sys.exit(1)

# ── Create schema ─────────────────────────────────────────────────────────────
print("\n[3/4] Creating tables (if not exist)...")

# We import the ORM models to let SQLAlchemy create the schema.
# We need a sync metadata for this step.
from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Float,
    Integer, MetaData, String, Table, Text, UniqueConstraint
)
from sqlalchemy.sql import func as sqlfunc

meta = MetaData()

users_table = Table("users", meta,
    Column("id",           Integer,  primary_key=True, autoincrement=True),
    Column("username",     String(64),  nullable=False, unique=True),
    Column("display_name", String(128), nullable=False, default=""),
    Column("password_hash",Text,        nullable=False),
    Column("role",         String(16),  nullable=False, default="viewer"),
    Column("is_active",    Boolean,     nullable=False, default=True),
    Column("created_at",   DateTime(timezone=True), server_default=sqlfunc.now()),
)

refresh_tokens_table = Table("refresh_tokens", meta,
    Column("id",         Integer,     primary_key=True, autoincrement=True),
    Column("user_id",    Integer,     nullable=False),
    Column("token_hash", String(128), nullable=False, unique=True),
    Column("issued_at",  DateTime(timezone=True), server_default=sqlfunc.now()),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked",    Boolean,     nullable=False, default=False),
)

sales_table = Table("sales", meta,
    Column("id",               BigInteger, primary_key=True, autoincrement=True),
    Column("branch",           String(64),  nullable=False),
    Column("month_label",      String(32),  nullable=False),
    Column("bill_date",        String(32),  nullable=False),
    Column("net_amount",       Float,       nullable=False, default=0.0),
    Column("cash_bill_count",  Float),
    Column("cash_sales",       Float),
    Column("credit_bill_count",Float),
    Column("credit_sales",     Float),
    Column("card_bill_count",  Float),
    Column("card_sales",       Float),
    Column("return_count",     Float),
    Column("cash_return",      Float),
    Column("discount",         Float),
    Column("total_bills",      Float),
    Column("pharma_sales",     Float),
    Column("non_pharma_sales", Float),
    Column("cash_in_hand",     Float),
    Column("cost_of_sales",    Float),
    Column("value",            Float),
    Column("margin_pct",       Float),
    UniqueConstraint("branch", "bill_date", name="uq_sales_branch_date"),
)

purchases_table = Table("purchases", meta,
    Column("id",               BigInteger, primary_key=True, autoincrement=True),
    Column("branch",           String(64),  nullable=False),
    Column("month_label",      String(32),  nullable=False),
    Column("supplier_code",    String(32)),
    Column("supplier_name",    String(256)),
    Column("gross_amount",     Float),
    Column("discount_pct",     Float),
    Column("adjustment_value", Float),
    Column("net_amount",       Float, nullable=False, default=0.0),
    Column("vat_amount",       Float),
    Column("grn_number",       String(64)),
    Column("grn_date",         String(32)),
    Column("invoice_number",   String(64)),
    Column("invoice_date",     String(32)),
    Column("base_amount",      Float),
    Column("sgst",             Float),
    Column("cgst",             Float),
    Column("igst",             Float),
    Column("total_gst",        Float),
    Column("amount",           Float),
    Column("dealer_type",      String(64)),
)

meta.create_all(pg_engine)
print("  Schema ready")

# ── Insert data ───────────────────────────────────────────────────────────────
print("\n[4/4] Migrating data...")

def migrate_table(df: pd.DataFrame, table_name: str, truncate: bool):
    if df.empty:
        print(f"  [{table_name}]: no data to migrate")
        return

    with pg_engine.begin() as conn:
        if truncate:
            conn.execute(text(f"TRUNCATE TABLE {table_name} RESTART IDENTITY CASCADE"))
            print(f"  [{table_name}]: truncated")

        # Drop 'id' column if present -- let PG auto-assign
        insert_df = df.drop(columns=["id"], errors="ignore")

        # Check existing row count
        existing = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()
        if existing > 0 and not truncate:
            print(f"  [{table_name}]: {existing:,} rows already exist -- skipping (use --truncate to overwrite)")
            return

        insert_df.to_sql(table_name, conn, if_exists="append", index=False, method="multi", chunksize=500)
        inserted = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()
        print(f"  [{table_name}]: inserted {len(insert_df):,} rows  (total in PG: {inserted:,})")

migrate_table(sales_df,    "sales",     args.truncate)
migrate_table(purchase_df, "purchases", args.truncate)

# SQLite auth.py uses column name "active"; PostgreSQL schema uses "is_active"
if "active" in users_df.columns and "is_active" not in users_df.columns:
    users_df = users_df.rename(columns={"active": "is_active"})
migrate_table(users_df,    "users",     args.truncate)

print("\nMigration complete.")
print("Next: uvicorn api.main:app --reload --port 8000")
