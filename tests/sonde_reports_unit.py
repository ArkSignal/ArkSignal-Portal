"""In-container checks of sonde_reports.py: the exact formats sent to SondeHub (PUT /recovered and PUT /listeners as its API reference and the tracker/ChaseMapper use them), the headers, the
retry rule (only 50x answers), and what is stored. docker cp this file to /tmp and run: docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/sonde_reports_unit.py"""
import os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"; os.environ["HUB_URL"] = ""
import io, json, re, urllib.error
from types import SimpleNamespace
from app import create_app
from app import sondes as sd
from app import sonde_reports as sr
from app.settings import set_setting

app = create_app()
ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)

with app.app_context():
    from app.db import get_db
    d = get_db()
    d.execute("DELETE FROM sonde_recoveries WHERE serial='UNIT1'"); d.execute("DELETE FROM sondes WHERE serial='UNIT1'"); d.execute("DELETE FROM sonde_chase_share WHERE user_id=987654")
    d.execute("INSERT INTO sondes (serial, kind, type, first_seen, last_seen, status, last_lat, last_lon, last_alt, landing_lat, landing_lon, landing_alt) VALUES ('UNIT1','radiosonde','RS41', now(), now(), 'landed', 35.1, -92.2, 120, 35.1, -92.2, 118.5)")
    d.commit()
    me = SimpleNamespace(id=987654, callsign="kd5vcf-lr", display_name="Marty", username="marty")

    # ---- the recovery format
    sent = []
    real = sr.put_json
    sr.put_json = lambda path, payload, timeout=15: (sent.append((path, payload)) or (True, "SondeHub answered 200"))
    set_setting("sonde_report_mode", "1")
    ok, msg = sr.report_recovery(me, "unit1", True, True, "", "  found it  ", 35.12345678, -92.2, None)
    path, p = sent[0]
    check("a report goes to /recovered with exactly the fields SondeHub documents", ok and path == "/recovered" and set(p) == {"serial", "lat", "lon", "alt", "recovered", "planned", "recovered_by", "description", "recovery_software"}, (ok, msg, sent))
    check("the serial is the flight's own, position rounded to 6 places, altitude defaults to the landing altitude in meters, text trimmed", p["serial"] == "UNIT1" and p["lat"] == 35.123457 and p["lon"] == -92.2 and p["alt"] == 118.5 and p["description"] == "found it", p)
    check("recovered wins over planned (SondeHub's own form clears planned), both are real booleans", p["recovered"] is True and p["planned"] is False)
    check("recovered_by defaults to the member's call sign and the software names ArkSignal with its version", p["recovered_by"] == "kd5vcf-lr" and p["recovery_software"].startswith("ArkSignal "), p)
    row = d.execute("SELECT * FROM sonde_recoveries WHERE serial='UNIT1'").fetchone()
    check("it is kept locally as a portal report that was sent", row and row["source"] == "portal" and row["sent"] == "sent" and row["user_id"] == 987654 and row["recovered"], dict(row) if row else None)
    sent.clear(); d.execute("DELETE FROM sonde_recoveries WHERE serial='UNIT1'"); d.commit()
    ok, msg = sr.report_recovery(me, "UNIT1", False, True, "Marty", "later", 35.1, -92.2, 100)
    check("planned (not recovered) keeps planned true", ok and sent[0][1]["planned"] is True and sent[0][1]["recovered"] is False, sent)
    check("the state words", sr.state_of(True, False) == "recovered" and sr.state_of(False, True) == "planned" and sr.state_of(False, False) == "notfound" and sr.state_for("UNIT1") == "planned")
    # SondeHub refuses: nothing is saved
    d.execute("DELETE FROM sonde_recoveries WHERE serial='UNIT1'"); d.commit()
    sr.put_json = lambda path, payload, timeout=15: (False, "SondeHub answered 500")
    ok, msg = sr.report_recovery(me, "UNIT1", True, False, "Marty", "", 35.1, -92.2, 100)
    check("when SondeHub does not take the report nothing is saved and the member is told", (not ok) and "Nothing was saved" in msg and d.execute("SELECT COUNT(*) AS n FROM sonde_recoveries WHERE serial='UNIT1'").fetchone()["n"] == 0, msg)
    # test mode sends nothing
    sent.clear(); sr.put_json = lambda path, payload, timeout=15: (sent.append(path) or (True, ""))
    set_setting("sonde_report_mode", "test")
    ok, msg = sr.report_recovery(me, "UNIT1", True, False, "Marty", "", 35.1, -92.2, 100)
    check("test mode keeps the report here and sends nothing", ok and sent == [] and "nothing was sent" in msg and d.execute("SELECT sent FROM sonde_recoveries WHERE serial='UNIT1'").fetchone()["sent"] == "test", msg)
    # SondeHub's copy replaces ours
    d.execute("UPDATE sonde_recoveries SET sent='sent' WHERE serial='UNIT1'"); d.commit()
    import psycopg
    from psycopg.rows import dict_row
    conn = psycopg.connect(app.config["DATABASE_URL"], row_factory=dict_row)
    from datetime import datetime, timezone
    sd.fetch_recoveries(conn, raw=[{"serial": "UNIT1", "recovered": True, "recovered_by": "Marty", "description": "", "datetime": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z"), "lat": 35.1, "lon": -92.2, "alt": 100, "recovery_software": "ArkSignal"}])
    rows = d.execute("SELECT source FROM sonde_recoveries WHERE serial='UNIT1'").fetchall()
    check("when SondeHub's own copy comes back in the list, ours is dropped so the report shows once", len(rows) == 1 and rows[0]["source"] == "sondehub", [dict(r) for r in rows])
    sr.put_json = real

    # ---- the chase car format
    d.execute("DELETE FROM sonde_chase_share WHERE user_id=987654"); d.commit()
    sent2 = []
    sr.put_json = lambda path, payload, timeout=15: (sent2.append((path, payload)) or (True, ""))
    set_setting("sonde_chase_mode", "1")
    ok, msg = sr.share_position(me, 35.1234567, -92.7654321, 101.26)
    path, p = sent2[0]
    check("a chase car goes to /listeners as a mobile listener named CALLSIGN_chase with a [lat, lon, alt] position", ok and path == "/listeners" and p["mobile"] is True and p["uploader_callsign"] == "KD5VCF_chase" and p["uploader_position"] == [35.123457, -92.765432, 101.3], (msg, sent2))
    check("and carries the software name, version, radio and antenna fields", all(k in p for k in ("software_name", "software_version", "uploader_radio", "uploader_antenna")) and p["software_name"] == "ArkSignal")
    ok2, msg2 = sr.share_position(me, 35.2, -92.7, 100)
    check("a second update inside the minimum interval is not sent", ok2 and len(sent2) == 1 and "Too soon" in msg2)
    check("no call sign, no sharing; a bad position is refused", sr.share_position(SimpleNamespace(id=1, callsign="", display_name="", username="x"), 35, -92, 1)[0] is False and sr.share_position(me, 99, 0, 1)[0] is False)
    d.execute("UPDATE sonde_chase_share SET started = now() - interval '9 hours', last_ts = now() - interval '1 minute' WHERE user_id=987654"); d.commit()
    check("sharing stops after the longest time allowed", sr.share_position(me, 35.1, -92.7, 100)[0] is False)
    sr.put_json = real

    # ---- the HTTP call itself: method, headers, retry on 50x only
    calls = []
    class Resp:
        def __init__(self, status): self.status = status
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def fake_open(req, timeout=0):
        calls.append(req)
        code = codes.pop(0)
        if code == 200: return Resp(200)
        raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(b""))
    import urllib.request, time
    real_open, real_sleep = urllib.request.urlopen, time.sleep
    urllib.request.urlopen = fake_open; sr.time.sleep = lambda s: None
    codes = [200]
    ok, msg = sr.put_json("/recovered", {"a": 1})
    req = calls[0]
    check("a PUT with JSON, a Date header in RFC 7231 form and an identifying User-Agent", ok and req.get_method() == "PUT" and req.full_url.endswith("/recovered") and re.fullmatch(r"[A-Z][a-z]{2}, \d\d [A-Z][a-z]{2} \d{4} \d\d:\d\d:\d\d GMT", req.get_header("Date")) and req.get_header("User-agent").startswith("ArkSignal/") and req.get_header("Content-type") == "application/json" and json.loads(req.data) == {"a": 1}, (req.headers, req.get_method()))
    calls.clear(); codes = [500, 503, 200]
    ok, msg = sr.put_json("/recovered", {})
    check("a 50x answer is retried, and a later 200 counts", ok and len(calls) == 3)
    calls.clear(); codes = [400, 200]
    ok, msg = sr.put_json("/recovered", {})
    check("a 4xx answer is not retried", (not ok) and len(calls) == 1 and "400" in msg)
    calls.clear(); codes = [500, 500, 500]
    ok, msg = sr.put_json("/recovered", {})
    check("it gives up after three tries", (not ok) and len(calls) == 3)
    urllib.request.urlopen, sr.time.sleep = real_open, real_sleep

    d.execute("DELETE FROM sonde_recoveries WHERE serial='UNIT1'"); d.execute("DELETE FROM sondes WHERE serial='UNIT1'"); d.execute("DELETE FROM sonde_chase_share WHERE user_id=987654")
    d.execute("DELETE FROM settings WHERE key IN ('sonde_report_mode','sonde_chase_mode')"); d.commit()
print("ALL PASS" if ok_all else "SOME FAILED")
