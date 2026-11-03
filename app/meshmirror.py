"""Mirror logins (request 217): a read-only login on this site's broker that another ArkSignal site (a test site, say) uses to read this site's public mesh feed live. It may subscribe to the public mesh topics and
nothing else: it cannot publish and never reads the private feed. Administrators make, switch off and delete them; the password is kept sealed and can be shown again (every opening is logged)."""
import re
import secrets

from . import mesh as ms
from . import vault
from .db import get_db
from .settings import cfg


def rows():
    return get_db().execute("SELECT m.*, u.username AS maker FROM mesh_mirror_logins m LEFT JOIN users u ON u.id = m.created_by ORDER BY m.created DESC").fetchall()


def get(mid):
    return get_db().execute("SELECT m.*, u.username AS maker FROM mesh_mirror_logins m LEFT JOIN users u ON u.id = m.created_by WHERE m.id=%s", (mid,)).fetchone()


def password_of(row):
    return vault.unseal(row["password_sealed"])


def _cred(row, pw=None):
    return {"username": row["username"], "password": pw if pw is not None else password_of(row), "community": "", "down": False, "private": False, "mirror": True}


def creds():
    """The enabled mirror logins in the form the mesh service takes (added to the sync of every member login, which would otherwise remove them)."""
    return [_cred(r) for r in get_db().execute("SELECT * FROM mesh_mirror_logins WHERE NOT disabled AND username <> ''").fetchall()]


def limit():
    return 10


def create(label, user_id):
    """(id, error). The row is only kept when the broker has the login."""
    d = get_db()
    if d.execute("SELECT COUNT(*) AS n FROM mesh_mirror_logins").fetchone()["n"] >= limit():
        return None, f"The site allows {limit()} mirror logins."
    label = (label or "").strip()[:60]
    if not label:
        return None, "Give the login a name, such as the site that will use it."
    pw = secrets.token_urlsafe(18)
    mid = d.execute("INSERT INTO mesh_mirror_logins (label, password_sealed, created_by) VALUES (%s,%s,%s) RETURNING id", (label, vault.seal(pw), user_id)).fetchone()["id"]
    name = f"mirror-{mid}"
    ok, r = ms.call("/credentials/set", "POST", {"username": name, "password": pw, "community": "", "down": False, "private": False, "mirror": True})
    if not ok or not r.get("ok"):
        d.rollback()
        return None, "The login could not be made on the broker right now: " + (r if isinstance(r, str) else str(r.get("error", "")))
    d.execute("UPDATE mesh_mirror_logins SET username=%s WHERE id=%s", (name, mid))
    d.commit()
    return mid, None


def new_password(mid):
    row = get(mid)
    pw = secrets.token_urlsafe(18)
    ok, r = ms.call("/credentials/set", "POST", _cred(row, pw))
    if not ok or not r.get("ok"):
        return False, "The broker could not change the password right now."
    d = get_db()
    d.execute("UPDATE mesh_mirror_logins SET password_sealed=%s WHERE id=%s", (vault.seal(pw), mid))
    d.commit()
    return True, ""


def set_disabled(mid, off):
    row = get(mid)
    ok, r = ms.call("/credentials/delete", "POST", {"username": row["username"]}) if off else ms.call("/credentials/set", "POST", _cred(row))
    if not ok or not r.get("ok"):
        return False, "The broker did not answer."
    d = get_db()
    d.execute("UPDATE mesh_mirror_logins SET disabled=%s WHERE id=%s", (bool(off), mid))
    d.commit()
    return True, ""


def delete(mid):
    row = get(mid)
    if row and row["username"]:
        ms.call("/credentials/delete", "POST", {"username": row["username"]})
    d = get_db()
    d.execute("DELETE FROM mesh_mirror_logins WHERE id=%s", (mid,))
    d.commit()


def connect_info():
    """What the other site types in: this broker's address and TLS port, and the topics to read."""
    host = cfg("mesh_join_host") or cfg("feed_host") or (cfg("main_address") or "")
    port = cfg("mesh_join_port") or "8883"
    root = (cfg("mesh_join_topic").strip("/") or "msh")
    return {"host": host, "port": port, "tls": port == "8883", "topics": f"{root}/#,meshcore/#"}


def read_other(host, port, tls, user, password, topics):
    """Point this site's second subscription at another site's broker (Settings > Mesh Nodes > Public Server): (ok, message). Forwarding stays whatever the communities chose; a read-only login cannot publish."""
    from .settings import set_setting
    host, user, topics = (host or "").strip(), (user or "").strip(), (topics or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9.-]{3,200}", host):
        return False, "Type the other site's broker address (a host name, like mesh.example.org)."
    if not str(port).isdigit() or not 1 <= int(port) <= 65535:
        return False, "The port is a number, usually 8883 for TLS."
    if not user or not password:
        return False, "Paste the login name and password the other site gave you."
    if not re.fullmatch(r"[A-Za-z0-9_+#/.,-]{1,300}", topics):
        return False, "The topics are separated by commas, like msh/#,meshcore/#."
    for k, v in (("mesh_up_host", host), ("mesh_up_port", str(port)), ("mesh_up_tls", "1" if tls else "0"), ("mesh_up_user", user), ("mesh_up_password", password), ("mesh_up_topic", topics), ("mesh_up_enabled", "1")):
        set_setting(k, v)
    ok, note = ms.push_config()
    return True, "This site now reads that broker." + ("" if ok else " The mesh service did not answer; press Apply on Admin, Mesh Nodes.")
