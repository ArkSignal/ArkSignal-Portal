"""Role queries and the rules that keep role changes safe."""
from .db import get_db
from .permissions import PERMISSIONS


def roles_overview():
    """Every role with its permissions and members, for the Roles page."""
    d = get_db()
    roles = d.execute("SELECT * FROM roles ORDER BY is_builtin DESC, lower(name)").fetchall()
    perms = {}
    for r in d.execute("SELECT role_id, permission FROM role_permissions").fetchall():
        perms.setdefault(r["role_id"], set()).add(r["permission"])
    members = {}
    for r in d.execute("SELECT ur.role_id, u.id, u.username, u.disabled FROM user_roles ur "
                       "JOIN users u ON u.id = ur.user_id ORDER BY lower(u.username)").fetchall():
        members.setdefault(r["role_id"], []).append(r)
    return [{"role": r, "perms": perms.get(r["id"], set()), "members": members.get(r["id"], [])} for r in roles]


def get_role(role_id):
    return get_db().execute("SELECT * FROM roles WHERE id=%s", (role_id,)).fetchone()


def role_perms(role_id):
    return {r["permission"] for r in get_db().execute(
        "SELECT permission FROM role_permissions WHERE role_id=%s", (role_id,)).fetchall()}


def can_grant(actor, role):
    """A super admin may grant anything; anyone else only roles that are not Super Admin and
    carry no permission the actor does not hold themselves."""
    if actor.is_super_admin:
        return True
    if role["is_super_admin"]:
        return False
    return all(actor.has_permission(p) for p in role_perms(role["id"]))


def enabled_super_admins():
    return get_db().execute(
        "SELECT COUNT(DISTINCT u.id) AS n FROM users u JOIN user_roles ur ON ur.user_id = u.id "
        "JOIN roles r ON r.id = ur.role_id WHERE r.is_super_admin AND NOT u.disabled").fetchone()["n"]


def valid_permissions(values):
    return [v for v in values if v in PERMISSIONS]
