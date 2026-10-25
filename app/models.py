from datetime import datetime, timedelta, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db
from .settings import cfg_int
from .permissions import ADMIN_AREA, ADMIN_ROLE, IMPLIED_BY, USER_ROLE



def _now():
    return datetime.now(timezone.utc)


class User(UserMixin):
    def __init__(self, row):
        self.row = row
        self.id = row["id"]
        self.username = row["username"]
        self.email = row["email"]
        self.display_name = row["display_name"]
        self.callsign = row["callsign"]
        self.disabled = row["disabled"]
        self.must_change_password = row["must_change_password"]
        self._roles = None
        self._perms = None

    # ---- roles and permissions (loaded once per request) ----
    def _load_access(self):
        if self._roles is None:
            d = get_db()
            self._roles = d.execute(
                "SELECT r.id, r.name, r.is_super_admin FROM roles r "
                "JOIN user_roles ur ON ur.role_id = r.id WHERE ur.user_id=%s ORDER BY lower(r.name)",
                (self.id,)).fetchall()
            self._perms = {r["permission"] for r in d.execute(
                "SELECT DISTINCT rp.permission FROM role_permissions rp "
                "JOIN user_roles ur ON ur.role_id = rp.role_id WHERE ur.user_id=%s", (self.id,)).fetchall()}

    @property
    def roles(self):
        self._load_access()
        return self._roles

    @property
    def is_super_admin(self):
        return any(r["is_super_admin"] for r in self.roles)

    def has_permission(self, perm):
        self._load_access()
        return self.is_super_admin or perm in self._perms or any(p in self._perms for p in IMPLIED_BY.get(perm, ()))

    @property
    def has_admin_access(self):
        return any(self.has_permission(p) for p in ADMIN_AREA)

    @property
    def is_active(self):
        return not self.disabled

    @property
    def label(self):
        return self.display_name or self.username

    # ---- sign-in state ----
    def check_password(self, pw):
        return check_password_hash(self.row["password_hash"], pw)

    def is_locked(self):
        lu = self.row["locked_until"]
        return bool(lu and lu > _now())

    def register_failure(self):
        d = get_db()
        n = self.row["failed_attempts"] + 1
        locked = n >= cfg_int("max_failed_attempts")
        d.execute("UPDATE users SET failed_attempts=%s, locked_until=%s WHERE id=%s",
                  (0 if locked else n,
                   _now() + timedelta(minutes=cfg_int("lockout_minutes")) if locked else None, self.id))
        d.commit()
        return locked

    def register_success(self):
        d = get_db()
        d.execute("UPDATE users SET failed_attempts=0, locked_until=NULL, last_login=now() WHERE id=%s",
                  (self.id,))
        d.commit()

    def set_password(self, pw, must_change=False):
        d = get_db()
        d.execute("UPDATE users SET password_hash=%s, must_change_password=%s, password_changed_at=now(), "
                  "failed_attempts=0, locked_until=NULL WHERE id=%s",
                  (generate_password_hash(pw), must_change, self.id))
        d.execute("UPDATE user_sessions SET ended=TRUE WHERE user_id=%s", (self.id,))   # a new password ends every sign-in of the account (whoever changed it keeps nothing: the caller starts a new one)
        d.commit()

    # ---- lookups ----
    @staticmethod
    def get(user_id):
        r = get_db().execute("SELECT * FROM users WHERE id=%s", (user_id,)).fetchone()
        return User(r) if r else None

    @staticmethod
    def get_by_username(name):
        r = get_db().execute("SELECT * FROM users WHERE lower(username)=lower(%s)", (name,)).fetchone()
        return User(r) if r else None

    @staticmethod
    def get_by_login(name):
        """Sign in with the username or the email address (either capitalization)."""
        name = (name or "").strip()
        r = get_db().execute("SELECT * FROM users WHERE lower(username)=lower(%s) OR lower(email)=lower(%s) "
                             "ORDER BY (lower(username)=lower(%s)) DESC LIMIT 1", (name, name, name)).fetchone()
        return User(r) if r else None

    @staticmethod
    def email_taken(email):
        return get_db().execute("SELECT 1 FROM users WHERE lower(email)=lower(%s)", (email,)).fetchone() is not None

    @staticmethod
    def create(username, email, password, role_name=USER_ROLE, must_change=False):
        d = get_db()
        uid = d.execute(
            "INSERT INTO users (username, email, password_hash, must_change_password) "
            "VALUES (%s,%s,%s,%s) RETURNING id",
            (username, email, generate_password_hash(password), must_change)).fetchone()["id"]
        d.execute("INSERT INTO user_roles (user_id, role_id) "
                  "SELECT %s, id FROM roles WHERE lower(name)=lower(%s)", (uid, role_name))
        d.commit()
        return uid

    @staticmethod
    def all_with_roles():
        """Every account with its role names, for the admin Users page."""
        return get_db().execute(
            "SELECT u.*, COALESCE(jsonb_agg(jsonb_build_object('id', r.id, 'name', r.name, 'is_super_admin', r.is_super_admin) "
            "ORDER BY lower(r.name)) FILTER (WHERE r.id IS NOT NULL), '[]'::jsonb) AS roles_list "
            "FROM users u LEFT JOIN user_roles ur ON ur.user_id = u.id LEFT JOIN roles r ON r.id = ur.role_id "
            "GROUP BY u.id ORDER BY lower(u.username)").fetchall()

    @staticmethod
    def bootstrap_admin(username, email, password):
        """First start only: create the first admin from env if no users exist."""
        if get_db().execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0:
            if len(password) < cfg_int("min_password_length") or "@" not in email:
                raise RuntimeError("No users yet: set ADMIN_USERNAME, ADMIN_EMAIL and a long enough ADMIN_PASSWORD in .env")
            User.create(username, email, password, role_name=ADMIN_ROLE)


def log_login(username, ip, success, detail=""):
    d = get_db()
    d.execute("INSERT INTO login_events (username, ip, success, detail) VALUES (%s,%s,%s,%s)",
              (username, ip, success, detail))
    d.commit()
