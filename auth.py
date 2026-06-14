"""
auth.py  —  InsightHub / MedStar Analytics
Flask-Login integration: User model, DB helpers, default user seeding.

Roles:
  admin   — all tabs + Upload + User Management + all exports
  viewer  — Overview, Sales, Purchases, Compare + exports (no Upload / User Mgmt)
"""

from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin
from sqlalchemy import create_engine, text

login_manager = LoginManager()
login_manager.session_protection = "strong"

# ── User model ────────────────────────────────────────────────
class User(UserMixin):
    def __init__(self, user_id, username, role, display_name=""):
        self.id           = str(user_id)
        self.username     = username
        self.role         = role          # "admin" | "viewer"
        self.display_name = display_name or username.capitalize()

    def is_admin(self):
        return self.role == "admin"

    def can_upload(self):
        return self.role == "admin"

    def can_manage_users(self):
        return self.role == "admin"

    def can_export(self):
        return True   # both roles can download CSV/Excel/PDF

    @property
    def role_label(self):
        return "Admin" if self.is_admin() else "Viewer"

    @property
    def role_color(self):
        return "#1e7e4b" if self.is_admin() else "#0d6efd"


# ── Engine reference (set by init_auth) ───────────────────────
_auth_engine = None


def _get_engine():
    return _auth_engine


# ── Bootstrap ─────────────────────────────────────────────────
def init_auth(flask_app, db_path):
    """
    Register Flask-Login with the Dash/Flask app server.
    Creates users table + seeds defaults on first run.
    Returns the SQLAlchemy engine used for auth (same DB as pharmacy data).
    """
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
                created_at    TEXT DEFAULT (datetime('now'))
            )
        """))
        conn.commit()

        count = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
        if count == 0:
            import os as _os
            # PRODUCTION: set ADMIN_PASSWORD and VIEWER_PASSWORD in your .env
            # Do NOT use these defaults for real users.
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
                """), {"u": uname,
                       "p": generate_password_hash(pwd),
                       "r": role,
                       "d": dname})
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


# ── Query helpers ─────────────────────────────────────────────
def get_user_by_id(user_id):
    with _get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT id, username, role, display_name FROM users "
                 "WHERE id=:id AND active=1"),
            {"id": user_id},
        ).fetchone()
    return User(row[0], row[1], row[2], row[3]) if row else None


def authenticate(username, password):
    """Return User if credentials valid, else None."""
    with _get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT id, username, password_hash, role, display_name "
                 "FROM users WHERE username=:u AND active=1"),
            {"u": username},
        ).fetchone()
    if row and check_password_hash(row[2], password):
        return User(row[0], row[1], row[3], row[4])
    return None


# ── CRUD ──────────────────────────────────────────────────────
def list_users():
    import pandas as pd
    with _get_engine().connect() as conn:
        return pd.read_sql_query(
            "SELECT id, username, display_name, role, active, created_at "
            "FROM users ORDER BY id",
            conn,
        )


def create_user(username, password, role, display_name=""):
    """Returns None on success, error string on failure."""
    try:
        with _get_engine().connect() as conn:
            conn.execute(text("""
                INSERT INTO users (username, password_hash, role, display_name)
                VALUES (:u, :p, :r, :d)
            """), {"u": username,
                   "p": generate_password_hash(password),
                   "r": role,
                   "d": display_name or username.capitalize()})
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


def reactivate_user(user_id):
    with _get_engine().connect() as conn:
        conn.execute(
            text("UPDATE users SET active=1 WHERE id=:id"),
            {"id": user_id},
        )
        conn.commit()
