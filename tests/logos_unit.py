"""In-container checks of the airline logo service with a deliberately slow outside source: the request never waits for the source (it answers at once with an empty 204 and starts the fetch in the
background), the logo is there a few seconds later and then served from the database, a logo kept earlier is served while it refreshes, warming starts fetches for the airlines of the aircraft in the air
(a few at a time, not for ones already kept), and a source that fails is remembered as a miss.
docker cp this file to /tmp and run: docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/logos_unit.py"""
import os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"; os.environ["HUB_URL"] = ""
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from app import create_app
from app import lookups
from app.settings import set_setting

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")
HITS = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        HITS.append(self.path)
        if "/slow/" in self.path:
            time.sleep(4)   # an outside service having a bad day
        if "/none/" in self.path:
            self.send_response(404); self.end_headers(); return
        self.send_response(200); self.send_header("Content-Type", "image/png"); self.send_header("Content-Length", str(len(PNG))); self.end_headers(); self.wfile.write(PNG)


srv = ThreadingHTTPServer(("127.0.0.1", 18999), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()

app = create_app()
ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)

with app.app_context():
    from app.db import get_db
    d = get_db()
    d.execute("DELETE FROM airline_logos WHERE code IN ('ZZA','ZZB','ZZC','ZZD','ZZE','ZZF','ZZG','ZZH','ZZI','ZZJ')"); d.commit()
    set_setting("lookups_enabled", "1")
    set_setting("airline_logo_url", "http://127.0.0.1:18999/slow/{icao}.png")
    set_setting("airline_logo_days", "30")
c = app.test_client()
t0 = time.time()
r = c.get("/api/airline-logo/ZZA.png")
dt = time.time() - t0
check("an airline with no logo kept yet answers at once with an empty 204, not after the slow source", r.status_code == 204 and dt < 1.0 and r.data == b"", (r.status_code, round(dt, 2)))
check("and tells the browser to ask again soon", "max-age=15" in r.headers.get("Cache-Control", ""), r.headers.get("Cache-Control"))
time.sleep(0.6)
r = c.get("/api/airline-logo/ZZA.png")
check("asking again while the fetch is running does not start a second fetch", r.status_code == 204 and len([h for h in HITS if "ZZA" in h]) == 1, HITS)
time.sleep(6)
r = c.get("/api/airline-logo/ZZA.png")
check("a few seconds later the logo is served from the database with a long cache time", r.status_code == 200 and r.data == PNG and "max-age=604800" in r.headers.get("Cache-Control", ""), (r.status_code, r.headers.get("Cache-Control")))
n = len(HITS)
c.get("/api/airline-logo/ZZA.png")
check("it is not fetched again", len(HITS) == n)

with app.app_context():
    set_setting("airline_logo_url", "http://127.0.0.1:18999/none/{icao}.png")
c.get("/api/airline-logo/ZZB.png"); time.sleep(1.5)
r = c.get("/api/airline-logo/ZZB.png")
check("an airline the source does not know is kept as a miss and answers 204 with a longer cache time", r.status_code == 204 and "max-age=3600" in r.headers.get("Cache-Control", ""), (r.status_code, r.headers.get("Cache-Control")))

with app.app_context():
    set_setting("airline_logo_url", "http://127.0.0.1:18999/fast/{icao}.png")
    d = get_db()
    d.execute("UPDATE airline_logos SET fetched_at = now() - interval '90 days' WHERE code='ZZA'"); d.commit()
t0 = time.time()
r = c.get("/api/airline-logo/ZZA.png")
check("a changed source address makes kept logos stale; a stale one is not served for the new address until fetched", r.status_code == 204 and time.time() - t0 < 1.0, r.status_code)
time.sleep(1.5)
r = c.get("/api/airline-logo/ZZA.png")
check("and the new one is there a moment later", r.status_code == 200)

with app.app_context():
    d = get_db()
    d.execute("UPDATE airline_logos SET fetched_at = now() - interval '90 days' WHERE code='ZZA'"); d.commit(); lookups._logo_mem.clear()   # the test changed the row behind the memory cache
r = c.get("/api/airline-logo/ZZA.png")
check("a logo kept earlier for the same address is served while it refreshes", r.status_code == 200 and r.data == PNG)
time.sleep(4.5)   # the refresh finishes (generous: the test box is slow under load)

# warming from the aircraft in the air
with app.app_context():
    d = get_db()
    before = len(HITS)
    lookups.warm_logos(app, ["ZZC123", "ZZD45", "ZZE9", "ZZF1", "ZZG2", "ZZH3", "ZZI4", "ZZJ5", "ZZA77", "N123AB", "", None])
    time.sleep(2.5)
    got = d.execute("SELECT code FROM airline_logos WHERE code IN ('ZZC','ZZD','ZZE','ZZF','ZZG','ZZH','ZZI','ZZJ')").fetchall()
check("warming starts fetches for airlines not kept yet, six at a time, and skips private callsigns", 4 <= len(got) <= 6 and not any("N123" in h for h in HITS[before:]), (len(got), HITS[before:]))
check("an airline already kept is not fetched again by warming", not any("ZZA" in h for h in HITS[before:]), HITS[before:])

with app.app_context():
    d = get_db()
    d.execute("DELETE FROM airline_logos WHERE code LIKE 'ZZ_'")
    d.execute("DELETE FROM settings WHERE key IN ('airline_logo_url', 'airline_logo_days')"); d.commit()
srv.shutdown()
print("ALL PASS" if ok_all else "SOME FAILED")
