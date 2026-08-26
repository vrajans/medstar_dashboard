"""
api/create_admin.py — create or reset a login for the FastAPI backend (the one
the Next.js customer app on :3000 authenticates against).

Runs against the SAME database FastAPI uses (PG_DSN in .env), so the account it
makes is guaranteed to work for :3000.

Usage (from the repo root, with the venv active):
    python -m api.create_admin                 # creates/resets  admin / admin123
    python -m api.create_admin myuser mypass   # custom username / password
"""
import asyncio
import sys

from sqlalchemy import select

from .database import engine, AsyncSessionLocal, Base
from .models import User
from .security import hash_password


async def main(username: str = "admin", password: str = "admin123") -> None:
    # make sure the tables exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        res = await db.execute(select(User).where(User.username == username))
        user = res.scalar_one_or_none()
        if user:
            user.password_hash = hash_password(password)
            user.role = "admin"
            user.is_active = True
            action = "reset"
        else:
            db.add(User(
                username=username,
                display_name="Administrator",
                password_hash=hash_password(password),
                role="admin",
                is_active=True,
            ))
            action = "created"
        await db.commit()

    print(f"OK — {action} user.  Log in at http://localhost:3000 with:")
    print(f"     username: {username}")
    print(f"     password: {password}")


if __name__ == "__main__":
    args = sys.argv[1:3]
    asyncio.run(main(*args) if args else main())
