"""The map notes of 2026-10-13: a Street / Satellite switch with the USGS imagery behind it, Show my location, the details card folds (and Layers and Filters fold on a phone), sections tagged for map types, the plain
county outline, brighter radiosonde tracks, forecast flights off by default, the new names, and the statistics in the Aircraft menu."""
import re, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
adm = sess("marty", PW)
pages = {"/live": "aircraft", "/weather/map": "weather", "/sondes/map": "balloons", "/counties/": "counties", "/coverage": "coverage", "/repeaters/map": "repeaters", "/aprs/map": "aprs"}
for path, mt in pages.items():
    h = requests.get(B + path).text
    check(f"{path}: Street / Satellite, Show my location and the card Collapse button", all(x in h for x in ('id="l-style"', 'id="my-loc"', 'id="my-loc-go"', 'id="c-min"')) and "/static/mylocation.js" in h, path)
    check(f"{path}: the Map selector lists Radiosondes, not Weather Balloons", "Radiosondes</option>" in h and "Weather Balloons" not in h, path)
layers = adm.get(B + "/admin/layers").text
check("the USGS satellite imagery is a base map in the catalog", "USGS Satellite Imagery" in layers)
check("the Satellite choice is a setting", 'name="map_satellite_layer"' in adm.get(B + "/admin/settings").text)
js = requests.get(B + "/static/maplayers.js").text
check("the switch changes the base map", 'getElementById("l-style")' in js and "dataset.sat" in js)
loc = requests.get(B + "/static/mylocation.js").text
check("my location is read in the browser only", "watchPosition" in loc and "never sent" in loc and "fetch(" not in loc and "XMLHttpRequest" not in loc)

sp = adm.get(B + "/admin/settings").text
check("map sections by map type is a setting, with Drive Times only on the counties and aircraft maps by default", 'name="map_section_tags"' in sp and "drive=counties,aircraft" in sp)
check("a map type without Drive Times hides its section", '["drive"]' in requests.get(B + "/weather/map").text or '"drive"' in requests.get(B + "/weather/map").text)
check("the counties map keeps it", '"drive"' not in re.sub(r"\s+", " ", requests.get(B + "/counties/").text).split("sections tagged for other map types")[-1][:300])
cty = requests.get(B + "/counties/").text
check("the County Map has a plain outline that needs no data", 'value="plain"' in cty and "Plain outline" in cty and 'state.look === "plain"' in cty)
sm = requests.get(B + "/sondes/map").text
check("the radiosonde tracks are drawn thick and bright over a dark edge", "function trk(pts, color, f)" in sm and "w + 2.5" in sm)
check("forecast flights are not drawn until a launch site asks for them", "siteFc = []" in sm and "if (!siteFc.length || state.only) return;" in sm)
check("the map is called the Radiosonde Map and the stations Sonde Receiving Stations", "Radiosonde Map" in requests.get(B + "/sondes/").text and "Sonde Receiving Stations" in requests.get(B + "/sondes/stations/").text)
nav = requests.get(B + "/").text
check("the menu says Radiosonde Map and Sonde Receiving Stations", "Radiosonde Map" in nav and "Sonde Receiving Stations" in nav and "Balloon Map" not in nav)
u = sess("plain1", "plainpassword1").get(B + "/my/").text
check("the profile menu says My Sonde Stations", "My Sonde Stations" in u and "My Radiosondes" not in u)
check("on the Radiosonde Map a balloon, its launch, burst and landing markers, a launch site and a receiving station all open the flyout (none leaves the page)", all(x in sm for x in ("function placeCard(kind, it)", 'placeCard("site", st)', 'placeCard("station", st)', 'select(decodeURIComponent(href.split("/").pop()))')) and "location.href = st.href" not in sm and "location.href = href" not in sm)
import subprocess
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
psql("DELETE FROM sondes WHERE serial = 'ZZHEARD-1'")
psql("INSERT INTO sondes (serial, kind, first_seen, last_seen, last_lat, last_lon, status, frames) VALUES ('ZZHEARD-1', 'radiosonde', now() - interval '2 hours', now() - interval '1 hour', 35.0, -92.0, 'landed', 10)")
psql("INSERT INTO sonde_heard (serial, callsign, packets, first_ts, last_ts, best_snr, last_snr, last_freq) VALUES ('ZZHEARD-1', 'ZZSTA-A', 5, now() - interval '2 hours', now() - interval '1 hour', 9.5, 8.0, 404.5)")
try:
    pg = requests.get(B + "/sondes/ZZHEARD-1").text
    check("the flight page has search and filters for the Heard By table", all(x in pg for x in ('id="hf-q"', 'id="hf-claimed"', 'id="hf-pos"', 'id="hf-age"')))
    check("the flight details say who heard it last, how many, the best signal and the farthest listener", all(x in pg for x in ('id="fd-lasthe"', 'id="fd-count"', 'id="fd-best"', 'id="fd-far"')))
finally:
    psql("DELETE FROM sondes WHERE serial = 'ZZHEARD-1'")
check("the Hybrid choice (satellite with roads and place names) is there, with its overlays in the catalog and a setting", all('value="hyb"' in requests.get(B + p_).text for p_ in ("/live", "/counties/")) and "Roads (for satellite)" in layers and "Place Names (for satellite)" in layers and 'name="map_hybrid_layers"' in adm.get(B + "/admin/settings").text)
print("ALL PASS" if ok_all else "SOME FAILED")
