"""Runs inside the portal container (tests/meshalertchan_test.py): the alert and weather channel with the Weather Alerts layer's data replaced by fixed warnings, sending through the REAL mesh service and broker. Prints one
JSON line. Arguments: the community's address."""
import os, sys, json, time
from datetime import datetime, timedelta, timezone
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
os.environ["DISABLE_MESH_SYNC"] = "1"
from app import create_app
app = create_app()
from app import meshalertchan as ma, wxalerts
from app.db import get_db
from app.settings import set_setting
slug = sys.argv[1]
out = {}
with app.app_context():
    d = get_db()
    for k, v in (("mesh_enabled", "1"), ("meshalert_enabled", "1"), ("meshalert_codes", "TOR,SVR,FLD"), ("meshalert_weather_hours", "6"), ("meshalert_daily_cap", "24"), ("meshalert_min_gap_seconds", "1"), ("meshalert_station_minutes", "90")):
        set_setting(k, v)
    c = lambda: d.execute("SELECT * FROM communities WHERE slug=%s", (slug,)).fetchone()
    cid = c()["id"]
    d.execute("DELETE FROM mesh_chan_log WHERE community_id=%s", (cid,))
    d.execute("DELETE FROM wx_stations WHERE name LIKE 'MAC Test %%'")
    ENDS = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    sq = lambda la, lo, h: [[lo - h, la - h], [lo + h, la - h], [lo + h, la + h], [lo - h, la + h], [lo - h, la - h]]
    feat = lambda kind, ev, ring, area="Pulaski, Saline": {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": {"event": ev, "KIND": kind, "headline": ev + " issued", "area": area, "ends": ENDS}}
    wxalerts.geojson = lambda: {"type": "FeatureCollection", "features": [feat("TOR", "Tornado Warning", sq(34.75, -92.0, 0.5)), feat("FLD", "Flood Warning", sq(40.0, -100.0, 0.3), "Far Away County"), feat("HOT", "Heat Advisory", sq(34.75, -92.0, 0.5)),
                                                                          feat("SVR", "Severe Thunderstorm Warning " + "x" * 400, sq(34.7, -92.1, 0.2), "Faulkner")]}
    # the public weather stations: two inside the area (reporting), one outside, one hidden
    def station(name, lat, lon, temp, privacy="general", ago=5):
        r = d.execute("INSERT INTO wx_stations (name, lat, lon, privacy, kind, token_hash, public) VALUES (%s,%s,%s,%s,'pws',%s,true) RETURNING id", (name, lat, lon, privacy, "mac" + name + str(time.time()))).fetchone()
        d.execute("INSERT INTO wx_readings (station_pk, ts, temp_f, hum, wind_mph, gust_mph, rain_day_in) VALUES (%s, now() - make_interval(mins => %s), %s, 60, 8, 15, 0.2)", (r["id"], ago, temp))
    station("MAC Test A", 34.7, -92.3, 70); station("MAC Test B", 34.8, -92.1, 74); station("MAC Test Far", 40.0, -100.0, 20); station("MAC Test Hidden", 34.75, -92.0, 99, privacy="hidden"); station("MAC Test Old", 34.75, -92.0, 99, ago=600)
    d.commit()
    # the site switch off, and the community's own switch off: nothing is sent
    set_setting("meshalert_enabled", "0")
    d.execute("UPDATE communities SET alert_enabled=true WHERE slug=%s", (slug,)); d.commit()
    out["site_switch_off_sends_nothing"] = ma.watch_pass() == 0
    set_setting("meshalert_enabled", "1")
    d.execute("UPDATE communities SET alert_enabled=false WHERE slug=%s", (slug,)); d.commit()
    out["community_switch_off_sends_nothing"] = ma.watch_pass() == 0
    d.execute("UPDATE communities SET alert_enabled=true WHERE slug=%s", (slug,)); d.commit()
    # the text of what would go out
    al = ma.alerts_for(c())
    out["alerts_for_area_only_and_kinds_only"] = sorted(a["text"][:7] for a in al) == ["Severe ", "Tornado"]
    out["every_text_fits"] = all(len(a["text"].encode()) <= 200 for a in al) and any(a["text"].endswith("...") or "Source: NWS" in a["text"] for a in al)
    ws = ma.weather_summary(c())
    out["weather_summary_from_stations_in_area"] = ws.startswith("Weather ") and "72F (70 to 74)" in ws and "(2 stations)" in ws and "wind 8 mph, gusts 15" in ws and "humidity 60%" in ws and len(ws.encode()) <= 200
    out["debug_ws"] = ws
    # one pass: the warnings that cover the area, then the summary (the gap is one second, so passes are spaced)
    s1 = ma.watch_pass(); time.sleep(1.5)
    s1 += ma.watch_pass(); time.sleep(1.5)
    s1 += ma.watch_pass()
    rows = [dict(r) for r in d.execute("SELECT kind, ok, text, detail FROM mesh_chan_log WHERE community_id=%s ORDER BY id", (cid,)).fetchall()]
    out["sent_the_two_warnings_and_one_summary"] = s1 == 3 and sorted(r["kind"] for r in rows if r["ok"]) == ["nws", "nws", "weather"]
    out["debug_rows"] = [s1, rows]
    out["each_warning_once_and_the_summary_once"] = ma.watch_pass() == 0 and d.execute("SELECT COUNT(*) AS n FROM mesh_chan_log WHERE community_id=%s", (cid,)).fetchone()["n"] == 3
    out["audit_entry_for_each_send"] = d.execute("SELECT COUNT(*) AS n FROM audit_log WHERE action='mesh.alert_send' AND target=%s AND ts > now() - interval '2 minutes'", (slug,)).fetchone()["n"] >= 3
    # a new warning appears later: sent on the next pass
    wxalerts.geojson = lambda: {"type": "FeatureCollection", "features": [feat("TOR", "Tornado Warning", sq(34.75, -92.0, 0.5)), feat("FLD", "Flood Warning", sq(34.8, -92.0, 0.1), "Pulaski")]}
    time.sleep(1.5)
    out["a_new_warning_is_sent_later"] = ma.watch_pass() == 1
    # the minimum gap holds a second message back until it has passed
    set_setting("meshalert_min_gap_seconds", "3600")
    wxalerts.geojson = lambda: {"type": "FeatureCollection", "features": [feat("SVR", "Severe Thunderstorm Warning", sq(34.75, -92.0, 0.5), "Conway")]}
    out["the_gap_holds_a_message_back_and_it_is_not_logged_as_failed"] = ma.watch_pass() == 0 and d.execute("SELECT COUNT(*) AS n FROM mesh_chan_log WHERE community_id=%s AND NOT ok", (cid,)).fetchone()["n"] == 0
    set_setting("meshalert_min_gap_seconds", "1"); time.sleep(1.5)
    # the daily cap
    n_ok = d.execute("SELECT COUNT(*) AS n FROM mesh_chan_log WHERE community_id=%s AND ok", (cid,)).fetchone()["n"]
    set_setting("meshalert_daily_cap", str(n_ok))
    out["the_daily_cap_holds_messages_back_and_says_so"] = ma.watch_pass() == 0 and d.execute("SELECT COUNT(*) AS n FROM mesh_chan_log WHERE community_id=%s AND NOT ok AND detail LIKE 'held back%%'", (cid,)).fetchone()["n"] >= 1
    set_setting("meshalert_daily_cap", "24")
    # the channel removed: nothing to send on
    d.execute("UPDATE communities SET alert_channel_id=NULL WHERE slug=%s", (slug,)); d.commit()
    out["no_channel_sends_nothing"] = ma.watch_pass() == 0
    d.execute("DELETE FROM wx_stations WHERE name LIKE 'MAC Test %%'")
    d.execute("DELETE FROM settings WHERE key IN ('meshalert_enabled', 'meshalert_codes', 'meshalert_weather_hours', 'meshalert_daily_cap', 'meshalert_min_gap_seconds', 'meshalert_station_minutes')")   # back to the built-in values (the test used values below the allowed range)
    d.commit()
print(json.dumps(out, default=str))
