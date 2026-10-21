"""End to end with a real Home Assistant (container 'hatest' on the sandbox, started from ha-arksignal; see ha-arksignal/tests/ha/README.md):
the ArkSignal integration is set up through Home Assistant's own config flow, finds the sensors of a fake Tempest device, and its readings arrive at a station on the portal.
Not part of run_all (it needs the Home Assistant container); run it by hand after changing the integration or the relay."""
import json, re, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
D = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker "
def ssh(cmd): return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True).stdout
def psql(sql): return ssh(D + f"exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\"").strip()
def csrf(s, path): return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + path).text).group(1)
HA = "http://test-box.invalid:8123"

# ---- a relay station on the portal
me = sess("plain1", "plainpassword1")
psql("DELETE FROM wx_stations WHERE station_id = 'HATEST-1'")
r = me.post(B + "/my/weather/add", data={"csrf_token": csrf(me, "/my/weather"), "kind": "relay", "name": "HA Test", "feeder": "", "station_id": "HATEST-1", "lat": "34.7", "lon": "-92.3", "privacy": "general", "public": "1"}, allow_redirects=False)
wid = int(r.headers["Location"].rsplit("/", 1)[1])
token = re.search(r"/wx/relay/([A-Za-z0-9_-]{20,64})", me.get(B + f"/my/weather/{wid}").text).group(1)
ADDR = f"http://test-box.invalid:18430/wx/relay/{token}"
# a site that answers plain http with a permanent redirect to the real address, the way production does (port 80 sends everything to https): a POST must stay a POST
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
SEEN = []
class Redir(BaseHTTPRequestHandler):
    def do_POST(self):
        SEEN.append(("POST", self.path)); self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(301); self.send_header("Location", ADDR); self.send_header("Content-Length", "0"); self.end_headers()
    def do_GET(self):
        SEEN.append(("GET", self.path)); self.send_response(405); self.send_header("Content-Length", "0"); self.end_headers()   # what the relay does to a redirected GET
    def log_message(self, *a): pass
rsrv = HTTPServer(("0.0.0.0", 2551), Redir); threading.Thread(target=rsrv.serve_forever, daemon=True).start()
REDIR_ADDR = f"http://test-box.invalid:2551/wx/relay/{token}"
psql("INSERT INTO settings (key, value) VALUES ('wx_push_min_seconds','0') ON CONFLICT (key) DO UPDATE SET value='0'")

# ---- Home Assistant: onboard, get a token
s = requests.Session()
if s.get(HA + "/api/onboarding").json() and not all(x["done"] for x in s.get(HA + "/api/onboarding").json() if x["step"] == "user"):
    code = s.post(HA + "/api/onboarding/users", json={"client_id": HA + "/", "name": "t", "username": "tester", "password": "Passw0rd!tester", "language": "en"}).json()["auth_code"]
else:
    code = None
if code:
    tok = s.post(HA + "/auth/token", data={"grant_type": "authorization_code", "code": code, "client_id": HA + "/"}).json()["access_token"]
else:
    tok = open("ha_token.txt").read().strip()
open("ha_token.txt", "w").write(tok)
H = {"Authorization": "Bearer " + tok}
def flow(path="", body=None, method="post"):
    f = getattr(s, method)
    r = f(HA + "/api/config/config_entries/flow" + path, headers=H, json=body)
    return r.json()
def tpl(t): return s.post(HA + "/api/template", headers=H, json={"template": t}).text.strip()

# ---- the fake Tempest device
entries = s.get(HA + "/api/config/config_entries/entry", headers=H).json()
if not any(e["domain"] == "fakestation" for e in entries):
    check("the fake weather station is added", flow(body={"handler": "fakestation"})["type"] == "create_entry")
for _ in range(20):
    if tpl("{{ states('sensor.fake_tempest_air_temperature') }}") not in ("unknown", "unavailable", ""): break
    time.sleep(2)
check("its sensors exist in Home Assistant", tpl("{{ states('sensor.fake_tempest_air_temperature') }}") == "21.5", tpl("{{ states('sensor.fake_tempest_air_temperature') }}"))
dev = tpl("{{ device_id('sensor.fake_tempest_air_temperature') }}")
check("and belong to one device", len(dev) == 32, dev)

# ---- the config flow: bad addresses first
for entry in [e for e in entries if e["domain"] == "arksignal"]:
    s.delete(HA + f"/api/config/config_entries/entry/{entry['entry_id']}", headers=H)
f = flow(body={"handler": "arksignal"})
check("the flow opens on the address step", f["type"] == "form" and f["step_id"] == "user", f)
r = flow("/" + f["flow_id"], {"url": "not an address"})
check("a bad address is explained", r["type"] == "form" and r["errors"].get("url") == "bad_address", r)
r = flow("/" + f["flow_id"], {"url": "http://test-box.invalid:18430/wx/relay/" + "z" * 30})
check("an address ArkSignal does not know is explained", r["type"] == "form" and r["errors"].get("url") == "unknown_address", r)
r = flow("/" + f["flow_id"], {"url": REDIR_ADDR})
check("an address that redirects (http to https) is followed with the POST kept, and the device step opens", r["type"] == "form" and r["step_id"] == "device", r)
r = flow("/" + f["flow_id"], {"device": dev})
check("choosing the device opens the sensor step", r["type"] == "form" and r["step_id"] == "sensors", r)
suggested = {}
for k in r["data_schema"]:
    sv = (k.get("description") or {}).get("suggested_value")
    if sv:
        suggested[k["name"]] = sv
check("the sensors were found by themselves", suggested == {
    "temperature": "sensor.fake_tempest_air_temperature", "dew_point": "sensor.fake_tempest_dew_point", "humidity": "sensor.fake_tempest_humidity", "pressure": "sensor.fake_tempest_sea_level_pressure",
    "wind_speed": "sensor.fake_tempest_wind_speed", "wind_gust": "sensor.fake_tempest_wind_gust", "wind_direction": "sensor.fake_tempest_wind_direction", "rain_rate": "sensor.fake_tempest_precipitation_rate",
    "rain_today": "sensor.fake_tempest_precipitation_today", "solar_radiation": "sensor.fake_tempest_solar_radiation", "uv_index": "sensor.fake_tempest_uv_index",
    "lightning_count": "sensor.fake_tempest_lightning_count", "lightning_distance": "sensor.fake_tempest_lightning_distance"}, suggested)
r2 = flow("/" + f["flow_id"], suggested)
check("finishing creates the entry", r2["type"] == "create_entry" and "Fake Tempest" in r2["title"], r2)
r3 = flow(body={"handler": "arksignal"}); r3 = flow("/" + r3["flow_id"], {"url": ADDR})
check("the redirect was followed by repeating the POST, never by a GET", ("POST", f"/wx/relay/{token}") in SEEN and not [x for x in SEEN if x[0] == "GET"], SEEN)
check("the same station cannot be set up twice", r3["type"] == "abort" and r3["reason"] == "already_configured", r3)

# ---- readings reach the portal (the first send is 20 seconds after setup)
la = {}
for _ in range(20):
    la = json.loads(psql(f"select latest from wx_stations where id={wid}") or "{}")
    if la.get("temp_f"): break
    time.sleep(3)
check("the first reading reaches the portal station", bool(la.get("temp_f")), la)
check("every value arrived converted: 21.5 C, 55 %, 1013.2 hPa, 4.5 m/s, 8 m/s, 225 degrees, 5.08 mm, 410 W/m2, UV 3.5, 2 strikes at 12 km",
      abs(la.get("temp_f", 0) - 70.7) < .05 and la.get("hum") == 55 and abs(la.get("press_in", 0) - 29.92) < .01 and abs(la.get("wind_mph", 0) - 10.07) < .02 and abs(la.get("gust_mph", 0) - 17.9) < .05
      and la.get("wind_dir") == 225 and abs(la.get("rain_day_in", 0) - 0.2) < .01 and la.get("solar") == 410 and la.get("uv") == 3.5 and la.get("lightning_n") == 2 and abs(la.get("lightning_mi", 0) - 7.46) < .05, la)
check("the station's page shows it", "70.7" in requests.get(B + "/weather/HATEST-1", params={"units": "imperial"}).text)

# ---- options: change the interval and the mapping; the entry reloads
entry = [e for e in s.get(HA + "/api/config/config_entries/entry", headers=H).json() if e["domain"] == "arksignal"][0]
o = s.post(HA + "/api/config/config_entries/options/flow", headers=H, json={"handler": entry["entry_id"]}).json()
check("the options form opens with the current choices", o["type"] == "form" and o["step_id"] == "init", o)
o2 = s.post(HA + f"/api/config/config_entries/options/flow/{o['flow_id']}", headers=H, json=dict(suggested, interval=30)).json()
check("saving the options works", o2["type"] == "create_entry", o2)
log = ssh(D + "logs --tail 200 hatest 2>&1")
check("Home Assistant logged no error from the integration", not re.search(r"(ERROR|Traceback).*arksignal|arksignal.*(ERROR|Traceback)", log, re.I), "\n".join(l for l in log.splitlines() if "arksignal" in l.lower())[-600:])

# ---- a failing send is reported once, not every minute
psql(f"update wx_stations set disabled=true where id={wid}")
time.sleep(2)
psql(f"update wx_stations set disabled=false where id={wid}")
psql(f"DELETE FROM wx_stations WHERE id={wid}")
print("ALL PASS" if ok_all else "SOME FAILED")
