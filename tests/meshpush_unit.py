"""Runs inside the container (tests/pushmore_test.py): mesh node alerts by phone or computer notification, with the push sender replaced by a recorder. Prints one JSON line."""
import os, json
from datetime import datetime, timedelta, timezone
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
os.environ["DISABLE_MESH_SYNC"] = "1"
from app import create_app
app = create_app()
from app import mesh_alerts as ma, webpush, vault
from app.db import get_db
from app.settings import set_setting
out = {}
NOW = datetime.now(timezone.utc)
iso = lambda h: (NOW - timedelta(hours=h)).isoformat().replace("+00:00", "Z")
node = lambda i, role="Router", seen=1, bat=None: {"id": "!bbbb%04x" % i, "role_name": role, "battery": bat, "long_name": "Push Node %d" % i, "last_seen": iso(seen)}
calls = []
webpush.send = lambda sub, payload, ttl=None, urgency="normal": (calls.append((sub["user_id"], payload, urgency)), (True, 201))[1]
with app.app_context():
    d = get_db()
    for k, v in (("mesh_enabled", "1"), ("mesh_owner_alerts", "1"), ("mesh_offline_hours", "24"), ("mesh_low_battery_pct", "20"), ("mesh_owner_alert_combine_minutes", "10"), ("mesh_owner_alert_per_day", "6"), ("push_enabled", "1")):
        set_setting(k, v)
    uid = lambda n: d.execute("SELECT id FROM users WHERE username=%s", (n,)).fetchone()["id"]
    a, b = uid("plain1"), uid("claimer1")
    for t in ("mesh_alert_events", "mesh_alert_state", "mesh_alert_sends", "mesh_alert_prefs"):
        d.execute(f"DELETE FROM {t}")
    d.execute("DELETE FROM mesh_claims WHERE node_key LIKE '!bbbb%%'")
    d.execute("DELETE FROM push_subscriptions WHERE user_id = ANY(%s)", ([a, b],))
    for i, o in ((1, a), (2, a), (3, b)):
        d.execute("INSERT INTO mesh_claims (owner_id, node_key, code, status, claimed_at) VALUES (%s,%s,%s,'claimed', now())", (o, "!bbbb%04x" % i, "ARK-P%d" % i))
    d.execute("INSERT INTO push_subscriptions (user_id, endpoint, p256dh_sealed, auth_sealed, pushkey_hash, device) VALUES (%s,'https://fcm.googleapis.com/x',%s,%s,'h1','t')", (a, vault.seal("x"), vault.seal("y")))
    d.execute("INSERT INTO mesh_alert_prefs (user_id, by_email, by_push) VALUES (%s,false,true), (%s,false,true) ON CONFLICT (user_id) DO UPDATE SET by_email=false, by_push=true", (a, b))
    d.commit()
    ma._smtp = lambda: False
    rows = [node(1, seen=30), node(2, role="Client", bat=10), node(3, seen=30)]   # a: quiet router and low battery; b: quiet router but no device
    out["made"] = ma.run_pass(NOW, rows)
    out["pending"] = d.execute("SELECT count(*) AS n FROM mesh_alert_events WHERE push_state='pending'").fetchone()["n"] == 3
    out["waits_for_combine"] = ma.send_pending(NOW + timedelta(minutes=1)) == 0 and not calls
    sent = ma.send_pending(NOW + timedelta(minutes=20))
    mine = [c for c in calls if c[0] == a]
    out["one_combined_notice"] = sent == 1 and len(mine) == 1 and mine[0][1]["title"] == "2 mesh node alerts" and "Push Node 1" in mine[0][1]["body"] and mine[0][1]["url"] == "/my/mesh" and mine[0][2] == "high"
    st = {r["user_id"]: r["push_state"] for r in d.execute("SELECT DISTINCT user_id, push_state FROM mesh_alert_events").fetchall()}
    out["states"] = st.get(a) == "sent" and st.get(b) == "failed"
    out["not_sent_twice"] = ma.send_pending(NOW + timedelta(minutes=30)) == 0 and len([c for c in calls if c[0] == a]) == 1
    d.execute("DELETE FROM mesh_alert_events"); d.execute("DELETE FROM mesh_alert_state"); d.execute("DELETE FROM mesh_alert_sends")
    d.execute("UPDATE mesh_alert_prefs SET by_push=false"); d.commit()
    ma.run_pass(NOW + timedelta(hours=50), [dict(node(1, seen=30), last_seen=iso(-20))])
    out["choice_off_means_none"] = d.execute("SELECT count(*) AS n FROM mesh_alert_events WHERE push_state<>'none'").fetchone()["n"] == 0
    for t in ("mesh_alert_events", "mesh_alert_state", "mesh_alert_sends", "mesh_alert_prefs"):
        d.execute(f"DELETE FROM {t}")
    d.execute("DELETE FROM mesh_claims WHERE node_key LIKE '!bbbb%%'")
    d.execute("DELETE FROM push_subscriptions WHERE user_id = ANY(%s)", ([a, b],))
    d.commit()
print(json.dumps(out))
