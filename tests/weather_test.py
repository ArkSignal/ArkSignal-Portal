import json, re, subprocess, threading, time, requests
from http.server import BaseHTTPRequestHandler, HTTPServer
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
D = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker "
def ssh(cmd): return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True).stdout
def psql(sql): return ssh(D + f"exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\"").strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
def csrf(s, path): return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + path).text).group(1)

# ---- the in-container unit checks: parsers, units, storing, roll-up, charts, and the flight-path leg split
for unit in ("weather_unit.py", "traceleg_unit.py"):
    subprocess.run(["scp", "-q", unit, f"nastemp:adsb-portal/{unit}"], check=True, capture_output=True)
    ssh(D + f"cp ~/adsb-portal/{unit} adsb-portal:/tmp/{unit}")
    out = ssh(D + f"exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/{unit}")
    check(f"{unit} passes inside the portal container", "ALL PASS" in out and "FAIL" not in out, out[-400:])

# ---- a fake Ecowitt.net and Tempest on this PC (the portal container reaches it at test-box.invalid)
HOST, PORT = "test-box.invalid", 2541
MODE = {"ecowitt": "ok", "hits": []}
ECO = {"code": 0, "msg": "success", "data": {"outdoor": {"temperature": {"unit": "℃", "value": "20"}, "humidity": {"unit": "%", "value": "60"}},
       "wind": {"wind_speed": {"unit": "km/h", "value": "36"}, "wind_direction": {"unit": "º", "value": "90"}}, "pressure": {"relative": {"unit": "hPa", "value": "1013.25"}},
       "rainfall": {"daily": {"unit": "mm", "value": "25.4"}}}}
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        MODE["hits"].append(self.path)
        if self.path.startswith("/ecowitt"):
            body = ECO if MODE["ecowitt"] == "ok" else {"code": 40010, "msg": "Illegal Application_Key"}
        else:
            body = {"obs": [{"air_temperature": 10.0, "relative_humidity": 80, "barometric_pressure": 1000.0, "wind_avg": 2.0, "wind_direction": 45}]}
            if "Bearer tok-tempest" not in (self.headers.get("Authorization") or ""):
                self.send_response(401); self.end_headers(); return
        data = json.dumps(body).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(data)
    def log_message(self, *a): pass
srv = HTTPServer(("0.0.0.0", PORT), H); threading.Thread(target=srv.serve_forever, daemon=True).start()

anon = requests.Session(); adm = sess("marty", PW); me = sess("plain1", "plainpassword1")
other = None
try:
    other = sess("claimer1", "claimerpassword1")
except Exception:
    other = None
psql("DELETE FROM wx_stations")
for k, v in (("wx_enabled", "1"), ("public_cache_seconds", "0"), ("wx_push_min_seconds", "0"), ("wx_store_seconds", "10"), ("wx_ecowitt_pull_enabled", "1"), ("wx_tempest_pull_enabled", "0"), ("wx_bad_token_per_minute", "30"),
             ("wx_ecowitt_api_url", f"http://{HOST}:{PORT}/ecowitt"), ("wx_tempest_api_url", f"http://{HOST}:{PORT}/tempest/"), ("wx_max_per_user", "5"), ("wx_push_host", ""), ("wx_push_port", "80")):
    setting(k, v)

# ---- pages exist and are linked
t = anon.get(B + "/weather/").text
check("the Weather page loads for anyone and says there are no stations yet", "Weather" in t and "Stations" in t and "No weather stations have been made public yet" in t)
check("the menu has a Weather group and the Feed menu a Weather entry", "/weather/" in anon.get(B + "/").text and "/feed/weather" in anon.get(B + "/").text)
check("the How to Feed: Weather page loads with the Ecowitt steps", "Ecowitt Gateway or Console" in anon.get(B + "/feed/weather").text and "Home Assistant" in anon.get(B + "/feed/weather").text)
check("the home page has a Weather card even with no stations yet", "be the first to add one" in anon.get(B + "/").text)
check("the owner page needs a sign-in", requests.get(B + "/my/weather", allow_redirects=False).status_code in (302, 401))
check("the admin page is not for an ordinary user", me.get(B + "/admin/weather", allow_redirects=False).status_code in (302, 401, 403, 404))
check("Weather Stations is in the admin menu", 'href="/admin/weather"' in adm.get(B + "/admin/weather").text)
check("the service status page has a Weather Stations row", "Weather Stations" in anon.get(B + "/status").text)

# ---- an owner adds a stand-alone station
def add(s, **f):
    data = dict({"csrf_token": csrf(s, "/my/weather"), "kind": "ecowitt_push", "name": "Test Backyard", "feeder": "", "station_id": "TSTWX-1", "lat": "34.7432", "lon": "-92.3198", "privacy": "general", "hardware": "GW2000", "description": "", "public": "1"}, **f)
    return s.post(B + "/my/weather/add", data=data, allow_redirects=False)
r = add(me, station_id="x")
check("a bad station ID is refused", r.status_code == 302 and psql("select count(*) from wx_stations") == "0")
r = add(me, lat="95")
check("a bad latitude is refused", r.status_code == 302 and psql("select count(*) from wx_stations") == "0")
r = add(me)
loc = r.headers.get("Location", "")
check("a stand-alone station is added and its page opens", r.status_code == 302 and re.search(r"/my/weather/\d+$", loc), (r.status_code, loc))
wid = int(loc.rsplit("/", 1)[1])
page = me.get(B + loc).text
tok = re.search(r"/wx/push/([A-Za-z0-9_-]{20,64})", page).group(1)
check("the owner sees the Ecowitt settings with the private path and no secret in the database", "Customized" in page and f"/wx/push/{tok}" in page and tok not in psql(f"select token_sealed || token_hash from wx_stations where id={wid}"))
add(me, station_id="tstwx-1", name="b")
check("a duplicate station ID is refused (case-insensitive)", psql("select count(*) from wx_stations") == "1")

# ---- readings arrive
ECOFORM = {"PASSKEY": "ABC", "stationtype": "GW2000A", "model": "GW2000A", "tempf": "71.2", "humidity": "55", "baromrelin": "29.92", "windspeedmph": "7.5", "windgustmph": "12.1", "winddir": "225",
           "rainratein": "0.05", "dailyrainin": "0.12", "weeklyrainin": "0.4", "solarradiation": "410", "uv": "3", "lightning_num": "4", "lightning": "8"}
r = anon.post(B + f"/wx/push/{tok}", data=ECOFORM)
check("an Ecowitt post is accepted and answered 'success' (no sign-in, no CSRF token)", r.status_code == 200 and r.text == "success", (r.status_code, r.text))
la = json.loads(psql(f"select latest from wx_stations where id={wid}"))
check("the latest reading holds the values, a derived dew point and the device model", la["temp_f"] == 71.2 and la["hum"] == 55 and la["wind_dir"] == 225 and 53 < la["dew_f"] < 56 and la["extra"]["model"] == "GW2000A", la)
check("a raw reading went into the history", psql(f"select count(*) from wx_readings where station_pk={wid}") == "1")
r = anon.post(B + f"/wx/push/{tok}", data=dict(ECOFORM, tempf="72.0"))
check("a second post a moment later updates the latest but is not added to the history", json.loads(psql(f"select latest from wx_stations where id={wid}"))["temp_f"] == 72.0 and psql(f"select count(*) from wx_readings where station_pk={wid}") == "1")
check("an unknown address is not found", anon.post(B + "/wx/push/" + "x" * 32, data=ECOFORM).status_code == 404 and anon.post(B + "/wx/push/short", data=ECOFORM).status_code == 404)
check("a post with nothing usable is refused", anon.post(B + f"/wx/push/{tok}", data={"PASSKEY": "x"}).status_code == 400)
check("an oversize post is refused", anon.post(B + f"/wx/push/{tok}", data="x" * 20000, headers={"Content-Type": "text/plain"}).status_code == 413)
r = anon.get(B + f"/wx/wu/{tok}", params={"ID": "IARKANSAS1", "PASSWORD": "whatever", "dateutc": "now", "tempf": "60.5", "humidity": "40", "baromin": "30.10", "windspeedmph": "3", "winddir": "10", "dailyrainin": "0", "UV": "1.5", "action": "updateraw"})
check("the Weather Underground format is accepted on its own path", r.status_code == 200 and r.text == "success" and json.loads(psql(f"select latest from wx_stations where id={wid}"))["temp_f"] == 60.5, r.text)
r = anon.post(B + f"/wx/relay/{tok}", json={"temp_c": 20, "humidity": 60, "pressure_hpa": 1013.25, "wind_kph": 36, "wind_dir": 90, "rain_day_mm": 25.4})
la = json.loads(psql(f"select latest from wx_stations where id={wid}"))
check("a JSON relay is accepted and converted to the stored units", r.status_code == 200 and abs(la["temp_f"] - 68) < .01 and abs(la["wind_mph"] - 22.37) < .02 and abs(la["rain_day_in"] - 1) < .01, (r.status_code, la))
check("a relay that is not JSON is refused", anon.post(B + f"/wx/relay/{tok}", data="nope", headers={"Content-Type": "text/plain"}).status_code == 400)
# ---- the Tempest bridge (ha-arksignal/bridge) speaks to the relay with values that carry their own unit
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath("weather_test.py")), "..", "..", "ha-arksignal", "bridge"))
try:
    import arksignal_tempest_bridge as bridge
    st = bridge.State(elevation_m=100.0)
    st.feed({"serial_number": "ST-1", "type": "obs_st", "obs": [[1588948614, 0.2, 4.0, 7.5, 144, 3, 990.0, 22.0, 50.0, 32800, 3.5, 410, 0.254, 1, 12.0, 2, 2.41, 1]]})
    ok, text = bridge.post(f"{B}/wx/relay/{tok}", st.payload())
    la = json.loads(psql(f"select latest from wx_stations where id={wid}"))
    check("a Tempest bridge reading is accepted and converted (22 C = 71.6 F, 4 m/s = 8.9 mph, 2 strikes at 12 km = 7.5 mi, pressure raised to sea level)",
          ok and abs(la["temp_f"] - 71.6) < .05 and abs(la["wind_mph"] - 8.95) < .05 and abs(la["gust_mph"] - 16.78) < .05 and la["lightning_n"] == 2 and abs(la["lightning_mi"] - 7.46) < .05 and la["press_in"] > 29.3 and la["wind_dir"] == 144, (ok, text, la))
except ImportError:
    check("the Tempest bridge is next to the portal in ha-arksignal/bridge", False, "not found")
setting("wx_push_min_seconds", "30")
check("a station that reports too fast is told to slow down", anon.post(B + f"/wx/push/{tok}", data=ECOFORM).status_code == 429)
setting("wx_push_min_seconds", "0")
setting("wx_bad_token_per_minute", "1000")
codes = [anon.post(B + "/wx/push/" + ("g%d" % i) * 12, data=ECOFORM).status_code for i in range(6)]
check("unknown addresses are simply not found while few", set(codes) == {404}, codes)
setting("wx_bad_token_per_minute", "3")
codes = [anon.post(B + "/wx/push/" + ("h%d" % i) * 12, data=ECOFORM).status_code for i in range(8)]
check("a run of guessed addresses is slowed down (429)", codes[-1] == 429 and 429 in codes, codes)
check("the brake only touches unknown addresses: a real station still gets through", anon.post(B + f"/wx/push/{tok}", data=dict(ECOFORM, tempf="70.1")).status_code == 200)
setting("wx_bad_token_per_minute", "30")
time.sleep(0.1)

# ---- public visibility and privacy
psql(f"update wx_stations set public=false where id={wid}")
check("a private station has no public page, no API and is not on the map", anon.get(B + "/weather/TSTWX-1").status_code == 404 and anon.get(B + "/api/weather/TSTWX-1.json").status_code == 404
      and "TSTWX-1" not in anon.get(B + "/api/weather.json").text)
page = me.get(B + f"/my/weather/{wid}").text
check("the owner can still open the manage page and sees the latest reading", "Latest Reading" in page and "TSTWX-1" in page)
tk = csrf(me, f"/my/weather/{wid}")
r = me.post(B + f"/my/weather/{wid}/save", data={"csrf_token": tk, "name": "Test Backyard", "hardware": "GW2000", "description": "A test", "public": "1", "lat": "34.7432", "lon": "-92.3198", "privacy": "general"}, headers={"X-Requested-With": "XMLHttpRequest"})
check("the owner makes it public", r.json()["ok"] and psql(f"select public from wx_stations where id={wid}") == "t", r.text)
t = anon.get(B + "/weather/TSTWX-1").text
check("the public page shows current conditions, charts, summary, records and the station", all(x in t for x in ("Current Conditions", "Charts", "Daily Summary", "Records", "TSTWX-1", "wind", "Show on Weather Map")) and "approximate position" in t, t[:300])
check("it never shows the exact position (general privacy rounds to 0.1 degree)", "34.7432" not in t and "92.3198" not in t and "34.74" not in t)
check("units can be switched to metric", "°C" in anon.get(B + "/weather/TSTWX-1", params={"units": "metric"}).text)
api = anon.get(B + "/api/weather.json").json()
pt = [p for p in api["stations"] if p["id"] == "TSTWX-1"]
check("it is on the map feed at a rounded position with its temperature", pt and pt[0]["lat"] == 34.7 and pt[0]["lon"] == -92.3 and pt[0]["approx"] and pt[0]["temp"] == "70.1" and pt[0]["online"], api)
check("the map feed carries the full raw reading for the markers and the owner", pt and pt[0]["r"]["wind_dir"] == 225 and pt[0]["r"]["hum"] == 55 and pt[0]["r"]["press_in"] == 29.92 and pt[0]["owner"] == "plain1" and pt[0]["last"], pt)
mp = anon.get(B + "/weather/map")
check("the full Weather Map loads with its panel, layers, station list and shared script", mp.status_code == 200 and all(x in mp.text for x in ("Weather Map", "lv-layers", "lv-tbl", "/static/wxmap.js", "Value Range", "Copy Link to This View")), mp.status_code)
SHELL = ("lv", "lvmap", "lv-left", "lv-tools", "lv-toolsbody", "lv-legend", "lv-card", "lv-list", "lv-hide", "lv-altbar", "lv-chips", "lv-legbtn", "lv-filtbtn", "lv-wx", "lv-wxbtn", "lv-layers")
lvp = anon.get(B + "/live").text
check("the Weather Map is built on the same shell as the aircraft map: same stylesheet and the same panel, list, card, legend and colour-bar elements",
      all('id="%s"' % i in mp.text and 'id="%s"' % i in lvp for i in SHELL) and "/static/mapshell.css" in mp.text and "/static/mapshell.css" in lvp and "/static/maplayers.js" in mp.text and "/static/maplayers.js" in lvp, [i for i in SHELL if 'id="%s"' % i not in mp.text])
css = anon.get(B + "/static/mapshell.css")
check("the shared map stylesheet is served and carries the panel, list and card rules", css.status_code == 200 and all(x in css.text for x in ("#lv-list", "#lv-card", "details.fsec", "#lv-altbar", ".lv-panel")))
check("condition icons are drawn from one sprite on the weather pages", all('id="wxi-sunny"' in anon.get(B + p).text for p in ("/weather/map", "/weather/", "/live")))
js = anon.get(B + "/static/wxmap.js")
check("the shared marker script is served and offers the choices (wind arrows, filters, legend)", js.status_code == 200 and all(x in js.text for x in ("ArkWx", "arrows", "controlsHtml", "legend", "Dew Point", "UV Index")))
sr = anon.get(B + "/api/weather/TSTWX-1/series.json", params={"period": "24h"})
check("a public station's history is available for small charts", sr.status_code == 200 and sr.json()["station"] == "TSTWX-1" and isinstance(sr.json()["points"], list), sr.text[:200])
check("a private or unknown station's history is not", anon.get(B + "/api/weather/NOSUCH-1/series.json").status_code == 404)
check("the Weather page has the marker choices above its map and a link to the full map", "wxctl" in anon.get(B + "/weather/").text and "/weather/map" in anon.get(B + "/weather/").text)
cov = anon.get(B + "/coverage").text
check("the coverage map has a Weather stations layer (switched on) and no longer lists it as coming later", 'id: "wx"' in cov and "/api/weather.json" in cov and '"Weather stations", "Radiosondes"' not in cov and 'enabled: true, refresh' in cov)   # the layer exists; only ADS-B coverage is on when the map opens
lv = anon.get(B + "/live").text
check("the live map keeps every weather station control in the Weather panel (no second copy under Layers) and loads the shared scripts", 'id="wx-opts"' not in lv and "Weather stations</label>" not in lv and "wx-stn-on" in lv and "wx-stn-ctl" in lv and "/static/wxmap.js" in lv and "wxMetric" in lv)
check("a station ID that would hide the full weather map is refused", "used by the site itself" in (me.post(B + "/my/weather/add", data={"csrf_token": csrf(me, "/my/weather"), "kind": "relay", "name": "m", "feeder": "", "station_id": "map", "lat": "35", "lon": "-92", "privacy": "general"}, allow_redirects=True).text) or psql("select count(*) from wx_stations where station_id='MAP'") == "0")
lj = anon.get(B + "/weather/TSTWX-1/live.json", params={"units": "imperial"})
check("a public station has a live fragment: the status line and the current conditions as ready-made HTML, never cached", lj.status_code == 200 and lj.headers.get("Cache-Control") == "no-store" and "Humidity" in lj.json()["now"] and "reporting" in lj.json()["sub"] and lj.json()["online"], lj.text[:200])
anon.post(B + f"/wx/push/{tok}", data=dict(ECOFORM, tempf="63.5", windspeedmph="21"))
lj2 = anon.get(B + "/weather/TSTWX-1/live.json", params={"units": "imperial"}).json()
check("a new reading shows in the next fragment without reloading the page", "63.5" in lj2["now"] and "21" in lj2["now"] and "63.5" not in lj.json()["now"], lj2["now"][:300])
check("metric units work in the fragment", "°C" in anon.get(B + "/weather/TSTWX-1/live.json", params={"units": "metric"}).json()["now"])
check("a private or unknown station has no live fragment", anon.get(B + "/weather/NOSUCH-1/live.json").status_code == 404)
pg = anon.get(B + "/weather/TSTWX-1").text
check("the station page asks for fresh conditions by itself (no reload) and the maps refresh on the same setting", "/live.json?units=" in pg and 'id="wx-now-body"' in pg and 'id="wx-sub"' in pg and "wxLive" in anon.get(B + "/live").text and "wm.live" not in pg)
one = anon.get(B + "/api/weather/TSTWX-1.json").json()
check("the one-station API gives the reading in stored units and no device notes", one["reading"]["temp_f"] > 60 and "extra" not in one["reading"] and one["online"], one)
check("the Weather page lists it with the owner and the home page card counts it", "TSTWX-1" in anon.get(B + "/weather/").text and "of 1 station" in anon.get(B + "/").text)
home_layers = re.search(r"var LAYERS = (\[.*?\]), M = ", anon.get(B + "/").text, re.S)
check("the home map has a Weather Stations layer", home_layers and "Weather Stations" in home_layers.group(1) and "TSTWX-1" in home_layers.group(1))
check("the live map offers a Weather stations layer with a legend", 'id="l-wxstations"' in anon.get(B + "/live").text and "wxStations" in anon.get(B + "/live").text)
psql(f"update wx_stations set privacy='hidden' where id={wid}")
t = anon.get(B + "/weather/TSTWX-1").text
check("a hidden station is listed but has no position anywhere", "position hidden" in t and "Show on Map" not in t and not [p for p in anon.get(B + "/api/weather.json").json()["stations"] if p["id"] == "TSTWX-1"])
psql(f"update wx_stations set privacy='exact' where id={wid}")
pt = [p for p in anon.get(B + "/api/weather.json").json()["stations"] if p["id"] == "TSTWX-1"]
check("an exact station is shown at its real position, not marked approximate", pt and pt[0]["lat"] == 34.7432 and not pt[0]["approx"])
psql(f"update wx_stations set privacy='general' where id={wid}")

# ---- history, charts and the daily summary from stored rows
psql(f"insert into wx_readings (station_pk, ts, temp_f, dew_f, hum, press_in, wind_mph, gust_mph, rain_day_in, solar, uv) select {wid}, now() - g * interval '15 minutes' - interval '1 minute', 60 + 10 * sin(g / 8.0), 50, 55, 29.9, 5 + (g % 7), 9 + (g % 5), g * 0.001, 200, 2 from generate_series(1, 300) g on conflict do nothing")
for per in ("24h", "7d", "30d", "1y"):
    t = anon.get(B + "/weather/TSTWX-1", params={"period": per}).text
    check(f"the {per} charts draw", t.count("<svg") >= 4 and "No readings for this period" not in t.split("Daily Summary")[0].split("Charts")[1][:4000] or per == "1y", per)
t = anon.get(B + "/weather/TSTWX-1").text
import html as _html
_m = re.search(r'<svg class="chart" data-chart="([^"]+)"', t)
_meta = json.loads(_html.unescape(_m.group(1))) if _m else {}
check("each chart carries its data for the hover read-out (series with the time and value of every point, its unit and the time zone) and the page loads the script", bool(_meta) and _meta["series"] and len(_meta["series"][0]["pts"]) > 10 and _meta["unit"] and _meta["tz"] and "/static/chartinspect.js" in t, _meta.get("unit"))
check("the hover script is served", anon.get(B + "/static/chartinspect.js").status_code == 200 and "mousemove" in anon.get(B + "/static/chartinspect.js").text)
check("the daily summary has a row for today and the records show a date", re.search(r'<td class="mono">20\d\d-\d\d-\d\d</td>', t) and "Highest temperature" in t)

# ---- reading an owner's Ecowitt.net account
tk = csrf(me, "/my/weather")
r = me.post(B + "/my/weather/add", data={"csrf_token": tk, "kind": "ecowitt_cloud", "name": "Cloud One", "feeder": "", "station_id": "TSTWX-2", "lat": "35.1", "lon": "-92.1", "privacy": "general",
                                         "application_key": "APPKEYSECRET123", "api_key": "APIKEYSECRET456", "mac": "AA:BB:CC:DD:EE:FF", "public": "1"}, allow_redirects=False)
wid2 = int(r.headers["Location"].rsplit("/", 1)[1])
check("an Ecowitt.net station is added; its keys are stored sealed, not as text", "APPKEYSECRET123" not in psql(f"select secrets from wx_stations where id={wid2}") and psql(f"select secrets from wx_stations where id={wid2}").startswith("enc1:"))
page = me.get(B + f"/my/weather/{wid2}").text
check("the keys are not shown back to the owner", "APPKEYSECRET123" not in page and "APIKEYSECRET456" not in page and "Read Now" in page)
r = me.post(B + f"/my/weather/{wid2}/pull", data={"csrf_token": csrf(me, f"/my/weather/{wid2}")}, headers={"X-Requested-With": "XMLHttpRequest"})
la = json.loads(psql(f"select latest from wx_stations where id={wid2}") or "{}")
check("Read Now reads the account through the fake Ecowitt (units by their own text) and stores it", r.json()["ok"] and abs(la.get("temp_f", 0) - 68) < .01 and abs(la.get("wind_mph", 0) - 22.37) < .02 and abs(la.get("press_in", 0) - 29.92) < .01, (r.text, la))
check("the fake server saw the owner's own keys and the portal's user agent", any("api_key=APIKEYSECRET456" in h and "mac=AA%3ABB" in h and "application_key=APPKEYSECRET123" in h for h in MODE["hits"]))
t = anon.get(B + "/weather/TSTWX-2").text
check("a station read from Ecowitt.net credits Ecowitt on its public page", "Data from Ecowitt" in t)
MODE["ecowitt"] = "bad"
r = me.post(B + f"/my/weather/{wid2}/pull", data={"csrf_token": csrf(me, f"/my/weather/{wid2}")}, headers={"X-Requested-With": "XMLHttpRequest"})
check("a refused key is reported with Ecowitt's own words and counted", not r.json()["ok"] and "Illegal Application_Key" in r.json()["message"] and psql(f"select fail_count from wx_stations where id={wid2}") == "1", r.text)
check("the failure shows to the owner and the administrator", "Illegal Application_Key" in me.get(B + f"/my/weather/{wid2}").text and "Illegal Application_Key" in adm.get(B + "/admin/weather").text)
MODE["ecowitt"] = "ok"
# the background poller path: due stations are read, failing ones wait longer
unit = subprocess.run(["ssh", "-q", "nastemp", D + "exec -w /app -e PYTHONPATH=/app adsb-portal python -c \"import os; os.environ['DISABLE_BACKUP_SCHEDULER']='1'; os.environ['HUB_URL']=''; "
                       "import psycopg; from psycopg.rows import dict_row; from app import create_app; from app import weather as w; app=create_app(); ctx=app.app_context(); ctx.push(); "
                       "c=psycopg.connect(app.config['DATABASE_URL'], row_factory=dict_row); print('POLLED', w.poll_due(c)); print('POLLED2', w.poll_due(c))\""], capture_output=True, text=True).stdout
check("the poller reads the due account station and then waits", "POLLED 0" in unit or "POLLED 1" in unit and "POLLED2 0" in unit, unit)
check("Tempest accounts cannot be added while switched off", me.post(B + "/my/weather/add", data={"csrf_token": csrf(me, "/my/weather"), "kind": "tempest_cloud", "name": "T", "feeder": "", "station_id": "TSTWX-3", "lat": "35", "lon": "-92", "privacy": "general",
                                                                                                    "tempest_station": "12345", "token": "tok-tempest"}, allow_redirects=False).headers["Location"].endswith("/my/weather") and psql("select count(*) from wx_stations where kind='tempest_cloud'") == "0")
setting("wx_tempest_pull_enabled", "1")
r = me.post(B + "/my/weather/add", data={"csrf_token": csrf(me, "/my/weather"), "kind": "tempest_cloud", "name": "Tempest", "feeder": "", "station_id": "TSTWX-3", "lat": "35", "lon": "-92", "privacy": "general", "tempest_station": "12345", "token": "tok-tempest"}, allow_redirects=False)
wid3 = int(r.headers["Location"].rsplit("/", 1)[1])
r = me.post(B + f"/my/weather/{wid3}/pull", data={"csrf_token": csrf(me, f"/my/weather/{wid3}")}, headers={"X-Requested-With": "XMLHttpRequest"})
la = json.loads(psql(f"select latest from wx_stations where id={wid3}") or "{}")
check("a Tempest account (when allowed) is read with the owner's token and converted from SI", r.json()["ok"] and abs(la.get("temp_f", 0) - 50) < .01 and la.get("hum") == 80, (r.text, la))
setting("wx_tempest_pull_enabled", "0")
psql(f"update wx_stations set public=true where id={wid3}")
check("a Tempest station page carries the WeatherFlow credit", "Tempest by WeatherFlow" in anon.get(B + "/weather/TSTWX-3").text)

# ---- privacy between owners, admin control, deletion
if other:
    check("another member cannot open or change someone else's station", other.get(B + f"/my/weather/{wid}").status_code == 404 and other.post(B + f"/my/weather/{wid}/delete", data={"csrf_token": csrf(other, "/my/weather")}).status_code == 404)
check("a member's list shows only their own stations", me.get(B + "/my/weather").text.count("/my/weather/") >= 3 and "TSTWX-1" in me.get(B + "/my/weather").text)
r = me.post(B + f"/my/weather/{wid}/new-address", data={"csrf_token": csrf(me, f"/my/weather/{wid}")}, headers={"X-Requested-With": "XMLHttpRequest"})
tok2 = re.search(r"/wx/push/([A-Za-z0-9_-]{20,64})", me.get(B + f"/my/weather/{wid}").text).group(1)
check("a new address replaces the old one at once", r.json()["ok"] and tok2 != tok and anon.post(B + f"/wx/push/{tok}", data=ECOFORM).status_code == 404 and anon.post(B + f"/wx/push/{tok2}", data=ECOFORM).status_code == 200)
t = adm.get(B + "/admin/weather").text
check("the admin page lists every station with owner, source and state", all(x in t for x in ("TSTWX-1", "TSTWX-2", "TSTWX-3", "plain1", "Ecowitt gateway", "reporting")))
tk = csrf(adm, "/admin/weather")
adm.post(B + f"/admin/weather/{wid}/disable", data={"csrf_token": tk})
check("an administrator can switch a station off: it stops accepting readings and leaves the public pages", anon.post(B + f"/wx/push/{tok2}", data=ECOFORM).status_code == 404 and anon.get(B + "/weather/TSTWX-1").status_code == 404)
adm.post(B + f"/admin/weather/{wid}/enable", data={"csrf_token": csrf(adm, "/admin/weather")})
check("and back on", anon.post(B + f"/wx/push/{tok2}", data=ECOFORM).status_code == 200 and anon.get(B + "/weather/TSTWX-1").status_code == 200)
import re as _re
adm.post(B + "/admin/status/run", headers={"X-CSRFToken": _re.search(r'CSRF_TOKEN = "([^"]+)"', adm.get(B + "/admin/status").text).group(1), "X-Requested-With": "XMLHttpRequest"})   # the checks run in the background: an administrator forces a run
check("the status page counts the stations", "stations are reporting" in anon.get(B + "/api/status.json").text or "reporting" in anon.get(B + "/api/status.json").text)
check("the admin overview has a Weather stations tile", "Weather stations" in adm.get(B + "/admin/").text)
check("the Weather menu entry for the signed-in member is there", "/my/weather" in me.get(B + "/").text)
setting("wx_enabled", "0")
check("with the service switched off the coverage map drops the layer", 'id: "wx"' in anon.get(B + "/coverage").text and "enabled: false" in anon.get(B + "/coverage").text.split('id: "wx"')[1][:600])
check("with the service switched off the pages, API and upload addresses are not found and the menu entry is gone", anon.get(B + "/weather/").status_code == 404 and anon.get(B + "/api/weather.json").status_code == 404
      and anon.post(B + f"/wx/push/{tok2}", data=ECOFORM).status_code == 404 and "/weather/" not in anon.get(B + "/").text.split("</header>")[0] and "wx" not in [l["key"] for l in json.loads(re.search(r"var LAYERS = (\[.*?\]), M = ", anon.get(B + "/").text, re.S).group(1))])
setting("wx_enabled", "1")
tk = csrf(me, f"/my/weather/{wid}")
r = me.post(B + f"/my/weather/{wid}/delete", data={"csrf_token": tk}, allow_redirects=False)
check("the owner deletes a station with its readings", r.status_code == 302 and psql(f"select count(*) from wx_stations where id={wid}") == "0" and psql(f"select count(*) from wx_readings where station_pk={wid}") == "0")
adm.post(B + f"/admin/weather/{wid2}/delete", data={"csrf_token": csrf(adm, "/admin/weather")})
check("an administrator deletes one too", psql(f"select count(*) from wx_stations where id={wid2}") == "0")

# ---- a station that belongs to an ADS-B feeder shares its ID, location and privacy
FU = "weatherf-0000-4000-8000-000000000001"
psql(f"delete from feeders where uuid='{FU}'")
psql(f"insert into feeders (uuid, station_id, name, owner_id, lat, lon, privacy, online) values ('{FU}', 'TSTFEED-WX', 'Feeder With Weather', (select id from users where username='plain1'), 36.123456, -93.654321, 'general', true)")
add(me, station_id="TSTFEED-WX", name="clash")
check("a station ID that an ADS-B feeder already uses is refused", psql("select count(*) from wx_stations where station_id='TSTFEED-WX'") == "0")
t = me.get(B + f"/my/feeders/{FU}").text
check("the feeder page offers to add a weather station", "Add a Weather Station to This Feeder" in t)
r = me.post(B + "/my/weather/add", data={"csrf_token": csrf(me, "/my/weather"), "kind": "relay", "name": "", "feeder": FU, "hardware": "Davis", "description": "", "public": "1"}, allow_redirects=False)
wid4 = int(r.headers["Location"].rsplit("/", 1)[1])
tk4 = re.search(r"/wx/relay/([A-Za-z0-9_-]{20,64})", me.get(B + f"/my/weather/{wid4}").text).group(1)
rp = me.get(B + f"/my/weather/{wid4}").text
check("the address shown for the private relay (Home Assistant, the bridge, scripts) is https on the site's own name when no separate plain-http host is set", re.search(r"https://[^/\s<]+/wx/relay/" + tk4, rp) is not None, re.findall(r"https?://[^\s<]*wx/relay/", rp))
setting("wx_push_host", "plain.example.org"); setting("wx_push_port", "8080")
check("with a separate plain-http host set, the address is that host over http", f"http://plain.example.org:8080/wx/relay/{tk4}" in me.get(B + f"/my/weather/{wid4}").text)
setting("wx_push_host", ""); setting("wx_push_port", "80")
anon.post(B + f"/wx/relay/{tk4}", json={"temp_f": 77.7, "humidity": 40})
t = anon.get(B + "/weather/TSTFEED-WX").text
check("it takes the feeder's station ID and shows the feeder's rounded position, never the exact one", "TSTFEED-WX" in t and "36.12" not in t and "93.65" not in t and "approximate position" in t and "77.7" in t, t[:200])
check("the feeder's public page links to its weather", "/weather/TSTFEED-WX" in anon.get(B + "/feeders/TSTFEED-WX").text)
check("a feeder cannot have two weather stations and cannot be used by someone else", me.post(B + "/my/weather/add", data={"csrf_token": csrf(me, "/my/weather"), "kind": "relay", "feeder": FU, "name": "again", "public": ""}, allow_redirects=False).headers["Location"].endswith("/my/weather")
      and psql(f"select count(*) from wx_stations where feeder_uuid='{FU}'") == "1")
psql(f"update feeders set privacy='hidden' where uuid='{FU}'")
check("changing the feeder's privacy to hidden hides the weather station's position at once", not [p for p in anon.get(B + "/api/weather.json").json()["stations"] if p["id"] == "TSTFEED-WX"] and "position hidden" in anon.get(B + "/weather/TSTFEED-WX").text)
psql(f"update feeders set banned=true where uuid='{FU}'")
check("a banned feeder's weather station leaves the public pages", anon.get(B + "/weather/TSTFEED-WX").status_code == 404)
psql(f"delete from feeders where uuid='{FU}'")
check("deleting the feeder keeps the owner's weather station record (detached, no public ID)", psql(f"select count(*) from wx_stations where id={wid4}") == "1" and anon.get(B + "/weather/TSTFEED-WX").status_code == 404)
psql("DELETE FROM wx_stations")
print("ALL PASS" if ok_all else "SOME FAILED")
