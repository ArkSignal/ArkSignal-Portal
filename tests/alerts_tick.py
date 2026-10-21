"""Runs inside the container: unit-tests alerts.matches and drives run_tick with synthetic aircraft.
Prints one JSON line with results for the host-side test to check."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
os.environ["HUB_URL"] = ""
import psycopg
from psycopg.rows import dict_row
from app import alerts as al
from app import create_app

app = create_app()
AC = [
    {"hex": "a1b2c3", "flight": "AAL123  ", "r": "N123AA", "t": "B738", "alt_baro": 35000, "lat": 34.8, "lon": -92.3, "squawk": "1200", "dbFlags": 0},
    {"hex": "ae1234", "flight": "RCH45", "t": "C17", "alt_baro": 20000, "lat": 35.0, "lon": -92.0, "squawk": "4001", "dbFlags": 1},
    {"hex": "c0ffee", "flight": "EMG1", "alt_baro": 8000, "lat": 36.0, "lon": -94.0, "squawk": "7700", "emergency": "general"},
    {"hex": "beef01", "alt_baro": "ground", "lat": 34.73, "lon": -92.22, "squawk": "1200"},
    {"hex": "00face", "flight": "UAL9", "alt_baro": 30000, "squawk": "4321"},
]
R = lambda **k: dict({"kind": "any", "value": "", "area_lat": None, "area_lon": None, "area_radius_nm": None, "alt_min_ft": None, "alt_max_ft": None}, **k)
unit = {
    "icao": [a["hex"] for a in AC if al.matches(R(kind="icao", value="A1B2C3"), a)],
    "callsign": [a["hex"] for a in AC if al.matches(R(kind="callsign_prefix", value="AAL"), a)],
    "military": [a["hex"] for a in AC if al.matches(R(kind="military"), a)],
    "emergency": [a["hex"] for a in AC if al.matches(R(kind="emergency"), a)],
    "squawk": [a["hex"] for a in AC if al.matches(R(kind="squawk", value="4321"), a)],
    "type": [a["hex"] for a in AC if al.matches(R(kind="type", value="C17"), a)],
    "registration": [a["hex"] for a in AC if al.matches(R(kind="registration", value="N123AA"), a)],
    "area_klit_5nm": [a["hex"] for a in AC if al.matches(R(area_lat=34.75, area_lon=-92.29, area_radius_nm=5), a)],
    "alt_le_1000": [a["hex"] for a in AC if al.matches(R(alt_max_ft=1000), a)],
    "alt_ge_30000": [a["hex"] for a in AC if al.matches(R(alt_min_ft=30000), a)],
    "no_position_never_in_area": [a["hex"] for a in AC if a["hex"] == "00face" and al.matches(R(area_lat=34.75, area_lon=-92.29, area_radius_nm=500), a)],
}
now = datetime.now(timezone.utc)
HEXES = [a["hex"] for a in AC]


def synthetic_count():
    with psycopg.connect(app.config["DATABASE_URL"]) as c:
        return c.execute("SELECT COUNT(*) FROM alert_events WHERE hex = ANY(%s)", (HEXES,)).fetchone()[0]


al.run_tick(app, now=now, aircraft=AC)
c1 = synthetic_count()
al.run_tick(app, now=now + timedelta(seconds=15), aircraft=AC)      # inside cooldown: nothing new
c2 = synthetic_count()
al.run_tick(app, now=now + timedelta(minutes=45), aircraft=AC)      # after the 30 minute cooldown: repeats
c3 = synthetic_count()
with psycopg.connect(app.config["DATABASE_URL"], row_factory=dict_row) as c:
    per_rule = {r["name"]: r["n"] for r in c.execute(
        "SELECT r.name, COUNT(e.id) AS n FROM alert_rules r LEFT JOIN alert_events e ON e.rule_id = r.id AND e.hex = ANY(%s) GROUP BY r.name", (HEXES,)).fetchall()}
print(json.dumps({"unit": unit, "counts": [c1, c2, c3], "per_rule": per_rule}))
