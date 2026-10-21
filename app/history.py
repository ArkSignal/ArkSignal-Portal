"""Queries behind the Audit Log and Sign-In History pages."""
import csv
import io

from .db import get_db
from .settings import cfg_int

EXPORT_LIMIT = 50000
DAY_CHOICES = [("1", "Last 24 hours"), ("7", "Last 7 days"), ("30", "Last 30 days"), ("90", "Last 90 days"), ("0", "All time")]


def _where(clauses, params):
    return (" WHERE " + " AND ".join(clauses)) if clauses else ""


def _window(days, clauses, params):
    if days and days.isdigit() and int(days) > 0:
        clauses.append("ts >= now() - make_interval(days => %s)")
        params.append(int(days))


def audit_query(q="", action="", days="30", page=1, limit=None):
    limit = limit or cfg_int("page_size")
    clauses, params = [], []
    _window(days, clauses, params)
    if action:
        clauses.append("action = %s")
        params.append(action)
    if q:
        clauses.append("(actor_name ILIKE %s OR target ILIKE %s OR detail ILIKE %s OR ip ILIKE %s)")
        params += [f"%{q}%"] * 4
    w = _where(clauses, params)
    d = get_db()
    total = d.execute(f"SELECT COUNT(*) AS n FROM audit_log{w}", params).fetchone()["n"]
    rows = d.execute(f"SELECT * FROM audit_log{w} ORDER BY ts DESC, id DESC LIMIT %s OFFSET %s",
                     params + [limit, (page - 1) * limit]).fetchall()
    return rows, total


def audit_actions():
    return [r["action"] for r in get_db().execute("SELECT DISTINCT action FROM audit_log ORDER BY action").fetchall()]


def signin_query(q="", result="", days="30", page=1, limit=None):
    limit = limit or cfg_int("page_size")
    clauses, params = [], []
    _window(days, clauses, params)
    if result in ("ok", "failed"):
        clauses.append("success = %s")
        params.append(result == "ok")
    if q:
        clauses.append("(username ILIKE %s OR ip ILIKE %s OR detail ILIKE %s)")
        params += [f"%{q}%"] * 3
    w = _where(clauses, params)
    d = get_db()
    total = d.execute(f"SELECT COUNT(*) AS n FROM login_events{w}", params).fetchone()["n"]
    rows = d.execute(f"SELECT * FROM login_events{w} ORDER BY ts DESC, id DESC LIMIT %s OFFSET %s",
                     params + [limit, (page - 1) * limit]).fetchall()
    return rows, total


def to_csv(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def prune():
    """Delete history older than the retention setting (0 keeps everything). Returns rows removed."""
    days = cfg_int("history_retention_days")
    if days <= 0:
        return 0
    d = get_db()
    n = d.execute("DELETE FROM audit_log WHERE ts < now() - make_interval(days => %s)", (days,)).rowcount
    n += d.execute("DELETE FROM login_events WHERE ts < now() - make_interval(days => %s)", (days,)).rowcount
    d.commit()
    return n
