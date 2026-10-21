import os, sys
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
os.environ["HUB_URL"] = ""
import psycopg
from app import create_app

app = create_app()
url = app.config["DATABASE_URL"]
ip = sys.argv[1]
with psycopg.connect(url) as c:
    c.execute("DELETE FROM feeders WHERE uuid LIKE 'test%%'")
    c.execute("INSERT INTO feeders (uuid, first_seen, last_seen, last_ip, online) VALUES ('testaaaa-0000-4000-8000-000000000001', now(), now(), %s, TRUE)", (ip,))
    c.execute("INSERT INTO feeders (uuid, first_seen, last_seen, last_ip, online) VALUES ('testaaaa-0000-4000-8000-000000000002', now(), now(), %s, TRUE)", (ip,))
    c.execute("INSERT INTO feeders (uuid, first_seen, last_seen, last_ip, online) VALUES ('testbbbb-0000-4000-8000-000000000003', now()-interval '12 days', now()-interval '10 days', '9.9.9.9', FALSE)")
    c.execute("INSERT INTO feeders (uuid, first_seen, last_seen, last_ip, online) VALUES ('testcccc-0000-4000-8000-000000000004', now()-interval '2 days', now()-interval '2 days', '9.9.9.8', FALSE)")
    c.commit()
print("synthetic feeders created for ip", ip)
