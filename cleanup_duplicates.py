"""
cleanup_duplicates.py
Run ONCE to remove duplicate upload rows already in the database.

Usage (with app STOPPED):
    python cleanup_duplicates.py

What it does:
  - For each tenant + branch + period combination that has multiple active uploads,
    keeps only the LATEST upload and deletes all older data rows + marks them 'replaced'.
  - Safe to run multiple times (idempotent).
"""

import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(__file__), "medstar.db")

def run():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print(f"Connected to: {DB_PATH}")

    # Find groups with more than one active upload entry for same branch+period+tenant
    cur.execute("""
        SELECT branch, month_label, tenant_id, COUNT(*) as cnt,
               MAX(id) as keep_id, GROUP_CONCAT(id) as all_ids
        FROM upload_history
        WHERE status = 'active'
        GROUP BY branch, month_label, COALESCE(tenant_id, -1)
        HAVING cnt > 1
    """)
    groups = cur.fetchall()

    if not groups:
        print("No duplicates found — database is clean.")
        conn.close()
        return

    total_deleted_rows = 0
    total_replaced     = 0

    for g in groups:
        all_ids  = [int(x) for x in str(g["all_ids"]).split(",")]
        keep_id  = int(g["keep_id"])
        drop_ids = [i for i in all_ids if i != keep_id]
        table    = "sales"  # determine from report_type
        cur.execute("SELECT report_type FROM upload_history WHERE id=?", (keep_id,))
        rt_row = cur.fetchone()
        if rt_row and rt_row[0] in ("purchase", "generic_purchases"):
            table = "purchases"

        print(f"\nGroup: branch={g['branch']}  period={g['month_label']}  "
              f"tenant_id={g['tenant_id']}  uploads={all_ids}  keeping={keep_id}")

        for old_id in drop_ids:
            # Count rows about to be deleted
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE upload_id=?", (old_id,))
            n = cur.fetchone()[0]
            cur.execute(f"DELETE FROM {table} WHERE upload_id=?", (old_id,))
            cur.execute("UPDATE upload_history SET status='replaced' WHERE id=?", (old_id,))
            print(f"  Dropped upload_id={old_id}: deleted {n} rows from {table}, "
                  f"marked history as 'replaced'")
            total_deleted_rows += n
            total_replaced += 1

    conn.commit()
    conn.close()
    print(f"\nDone. Removed {total_deleted_rows} duplicate data rows "
          f"across {total_replaced} superseded upload entries.")
    print("Restart the app — Upload History will now show only the active uploads.")

if __name__ == "__main__":
    run()
