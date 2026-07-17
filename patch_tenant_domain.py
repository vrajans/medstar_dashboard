"""One-time patch: add tenant_domain column and set Gratiture Solutions to saas."""
import sqlite3

conn = sqlite3.connect("medstar.db")

# Add column if not already there
try:
    conn.execute("ALTER TABLE users ADD COLUMN tenant_domain TEXT DEFAULT 'pharmacy'")
    conn.commit()
    print("Added tenant_domain column")
except Exception as e:
    print(f"Column note: {e}")

# Patch all Gratiture Solutions users to saas
conn.execute("UPDATE users SET tenant_domain='saas' WHERE tenant_name='Gratiture Solutions'")
conn.commit()
print(f"Patched {conn.total_changes} user(s)")

# Verify result
print("\nCurrent users table:")
print(f"{'ID':<4} {'Username':<12} {'Tenant Name':<25} {'Domain'}")
print("-" * 60)
for row in conn.execute("SELECT id, username, tenant_name, tenant_domain FROM users ORDER BY id"):
    tid = row[2] or "(internal)"
    dom = row[3] or "pharmacy"
    print(f"{row[0]:<4} {row[1]:<12} {tid:<25} {dom}")

conn.close()
print("\nDone. Restart the Dash app now.")
