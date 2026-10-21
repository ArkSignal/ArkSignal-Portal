"""In-container checks of sonde_history.py with a made-up archive: the months to look at (newest first, finished past months left out, the current month again after a day), a flight imported
thinned to the history interval and kept as an archive flight, flights already here left alone, flights the archive cannot give remembered (and not asked for again), one bad flight not stopping
the month, and a month marked done.
docker cp this file to /tmp and run: docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/history_unit.py"""
import os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"; os.environ["HUB_URL"] = ""
from datetime import datetime, timedelta, timezone
from app import create_app
from app import sonde_history as sh
from app.settings import set_setting

app = create_app()
ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)
iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.000000Z")

def frames(serial, start, n=1200, step=3):
    return [{"serial": serial, "datetime": iso(start + timedelta(seconds=k * step)), "lat": 34.83 + k * 0.0004, "lon": -92.26 + k * 0.0003, "alt": (170 + k * 5.0) if k < 800 else (4170 - (k - 800) * 9.0),
             "type": "RS41", "manufacturer": "Vaisala", "uploader_callsign": "AE5AU", "frame": k} for k in range(n)]

with app.app_context():
    from app.db import get_db
    d = get_db()
    for t in ("sonde_history_months", "sonde_history_skipped"):
        d.execute(f"DELETE FROM {t}")
    d.execute("DELETE FROM sonde_points WHERE serial LIKE 'HIST%'"); d.execute("DELETE FROM sonde_heard WHERE serial LIKE 'HIST%'"); d.execute("DELETE FROM sondes WHERE serial LIKE 'HIST%'"); d.commit()
    set_setting("sonde_history_sites", "72340, 72248"); set_setting("sonde_history_since", "2026-07-15"); set_setting("sonde_history_thin_seconds", "30"); set_setting("sonde_history_pause_seconds", "0")
    this = datetime.now(timezone.utc).strftime("%Y-%m")
    ms = sh.months("2026-07-15")
    check("the months run from the current one back to the start month, newest first", ms[0] == this and ms[-1] == "2026-07" and ms == sorted(ms, reverse=True), ms)
    check("the sites come from the setting", sh.sites() == ["72340", "72248"])
    p = sh.pending(d)
    check("at first every site and month is pending", len(p) == 2 * len(ms) and p[0] == ("72340", this))
    d.execute("INSERT INTO sonde_history_months (site, ym, done) VALUES ('72340', %s, now()), ('72248', %s, now())", (ms[-1], ms[-1])); d.commit()
    check("a finished past month is not looked at again", ("72340", ms[-1]) not in sh.pending(d))
    d.execute("INSERT INTO sonde_history_months (site, ym, done) VALUES ('72340', %s, now() - interval '2 days') ON CONFLICT (site, ym) DO UPDATE SET done = now() - interval '2 days'", (this,)); d.commit()
    check("the current month is looked at again after a day", ("72340", this) in sh.pending(d))
    d.execute("DELETE FROM sonde_history_months"); d.commit()

    start = datetime.now(timezone.utc) - timedelta(days=20)
    files = {"HIST0001": frames("HIST0001", start), "HIST0002": frames("HIST0002", start + timedelta(hours=12))}
    calls = []
    def fetch(serial):
        calls.append(serial)
        if serial == "HIST0003":
            return None, "not in the archive"
        if serial == "HIST0004":
            raise RuntimeError("boom")
        return files.get(serial), "" if serial in files else "not in the archive"
    check("a flight is imported, thinned to the history interval and kept as an archive flight", sh.import_flight(d, "HIST0001", fetch) == "imported")
    r = d.execute("SELECT source, status, type FROM sondes WHERE serial='HIST0001'").fetchone()
    npts = d.execute("SELECT COUNT(*) AS n FROM sonde_points WHERE serial='HIST0001'").fetchone()["n"]
    check("it is stored as a finished archive flight with its type", r and r["source"] == "archive" and r["status"] in ("landed", "lost") and r["type"] == "RS41", r)
    check("one position every 30 seconds: 1200 frames 3 s apart are about 120 points", 100 <= npts <= 140, npts)
    check("a flight already here is left alone", sh.import_flight(d, "HIST0001", fetch) == "have" and calls == ["HIST0001"])
    check("a flight the archive cannot give is skipped and remembered", sh.import_flight(d, "HIST0003", fetch) == "skipped" and d.execute("SELECT 1 FROM sonde_history_skipped WHERE serial='HIST0003'").fetchone() is not None)
    n0 = len(calls)
    check("and not asked for again for a month", sh.import_flight(d, "HIST0003", fetch) == "skipped" and len(calls) == n0)

    sh.STATE.update(imported=0, skipped=0, errors=0)
    new, done = sh.step_month(d, "72340", ms[-1], lister=lambda s, m: ["HIST0001", "HIST0002", "HIST0003", "HIST0004"], fetch=fetch, pause=0)
    check("a month brings in what is missing, skips what it cannot get and survives a bad flight", new == 1 and done and sh.STATE["imported"] == 1 and sh.STATE["errors"] == 1, (new, sh.STATE))
    check("the month is marked done", d.execute("SELECT flights FROM sonde_history_months WHERE site='72340' AND ym=%s", (ms[-1],)).fetchone()["flights"] == 1)
    check("the counts say how many archive flights there are and how far it has got", sh.counts(d)["flights"] >= 2 and sh.counts(d)["months_done"] == 1)
    new, done = sh.step_month(d, "72248", ms[-1], lister=lambda s, m: ["HIST0002"], fetch=fetch, pause=0, should_stop=lambda: True)
    check("switching the job off stops it before the next flight and leaves the month unfinished", new == 0 and not done and d.execute("SELECT 1 FROM sonde_history_months WHERE site='72248'").fetchone() is None)

    try:   # the real archive: one past month of Little Rock launches (skipped, not failed, with no network)
        real = sh.list_serials("72340", "2025-06")
        check("the real archive lists about a month of Little Rock launches", len(real) >= 30 and all(len(x) >= 5 for x in real), len(real))
    except OSError as e:
        print("note: the archive could not be reached, real listing check skipped", e)
    d.execute("DELETE FROM sonde_points WHERE serial LIKE 'HIST%'"); d.execute("DELETE FROM sonde_heard WHERE serial LIKE 'HIST%'"); d.execute("DELETE FROM sondes WHERE serial LIKE 'HIST%'")
    d.execute("DELETE FROM sonde_history_months"); d.execute("DELETE FROM sonde_history_skipped")
    d.execute("DELETE FROM settings WHERE key IN ('sonde_history_sites','sonde_history_since','sonde_history_thin_seconds','sonde_history_pause_seconds')"); d.commit()
print("ALL PASS" if ok_all else "SOME FAILED")
