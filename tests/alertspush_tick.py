"""Runs inside the container (tests/alertspush_test.py): one alert pass with a synthetic aircraft, then the notification pass. Prints one JSON line with the event states."""
import json
import os
import sys

os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
os.environ["HUB_URL"] = ""
import psycopg
from psycopg.rows import dict_row
from app import alerts as al
from app import create_app

app = create_app()
AC = [{"hex": "ae9001", "flight": "PUSH1", "t": "C17", "alt_baro": 20000, "lat": 35.0, "lon": -92.0, "squawk": "4001", "dbFlags": 1}]
if len(sys.argv) > 1 and sys.argv[1] == "send":
    out = {"sent": al.send_pushes(app)}
else:
    out = {"events": al.run_tick(app, aircraft=AC)}
with psycopg.connect(app.config["DATABASE_URL"], row_factory=dict_row) as c:
    out["states"] = [r["push_state"] for r in c.execute("SELECT e.push_state FROM alert_events e JOIN users u ON u.id = e.user_id WHERE u.username LIKE 'pushal%' ORDER BY e.id").fetchall()]
    out["pushes"] = [dict(events=r["events"], ok=r["ok"]) for r in c.execute("SELECT p.events, p.ok FROM alert_pushes p JOIN users u ON u.id = p.user_id WHERE u.username LIKE 'pushal%' ORDER BY p.id").fetchall()]
print(json.dumps(out))
