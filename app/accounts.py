"""Account recovery and deletion: reset tokens for the email method, throttling, and removing an account.

Two ways to recover an account, both throttled per address and per account:
 - email: a single-use link, valid for a short time, stored only as a hash (needs SMTP);
 - station: the person proves they own a claimed station by knowing its UUID, which is shown only to the station's owner.
"""
import hashlib
import re
import secrets

from .db import get_db
from .settings import cfg, cfg_int

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def make_token(user_id):
    """A fresh single-use token for the user (older unused ones are cancelled). Returns the token to put in the email."""
    token = secrets.token_urlsafe(32)
    d = get_db()
    d.execute("UPDATE password_resets SET used_at=now() WHERE user_id=%s AND used_at IS NULL", (user_id,))
    d.execute("INSERT INTO password_resets (user_id, token_hash, expires_at) VALUES (%s,%s, now() + make_interval(mins => %s))",
              (user_id, _hash(token), cfg_int("reset_token_minutes")))
    d.commit()
    return token


def find_token(token):
    """The user id a valid, unused, unexpired token belongs to, or None."""
    if not token or len(token) > 100:
        return None
    r = get_db().execute("SELECT user_id FROM password_resets WHERE token_hash=%s AND used_at IS NULL AND expires_at > now()",
                         (_hash(token),)).fetchone()
    return r["user_id"] if r else None


def use_token(token):
    d = get_db()
    d.execute("UPDATE password_resets SET used_at=now() WHERE token_hash=%s", (_hash(token),))
    d.commit()


def allowed(ip, subject):
    """False when this address or this account has used up its recovery attempts for the hour."""
    limit = cfg_int("reset_attempts_per_hour")
    d = get_db()
    by_ip = d.execute("SELECT COUNT(*) AS n FROM reset_attempts WHERE ip=%s AND ts > now() - interval '1 hour'", (ip,)).fetchone()["n"]
    by_subject = d.execute("SELECT COUNT(*) AS n FROM reset_attempts WHERE lower(subject)=lower(%s) AND ts > now() - interval '1 hour'",
                           (subject,)).fetchone()["n"]
    return by_ip < limit and by_subject < limit


def note_attempt(ip, subject, method, ok):
    d = get_db()
    d.execute("INSERT INTO reset_attempts (ip, subject, method, ok) VALUES (%s,%s,%s,%s)", (ip, (subject or "")[:254], method, ok))
    d.execute("DELETE FROM reset_attempts WHERE ts < now() - interval '2 days'")
    d.commit()


def owns_station(user_id, uuid):
    uuid = (uuid or "").strip().lower()
    if not UUID_RE.match(uuid):
        return False
    return get_db().execute("SELECT 1 FROM feeders WHERE uuid=%s AND owner_id=%s", (uuid, user_id)).fetchone() is not None


def last_super_admin(user):
    """True when deleting this account would leave nobody with full access."""
    if not user.is_super_admin:
        return False
    n = get_db().execute(
        "SELECT COUNT(DISTINCT ur.user_id) AS n FROM user_roles ur JOIN roles r ON r.id = ur.role_id "
        "JOIN users u ON u.id = ur.user_id WHERE r.is_super_admin AND NOT u.disabled").fetchone()["n"]
    return n <= 1


def delete_account(user):
    """Remove the account, its alert rules and history. Its stations are released (unclaimed, names and IDs cleared) so they can be claimed again."""
    d = get_db()
    for f in d.execute("SELECT uuid FROM feeders WHERE owner_id=%s", (user.id,)).fetchall():
        d.execute("UPDATE feeders SET owner_id=NULL, station_id=NULL, station_id_kind=NULL, claimed_at=NULL, claim_method=NULL, "
                  "name=NULL, description=NULL, notes=NULL, website=NULL, website_public=FALSE, official=FALSE WHERE uuid=%s", (f["uuid"],))
        d.execute("INSERT INTO feeder_events (feeder_uuid, event, detail) VALUES (%s,'released',%s)", (f["uuid"], f"account {user.username} deleted"))
    d.execute("DELETE FROM users WHERE id=%s", (user.id,))
    d.commit()
