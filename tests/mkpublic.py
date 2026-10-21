import os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
os.environ["HUB_URL"] = ""
import psycopg
from app import create_app

app = create_app()
with psycopg.connect(app.config["DATABASE_URL"]) as c:
    c.execute("DELETE FROM feeders WHERE uuid LIKE 'pub%%'")
    owner1 = c.execute("SELECT id FROM users WHERE username='claimer1'").fetchone()[0]
    owner2 = c.execute("SELECT id FROM users WHERE username='claimer2'").fetchone()[0]
    # uuid, station, name, owner, lat, lon, privacy, banned, online, pos/s, msg/s, uptime_fraction (share of last 24h with samples)
    specs = [
        ("pubaaaa1-0000-4000-8000-000000000001", "PUB-ALPHA", "Alpha Station", owner1, 34.315231, -92.410357, "general", False, True, 20.0, 60.0, 1.0),
        ("pubaaaa2-0000-4000-8000-000000000002", "PUB-BRAVO", "Bravo Station", owner2, 36.062411, -94.157338, "exact", False, True, 10.0, 30.0, 0.5),
        ("pubaaaa3-0000-4000-8000-000000000003", "PUB-CHARLIE", "Charlie Hidden", owner1, 35.123456, -92.654321, "hidden", False, False, 40.0, 90.0, 0.25),
        ("pubaaaa4-0000-4000-8000-000000000004", "PUB-DELTA", "Delta Banned", owner2, 34.5, -92.5, "general", True, True, 99.0, 99.0, 1.0),
        ("pubaaaa5-0000-4000-8000-000000000005", None, "No Station ID Yet", owner2, 34.6, -92.6, "general", False, True, 5.0, 5.0, 1.0),
        ("pubaaaa6-0000-4000-8000-000000000006", None, None, None, 34.7, -92.7, None, False, True, 5.0, 5.0, 1.0),
    ]
    for uuid, sid, name, owner, lat, lon, privacy, banned, online, pos, msg, up in specs:
        c.execute("INSERT INTO feeders (uuid, station_id, name, owner_id, lat, lon, privacy, banned, online, last_ip, first_seen, last_seen, "
                  "setup_receiver, notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'203.0.113.77', now() - interval '3 days', "
                  "CASE WHEN %s THEN now() ELSE now() - interval '2 hours' END, 'Test SDR', 'PRIVATE NOTE TEXT')",
                  (uuid, sid, name, owner, lat, lon, privacy, banned, online, online))
        # samples every 30 s over the covered fraction of the last 24 h
        c.execute("INSERT INTO feeder_samples (feeder_uuid, ts, msg_per_s, pos_per_s, conn_secs, positions, msg_total) "
                  "SELECT %s, now() - (g * interval '30 seconds'), %s, %s, g, g, g FROM generate_series(%s::int, %s::int) g",
                  (uuid, msg, pos, 0 if online else 240, int(2880 * up) + (0 if online else 240)))
    c.commit()
print("public test feeders created")
