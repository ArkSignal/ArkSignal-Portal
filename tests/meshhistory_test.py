"""Node readings and charts: the mesh service keeps one row per node per interval of what a node reports in the clear (battery, voltage, channel use and the environment sensors: temperature, humidity, pressure,
wind, rain, light), drops values that cannot be real, serves them averaged in time slots, and the portal draws them with the site's shared chart style (units from the Display menu), lists every node with
sensors on Mesh Weather, and leaves hidden nodes out. Needs the service and broker running on the test box (sh tests/deploy_mesh.sh)."""
import base64, os, re, struct, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
HOST = os.environ.get("TEST_HOST", "test-box.invalid")
P = f"http://{HOST}:18482"
def ssh(cmd):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=120).stdout
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def mpsql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec mesh-db psql -U mesh -d mesh -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
try:
    h = requests.get(P + "/health", timeout=10).json()
except Exception as e:
    print("SKIP meshhistory_test: the mesh service is not running on the test box (sh tests/deploy_mesh.sh)", e); raise SystemExit(0)

def varint(v):
    v &= (1 << 64) - 1
    out = b""
    while True:
        b = v & 0x7F; v >>= 7
        out += bytes([b | (0x80 if v else 0)])
        if not v:
            return out
def tag(num, wt): return varint((num << 3) | wt)
def vi(num, v): return tag(num, 0) + varint(v)
def ld(num, b): return tag(num, 2) + varint(len(b)) + (b if isinstance(b, bytes) else b.encode())
def fx(num, v): return tag(num, 5) + struct.pack("<I", v & 0xFFFFFFFF)
def sfx(num, v): return tag(num, 5) + struct.pack("<i", v)
def flt(num, v): return tag(num, 5) + struct.pack("<f", v)
def data(port, payload): return ld(4, vi(1, port) + ld(2, payload))
def envelope(sender, port, payload, gateway="!feedbeef", channel="LongFast"):
    pk = fx(1, sender) + fx(2, 0xFFFFFFFF) + data(port, payload) + fx(6, 12345)
    return ld(1, pk) + ld(2, channel) + ld(3, gateway)
def map_report(long, short, lat, lon):
    return ld(1, long) + ld(2, short) + vi(3, 2) + vi(4, 9) + ld(5, "2.5.0") + vi(6, 1) + vi(7, 0) + vi(8, 1) + sfx(9, int(lat * 1e7)) + sfx(10, int(lon * 1e7)) + vi(11, 100) + vi(12, 16) + vi(13, 3) + vi(14, 1)
def send(env): return requests.post(P + "/ingest", json={"envelope_b64": base64.b64encode(env).decode()}, timeout=20).json()["result"]
def telemetry(battery=None, voltage=None, chan=None, air=None, env=None):
    dm = b""
    if battery is not None: dm += vi(1, battery)
    if voltage is not None: dm += flt(2, voltage)
    if chan is not None: dm += flt(3, chan)
    if air is not None: dm += flt(4, air)
    t = fx(1, int(time.time()))
    if dm: t += ld(2, dm)
    if env: t += ld(3, env)
    return t

N = 0xA0000000 + (int(time.time()) & 0xFFFF)   # a node of our own
slug = "%08x" % N
M = 0xB0000000 + (int(time.time()) & 0xFFFF)   # a node with no sensors
requests.post(P + "/config", json={"history_min_seconds": "30", "history_keep_days": "30"}, timeout=10)
mpsql(f"DELETE FROM node_history WHERE node_id IN ({N}, {M})")
send(envelope(N, 4, ld(2, "Hist Test Node") + ld(3, "HTN") + vi(5, 9) + vi(7, 2)))
send(envelope(N, 3, sfx(1, int(34.8 * 1e7)) + sfx(2, int(-92.3 * 1e7)) + vi(3, 100) + vi(23, 16)))
send(envelope(M, 4, ld(2, "No Sensor Node") + ld(3, "NSN") + vi(5, 9)))
send(envelope(M, 3, sfx(1, int(34.9 * 1e7)) + sfx(2, int(-92.4 * 1e7)) + vi(3, 100) + vi(23, 16)))
env = flt(1, 21.5) + flt(2, 55.0) + flt(3, 1013.2) + vi(13, 270) + flt(14, 4.0) + flt(16, 7.5) + flt(19, 2.0) + flt(9, 1200.0)
r = send(envelope(N, 67, telemetry(battery=87, voltage=3.91, chan=12.5, air=1.2, env=env)))
check("a telemetry packet with device and sensor readings is stored", r == "stored", r)
rows = requests.get(P + f"/nodes/!{slug}/history?hours=24", timeout=10).json()
row = rows["rows"][0] if rows.get("rows") else {}
check("one reading is kept with every value", len(rows.get("rows", [])) == 1 and row.get("battery") == 87 and abs(row.get("voltage") - 3.91) < 0.01 and abs(row.get("temp_c") - 21.5) < 0.01 and row.get("humidity") == 55
      and abs(row.get("pressure_hpa") - 1013.2) < 0.1 and row.get("wind_dir") == 270 and abs(row.get("wind_ms") - 4.0) < 0.01 and abs(row.get("gust_ms") - 7.5) < 0.01 and abs(row.get("rain_1h_mm") - 2.0) < 0.01 and abs(row.get("lux") - 1200) < 1, row)
send(envelope(N, 67, telemetry(battery=86, env=flt(1, 22.5))))
check("a second report inside the interval is not stored (one reading per node per interval)", mpsql(f"SELECT COUNT(*) FROM node_history WHERE node_id={N}") == "1")
# older readings so the charts have a line
mpsql(f"INSERT INTO node_history (node_id, ts, battery, voltage, temp_c, humidity, pressure_hpa, wind_ms, gust_ms, rain_1h_mm, lux, channel_util, air_util) SELECT {N}, now() - (i * interval '30 minutes'), 90 - i, 3.9, 15 + i * 0.5, 50 + i, 1010 + i * 0.1, 3, 6, 0.5, 800, 10 + i, 1 FROM generate_series(1, 20) i")
rows = requests.get(P + f"/nodes/!{slug}/history?hours=24", timeout=10).json()
check("the service serves them averaged in five-minute slots, oldest first", rows["bucket"] == 300 and len(rows["rows"]) == 21 and rows["rows"][0]["t"] < rows["rows"][-1]["t"], (rows["bucket"], len(rows["rows"])))
mpsql(f"DELETE FROM node_history WHERE node_id={N} AND ts > now() - interval '2 minutes'")
send(envelope(N, 67, telemetry(battery=0, voltage=0.0, env=flt(1, 999.0) + flt(2, 140.0) + flt(3, 1000.0))))
last = mpsql(f"SELECT coalesce(battery::text,'-') || '|' || coalesce(temp_c::text,'-') || '|' || coalesce(humidity::text,'-') || '|' || coalesce(pressure_hpa::text,'-') FROM node_history WHERE node_id={N} ORDER BY ts DESC LIMIT 1")
check("values that cannot be real are dropped (zero battery, 999 degrees, 140 percent humidity) and a real one next to them is kept", last == "-|-|-|1000", last)
rj = requests.post(P + "/ingest_json", json={"topic": "msh/US/AR/2/json/LongFast/!feedbeef", "payload": {"from": N, "type": "telemetry", "sender": "!feedbeef", "payload": {"temperature": 19.0, "relative_humidity": 60.0, "battery_level": 80}}}, timeout=10)
check("the JSON door understands the same readings", rj.status_code == 200, rj.text)
check("a node with no readings has no history rows", mpsql(f"SELECT COUNT(*) FROM node_history WHERE node_id={M}") == "0")
wx = requests.get(P + "/weather", timeout=10).json()["nodes"]
check("the weather list has the node with sensors and not the one without", any(x["slug"] == slug for x in wx) and not any(x["slug"] == "%08x" % M for x in wx), [x["slug"] for x in wx][:5])

setting("mesh_enabled", "1")
anon = requests.Session()
page = anon.get(B + f"/mesh/{slug}")
check("the node page has a Readings section with the charts it has data for", page.status_code == 200 and 'id="sec-readings"' in page.text and page.text.count('class="chart"') >= 6, page.status_code)
for title in ("Battery", "Voltage", "Channel Use", "Temperature", "Humidity", "Pressure", "Wind", "Rain", "Light"):
    check(f"chart: {title}", f"<h3>{title}</h3>" in page.text)
check("no chart for what the node never reported (air quality)", "<h3>Air Quality</h3>" not in page.text)
check("readings half an hour apart are joined into a line (not left as dots)", re.search(r'<path d="M[^"]* L', page.text) is not None)
check("the charts use the shared hover read-out", "/static/chartinspect.js" in page.text and 'data-chart="' in page.text)
imp, met = anon.get(B + f"/mesh/{slug}?units=imperial").text, anon.get(B + f"/mesh/{slug}?units=metric").text
check("units follow the Display menu (imperial: degrees F, inHg, mph; metric: degrees C, hPa, km/h)", "°F" in imp and "inHg" in imp and "mph" in imp and "°C" in met and "hPa" in met and "km/h" in met)
check("another period can be chosen", anon.get(B + f"/mesh/{slug}?period=7d").status_code == 200 and "7 days" in anon.get(B + f"/mesh/{slug}?period=7d").text)
check("the node without readings shows no Readings section", 'id="sec-readings"' not in anon.get(B + "/mesh/%08x" % M).text)
hj = anon.get(B + f"/api/mesh/{slug}/history.json?hours=24").json()
check("the history is also an API with the credit", len(hj["rows"]) >= 20 and "credit" in hj)
check("a bad node id is refused", anon.get(B + "/api/mesh/zzzz/history.json").status_code == 404 and anon.get(B + "/api/mesh/mc-" + "a" * 64 + "/history.json").status_code == 404)
w = anon.get(B + "/mesh/weather")
check("Mesh Weather lists the node with its newest values and the menu has it", w.status_code == 200 and "Hist Test Node" in w.text and "No Sensor Node" not in w.text and 'href="/mesh/weather"' in anon.get(B + "/mesh/").text)
check("Mesh Weather follows the units", "°F" in anon.get(B + "/mesh/weather?units=imperial").text and "°C" in anon.get(B + "/mesh/weather?units=metric").text)
mw = [x for x in anon.get(B + "/api/weather.json?sources=mesh").json()["stations"] if x["id"] == "!" + slug]
check("the Weather Map has the node as a mesh source with its place, name and link", len(mw) == 1 and mw[0]["src"] == "mesh" and mw[0]["name"] == "Hist Test Node" and abs(mw[0]["lat"] - 34.8) < 0.01 and mw[0]["href"] == "/mesh/" + slug, mw)
check("a node with no sensors is not on the Weather Map", not any(x["id"] == "!%08x" % M for x in anon.get(B + "/api/weather.json?sources=mesh").json()["stations"]))
check("the mesh source is not in the default members list, and a view that does not hold the node leaves it out", not any(x["id"] == "!" + slug for x in anon.get(B + "/api/weather.json").json()["stations"]) and not any(x["id"] == "!" + slug for x in anon.get(B + "/api/weather.json?sources=mesh&bbox=30,-100,31,-99").json()["stations"]))
check("the Weather Map has the Mesh Nodes switch", 'data-src="mesh"' in anon.get(B + "/weather/map").text)

send(envelope(M, 3, sfx(1, int(34.9 * 1e7)) + sfx(2, int(-92.4 * 1e7)) + vi(3, 100) + vi(23, 16), gateway="!" + slug))
check("Heard By names the gateway with its friendly name, linked to its page", re.search(rf'<a href="/mesh/{slug}"[^>]*>Hist Test Node</a>', anon.get(B + "/mesh/%08x" % M).text) is not None)
# a node taken down is not charted or listed
adm = sess("marty", PW)
act(adm, "/admin/mesh/hide", "/admin/mesh", node="!" + slug, reason="history test")
check("a node taken down is not named in Heard By", "Hist Test Node" not in anon.get(B + "/mesh/%08x" % M).text)
check("a hidden node has no history and is not on Mesh Weather", requests.get(P + f"/nodes/!{slug}/history", timeout=10).status_code == 404 and "Hist Test Node" not in anon.get(B + "/mesh/weather").text and anon.get(B + f"/api/mesh/{slug}/history.json").status_code == 404)
check("a hidden node is not on the Weather Map either", not any(x["id"] == "!" + slug for x in anon.get(B + "/api/weather.json?sources=mesh").json()["stations"]))
act(adm, "/admin/mesh/unhide", "/admin/mesh", node="!" + slug)
st = adm.get(B + "/admin/settings/radio").text
check("the retention choices are settings", "Keep Node Readings For" in st and "Keep One Reading per Node Every" in st)
mpsql(f"DELETE FROM node_history WHERE node_id IN ({N}, {M})")
print("ALL PASS" if ok_all else "SOME FAILED")
