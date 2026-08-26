"""
api/create_user.py — create (or reset) a customer login for the Next.js app.

The login is stored in Neon (the same DB the customer app authenticates against)
and mapped to a tenant, so when that user signs in to :3000 they see ONLY that
customer's data.

Usage (repo root, venv active):
    python -m api.create_user <username> <password> <tenant_id> [role]

Examples:
    python -m api.create_user gratitude Grat@2026 5           # viewer for tenant 5
    python -m api.create_user gratitude Grat@2026 5 admin     # tenant admin

Tip: find a tenant's id in the Dash Tenants tab, or via GET /tenants in the API docs.
"""
import asyncio
import sys

from sqlalchemy import select

from .database import engine, AsyncSessionLocal, Base
from .models import User
from .security import hash_password


async def main(username: str, password: str, tenant_id: int, role: str = "viewer") -> None:
    # make sure tables + the tenant_id column exist
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE users ADD COLUMN tenant_id INTEGER"))
    except Exception:
        pass

    async with AsyncSessionLocal() as db:
        res = await db.execute(select(User).where(User.username == username.lower().strip()))
        user = res.scalar_one_or_none()
        if user:
            user.password_hash = hash_password(password)
            user.tenant_id = int(tenant_id)
            user.role = role
            user.is_active = True
            action = "reset"
        else:
            db.add(User(
                username=username.lower().strip(),
                display_name=username.capitalize(),
                password_hash=hash_password(password),
                role=role,
                tenant_id=int(tenant_id),
                is_active=True,
            ))
            action = "created"
        await db.commit()

    print(f"OK — {action} login for tenant {tenant_id}:")
    print(f"     username: {username.lower().strip()}")
    print(f"     password: {password}")
    print(f"     role:     {role}")
    print("Sign in at http://localhost:3000 to see this customer's data.")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python -m api.create_user <username> <password> <tenant_id> [role]")
        raise SystemExit(1)
    _role = sys.argv[4] if len(sys.argv) > 4 else "viewer"
    asyncio.run(main(sys.argv[1], sys.argv[2], int(sys.argv[3]), _role))
