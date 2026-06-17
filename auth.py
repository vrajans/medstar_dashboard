"""
auth.py  -  InsightHub / MedStar Analytics
Flask-Login integration: User model, DB helpers, default user seeding.

Roles:
  admin   -- all tabs + Upload + User Management + all exports
  viewer  -- Overview, Sales, Purchases, Compare + exports (no Upload / User Mgmt)
  ca      -- accountant read-only: analytics + GST/YoY but no Upload/Users/Billing
"""

from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin
from sqlalchemy import create_engine, text

login_manager = LoginManager()
login_manager.session_protection = "strong"

# -- User model ---------------------------------------------------------------
class User(UserMixin):
    def __init__(self, user_id, username, role, display_name="",
                 tenant_id=None, tenant_name=None):
        self.id           = str(user_id)
        self.username     = username
        self.role         = role          # "admin" | "viewer" | "ca"
        self.display_name = display_name or username.capitalize()
        self.tenant_id    = tenant_id     # None = InsightHub internal user
        self.tenant_name  = tenant_name   # e.g. "Right Pharmacy"

    def is_admin(self):
        return self.role == "admin"

    def is_ca(self):
        """True if this is a CA (Chartered Accountant / accountant) read-only user."""
        return self.role == "ca"

    def is_viewer(self):
        return self.role == "viewer"

    def is_tenant_user(self):
        """True if this user belongs to an external tenant (not internal staff)."""
        return self.tenant_id is not None

    def can_upload(self):
        return self.role == "admin" and not self.is_tenant_user()

    def can_manage_users(self):
        return self.role == "admin"

    def can_manage_tenants(self):
        return self.role == "admin" and not self.is_tenant_user()

    def can_export(self):
        # CA users can export reports but not raw data uploads
        return self.role in ("admin", "viewer", "ca")

    def can_view_billing(self):
        return self.role == "admin" and not self.is_ca()

    @property
    def role_label(self):
        return {"admin": "Admin", "viewer": "Viewer", "ca": "Accountant"}.get(self.role, self.role.title())

    @property
    def role_color(self):
        return {"admin": "#1e7e4b", "viewer": "#0d6efd", "ca": "#6f42c1"}.get(self.role, "#6b7280")


# -- Engine reference ---------------------------------------------------------
_auth_engine = None

def _get_engine():
    return _auth_engine


# -- Bootstrap ----------------------------------------------------------------
def init_auth(flask_app, db_path):
    global _auth_engine
    _auth_engine = create_engine("sqlite:///{}".format(db_path), echo=False)

    login_manager.init_app(flask_app)
    login_manager.login_view = "/login"

    with _auth_engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role          TEXT NOT NULL DEFAULT 'viewer',
                display_name  TEXT,
                active        INTEGER DEFAULT 1,
                created_at    TEXT DEFAULT (datetime('now')),
                tenant_id     INTEGER,
                tenant_name   TEXT
            )
        """))
        conn.commit()

        for _col, _typedef in [("tenant_id", "INTEGER"), ("tenant_name", "TEXT")]:
            try:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {_col} {_typedef}"))
                conn.commit()
            except Exception:
                pass

        count = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
        if count == 0:
            import os as _os
            admin_pwd  = _os.environ.get("ADMIN_PASSWORD",  "MedStar@Admin#2026")
            viewer_pwd = _os.environ.get("VIEWER_PASSWORD", "MedStar@View#2026")
            defaults = [
                ("admin",  admin_pwd,  "admin",  "Administrator"),
                ("viewer", viewer_pwd, "viewer", "Viewer"),
            ]
            for uname, pwd, role, dname in defaults:
                conn.execute(text("""
                    INSERT INTO users (username, password_hash, role, display_name)
                    VALUES (:u, :p, :r, :d)
                """), {"u": uname, "p": generate_password_hash(pwd), "r": role, "d": dname})
            conn.commit()
            if _os.environ.get("ADMIN_PASSWORD"):
                print("[Auth] Default users seeded from environment variables.")
            else:
                print("[Auth] WARNING: Default users seeded with BUILT-IN passwords.")
                print("[Auth] Set ADMIN_PASSWORD and VIEWER_PASSWORD in .env before sharing.")
        else:
            print("[Auth] Users table ready ({} users)".format(count))

    @login_manager.user_loader
    def load_user(user_id):
        return get_user_by_id(user_id)

    return _auth_engine


# -- Query helpers ------------------------------------------------------------
def get_user_by_id(user_id):
    with _get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT id, username, role, display_name, tenant_id, tenant_name "
                 "FROM users WHERE id=:id AND active=1"),
            {"id": user_id},
        ).fetchone()
    if not row:
        return None
    return User(row[0], row[1], row[2], row[3],
                tenant_id=row[4], tenant_name=row[5])


def authenticate(username, password):
    """Return User if credentials valid, else None."""
    with _get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT id, username, password_hash, role, display_name, "
                 "tenant_id, tenant_name "
                 "FROM users WHERE username=:u AND active=1"),
            {"u": username},
        ).fetchone()
    if row and check_password_hash(row[2], password):
        return User(row[0], row[1], row[3], row[4],
                    tenant_id=row[5], tenant_name=row[6])
    return None


# -- CRUD ---------------------------------------------------------------------
def list_users():
    import pandas as pd
    with _get_engine().connect() as conn:
        df = pd.read_sql_query(
            "SELECT id, username, display_name, role, active, created_at, "
            "tenant_id, tenant_name FROM users ORDER BY id",
            conn,
        )
    df["created_at"]  = df["created_at"].astype(str).str[:10]
    df["active"]      = df["active"].apply(lambda x: "Yes" if x else "No")
    df["tenant_name"] = df["tenant_name"].fillna("-")
    return df


def create_user(username, password, role, display_name="", tenant_id=None, tenant_name=None):
    """Returns None on success, error string on failure."""
    try:
        with _get_engine().connect() as conn:
            conn.execute(text("""
                INSERT INTO users (username, password_hash, role, display_name, tenant_id, tenant_name)
                VALUES (:u, :p, :r, :d, :tid, :tname)
            """), {
                "u":     username,
                "p":     generate_password_hash(password),
                "r":     role,
                "d":     display_name or username.capitalize(),
                "tid":   tenant_id,
                "tname": tenant_name,
            })
            conn.commit()
        return None
    except Exception as e:
        return str(e)


def update_user_role(user_id, new_role):
    with _get_engine().connect() as conn:
        conn.execute(
            text("UPDATE users SET role=:r WHERE id=:id"),
            {"r": new_role, "id": user_id},
        )
        conn.commit()


def reset_password(user_id, new_password):
    with _get_engine().connect() as conn:
        conn.execute(
            text("UPDATE users SET password_hash=:p WHERE id=:id"),
            {"p": generate_password_hash(new_password), "id": user_id},
        )
        conn.commit()


def deactivate_user(user_id):
    with _get_engine().connect() as conn:
        conn.execute(
            text("UPDATE users SET active=0 WHERE id=:id AND username != 'admin'"),
            {"id": user_id},
        )
        conn.commit()


def list_roles():
    """Return available role strings."""
    return ["admin", "viewer", "ca"]
          text("UPDATE users SET active=1 WHERE id=:id"),
            {"id": user_id},
        )
        conn.commit()
