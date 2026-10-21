"""Runs inside the container: docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/digest_unit.py
Drives alerts.send_digests with a stubbed mailer: combining, per-hour and per-day limits, the item cap, expiry."""
import os
from datetime import datetime, timedelta, timezone

os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
os.environ["HUB_URL"] = ""
import psycopg
from psycopg.rows import dict_row
from app import alerts as al
from app import create_app, mailer
from app.settings import set_setting

app = create_app()
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)

sent = []
mailer.smtp_configured = lambda: True
mailer.send_mail = lambda to, subject, body: (sent.append((to, subject, body)) or (True, "ok"))
URL = app.config["DATABASE_URL"]
with app.app_context():
    for k, v in (("alert_email_per_hour", "2"), ("alert_email_per_day", "3"), ("alert_email_combine_minutes", "5"), ("alert_email_max_items", "5")):
        set_setting(k, v)
conn = psycopg.connect(URL, row_factory=dict_row, autocommit=True)
conn.execute("DELETE FROM users WHERE username='digestuser'")
uid = conn.execute("INSERT INTO users (username, email, password_hash) VALUES ('digestuser','digest@example.com','x') RETURNING id").fetchone()["id"]
rid = conn.execute("INSERT INTO alert_rules (user_id, name, kind, notify_email) VALUES (%s,'Test rule','military',TRUE) RETURNING id", (uid,)).fetchone()["id"]

def add(minutes_ago, n=1, state="pending"):
    for i in range(n):
        conn.execute("INSERT INTO alert_events (rule_id, user_id, ts, hex, callsign, email_state) VALUES (%s,%s,now() - make_interval(mins => %s),%s,%s,%s)",
                     (rid, uid, minutes_ago, f"{abs(hash((minutes_ago, i))) % 0xFFFFFF:06x}", f"TST{i}", state))
def pending(): return conn.execute("SELECT COUNT(*) AS n FROM alert_events WHERE user_id=%s AND email_state='pending'", (uid,)).fetchone()["n"]

add(1)                                                   # first match one minute ago
check("matches younger than the combine window wait", al.send_digests(app) == 0 and not sent)
add(1, 2)
add(6)                                                   # the oldest is now past the window: one email carries all four
check("the oldest match past the window sends one combined email", al.send_digests(app) == 1 and len(sent) == 1)
check("the email lists every match", sent[0][2].count("TST") == 4 and sent[0][1].endswith("4 aircraft"), sent[0][1])
check("sent matches are marked, none left waiting", pending() == 0)
check("nothing more is sent when nothing waits", al.send_digests(app) == 0)

add(10, 8)                                               # second email of the hour, with more matches than the item cap
check("second email goes out within the hourly limit", al.send_digests(app) == 1 and len(sent) == 2)
check("the item cap is applied and the rest counted", sent[1][2].count("TST") == 5 and "and 3 more" in sent[1][2], sent[1][2][-300:])
add(10)
check("third email in the hour is held back by the hourly limit", al.send_digests(app) == 0 and pending() == 1)
conn.execute("UPDATE alert_emails SET ts = now() - interval '2 hours' WHERE user_id=%s", (uid,))
check("after the hour passes the held match is sent", al.send_digests(app) == 1 and pending() == 0)
add(10); add(10, 2)
conn.execute("UPDATE alert_emails SET ts = now() - interval '2 hours' WHERE user_id=%s", (uid,))
check("the daily limit holds further emails", al.send_digests(app) == 0 and pending() == 3)
conn.execute("UPDATE alert_events SET ts = now() - interval '26 hours' WHERE user_id=%s AND email_state='pending'", (uid,))
al.send_digests(app)
check("matches older than a day expire instead of piling up", pending() == 0 and conn.execute("SELECT COUNT(*) AS n FROM alert_events WHERE user_id=%s AND email_state='expired'", (uid,)).fetchone()["n"] == 3)
with app.app_context():
    h, d, w = al.email_usage(psycopg.connect(URL, row_factory=dict_row), uid)
check("usage counts for the alerts page", d == 3 and w == 0, (h, d, w))
conn.execute("DELETE FROM users WHERE username='digestuser'")
with app.app_context():
    for k, v in (("alert_email_per_hour", "4"), ("alert_email_per_day", "20"), ("alert_email_combine_minutes", "5"), ("alert_email_max_items", "25")):
        set_setting(k, v)
print("ALL PASS" if ok_all else "SOME FAILED")
