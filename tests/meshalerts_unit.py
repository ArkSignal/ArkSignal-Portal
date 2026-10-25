import os, json
from datetime import datetime, timedelta, timezone
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
os.environ["DISABLE_MESH_SYNC"] = "1"
from app import create_app
app = create_app()
from app import mesh_alerts as ma, mailer
from app.db import get_db
from app.settings import set_setting
out = {}
NOW = datetime.now(timezone.utc)
OFF = [0]
iso = lambda h: (NOW + timedelta(hours=OFF[0]) - timedelta(hours=h)).isoformat().replace("+00:00", "Z")
def node(i, role="Router", seen=1, bat=None, name=None):
    return {"id": "!aaaa%04x" % i, "role_name": role, "_seen": seen, "battery": bat, "long_name": name or "Node %d" % i}
def run(h, *rows):
    """A pass h hours after the start; the nodes' last-heard times move along with it."""
    OFF[0] = h
    r = [dict(n, last_seen=iso(n["_seen"])) for n in rows]
    return ma.run_pass(NOW + timedelta(hours=h), r)
with app.app_context():
    d = get_db()
    for k, v in (("mesh_enabled", "1"), ("mesh_owner_alerts", "1"), ("mesh_offline_hours", "24"), ("mesh_low_battery_pct", "20"), ("mesh_owner_alert_repeat_hours", "24"), ("mesh_owner_alert_max_repeats", "2"),
                 ("mesh_owner_alert_battery_margin", "5"), ("mesh_owner_alert_combine_minutes", "10"), ("mesh_owner_alert_per_day", "6")):
        set_setting(k, v)
    uid = lambda n: d.execute("SELECT id FROM users WHERE username=%s", (n,)).fetchone()["id"]
    a, b = uid("plain1"), uid("claimer1")
    for t in ("mesh_alert_events", "mesh_alert_state", "mesh_alert_sends", "mesh_alert_prefs"):
        d.execute(f"DELETE FROM {t}")
    d.execute("DELETE FROM mesh_claims WHERE node_key LIKE '!aaaa%%'")
    d.execute("DELETE FROM mesh_accounts WHERE label = 'alerts-unit'")
    for i, o in ((1, a), (2, a), (3, b), (5, a)):
        d.execute("INSERT INTO mesh_claims (owner_id, node_key, code, status, claimed_at) VALUES (%s,%s,%s,'claimed', now())", (o, "!aaaa%04x" % i, "ARK-T%d" % i))
    d.execute("INSERT INTO mesh_accounts (owner_id, label, nodes) VALUES (%s,'alerts-unit','!aaaa0004, nonsense')", (b,))
    d.commit()
    ma._smtp = lambda: True
    rows = [node(1, seen=30), node(2, role="Client", bat=10), node(3, seen=2), node(4, seen=40), node(5, role="Client", bat=80)]   # 1 quiet router (a), 2 low battery (a), 3 fine (b), 4 quiet router named on b's login, 5 fine (a)
    n = run(0, *rows)
    ev = d.execute("SELECT user_id, node_key, kind FROM mesh_alert_events ORDER BY node_key").fetchall()
    out["first_pass"] = n == 3 and [(e["user_id"], e["node_key"], e["kind"]) for e in ev] == [(a, "!aaaa0001", "quiet"), (a, "!aaaa0002", "battery"), (b, "!aaaa0004", "quiet")]
    out["no_repeat_inside_cooldown"] = run(2, *rows) == 0
    out["repeat_after_cooldown"] = run(25, *rows) == 3 and d.execute("SELECT count(*) AS n FROM mesh_alert_events WHERE detail LIKE '%%(still)'").fetchone()["n"] == 3
    out["stops_after_most_repeats"] = run(50, *rows) == 3 and run(80, *rows) == 0   # the 2nd repeat, then no more
    out["unlisted_node_is_left_alone"] = run(100, *[node(3, seen=2)]) == 0 and d.execute("SELECT count(*) AS n FROM mesh_alert_state").fetchone()["n"] == 3
    # a battery just above the limit is not yet recovered; well above is
    d.execute("DELETE FROM mesh_alert_events")
    n1 = run(101, *[node(2, role="Client", bat=22)])
    n2 = run(102, *[node(2, role="Client", bat=40)])
    ev = d.execute("SELECT kind FROM mesh_alert_events ORDER BY id").fetchall()
    out["battery_hysteresis_and_back"] = n1 == 0 and n2 == 1 and [e["kind"] for e in ev] == ["back_battery"]
    out["router_back_note"] = run(103, *[node(1, seen=1)]) == 1 and d.execute("SELECT count(*) AS n FROM mesh_alert_events WHERE kind='back_quiet' AND user_id=%s", (a,)).fetchone()["n"] == 1
    # choices: battery off means no battery alert and no back note
    d.execute("INSERT INTO mesh_alert_prefs (user_id, battery) VALUES (%s, false) ON CONFLICT (user_id) DO UPDATE SET battery=false", (a,))
    d.execute("DELETE FROM mesh_alert_events")
    d.commit()
    out["pref_off_is_respected"] = run(110, *[node(2, role="Client", bat=5), node(1, seen=40)]) == 1 and d.execute("SELECT kind FROM mesh_alert_events").fetchone()["kind"] == "quiet"
    set_setting("mesh_owner_alerts", "0")
    out["switch_off_stops_everything"] = run(300, *[node(1, seen=400)]) == 0
    set_setting("mesh_owner_alerts", "1")
    # sending: one combined email per person, then nothing more; the daily limit holds the rest
    sent = []
    mailer.send_mail = lambda to, subject, body: (sent.append((to, subject, body)), (True, "ok"))[1]
    d.execute("DELETE FROM mesh_alert_events"); d.execute("DELETE FROM mesh_alert_state"); d.execute("DELETE FROM mesh_alert_sends")
    d.execute("DELETE FROM mesh_alert_prefs"); d.commit()
    run(400, *[node(1, seen=30), node(2, role="Client", bat=10)])
    out["not_sent_before_the_combine_wait"] = ma.send_pending(NOW + timedelta(hours=400, minutes=1)) == 0 and not sent
    s1 = ma.send_pending(NOW + timedelta(hours=400, minutes=20))
    mails = [m for m in sent if "Node 1" in m[2]]
    out["one_combined_email"] = s1 == 1 and len(mails) == 1 and "Node 2" in mails[0][2] and "Router gone quiet" in mails[0][2] and "Low battery" in mails[0][2]
    out["marked_sent_and_not_sent_twice"] = ma.send_pending(NOW + timedelta(hours=401)) == 0 and d.execute("SELECT count(*) AS n FROM mesh_alert_events WHERE email_state='pending'").fetchone()["n"] == 0
    set_setting("mesh_owner_alert_per_day", "1")
    run(430, *[node(1, seen=1)])   # the router is back: a 'back' note waits
    out["daily_limit_holds_the_rest"] = ma.send_pending(NOW + timedelta(hours=431)) == 0 and d.execute("SELECT count(*) AS n FROM mesh_alert_events WHERE email_state='pending'").fetchone()["n"] >= 1
    # email not set up: the alert is still listed, nothing waits to be emailed
    ma._smtp = lambda: False
    d.execute("DELETE FROM mesh_alert_events"); d.execute("DELETE FROM mesh_alert_state"); d.commit()
    run(500, *[node(1, seen=30)])
    out["without_email_set_up_it_is_listed_only"] = d.execute("SELECT email_state FROM mesh_alert_events WHERE user_id=%s", (a,)).fetchone()["email_state"] == "none"
    for t in ("mesh_alert_events", "mesh_alert_state", "mesh_alert_sends", "mesh_alert_prefs"):
        d.execute(f"DELETE FROM {t}")
    d.execute("DELETE FROM mesh_claims WHERE node_key LIKE '!aaaa%%'")
    d.execute("DELETE FROM mesh_accounts WHERE label = 'alerts-unit'")
    set_setting("mesh_owner_alert_per_day", "6")
    d.commit()
print(json.dumps(out))
