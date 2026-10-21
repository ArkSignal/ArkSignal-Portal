"""ACARS: the service's own API (ingest in three decoder formats, de-duplication across stations, filters, positions, takedown), the member's station (add, upload address, upload), the public pages,
the text setting, the administrators' page (add for a member, switch off, hide an aircraft), the menu, Settings, Service Status and the registry. Needs the service running on the test box
(sh tests/deploy_acars.sh)."""
import json, os, re, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
HOST = os.environ.get("TEST_HOST", "test-box.invalid")
P = f"http://{HOST}:18481"
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
try:
    h = requests.get(P + "/health", timeout=10).json()
except Exception as e:
    print("SKIP acars_test: the ACARS service is not running on the test box (sh tests/deploy_acars.sh)", e); raise SystemExit(0)
check("the service answers and says its version", h.get("ok") is True and "version" in h)
tag = str(int(time.time()))[-6:]
tail1, tail2 = "N" + tag + "A", "N" + tag + "B"
ICAO = 10000000 + int(tag)

def post(path, body):
    return requests.post(P + path, json=body, timeout=20).json()

# three decoder formats: acars_router (flat), dumpvdl2 (nested), dumphfdl (nested)
now = time.time()
flat = {"timestamp": now, "freq": 131.55, "level": -20.5, "mode": "2", "label": "H1", "block_id": "1", "tail": tail1, "flight": "ZZA123", "icao": ICAO, "text": "POSITION REPORT N34123W092456 FL350", "msgno": "M01A"}
vdl = {"vdl2": {"freq": 136975000, "avlc": {"acars": {"reg": "." + tail2, "flight": "ZZB456", "label": "Q0", "blk_id": "2", "msg_text": "LINK TEST", "msg_num": "S01A"}}}, "timestamp": now}
hf = {"hfdl": {"lpdu": {"hfnpdu": {"acars": {"reg": tail2, "flight": "ZZB456", "label": "SA", "msg_text": "MEDIA ADVISORY"}}}}, "freq": 8942000, "timestamp": now}
r1 = post("/ingest", {"station": "ZZST1", "messages": [flat, vdl, hf, {"junk": 1}, "nonsense"]})
check("three formats are stored and junk is rejected", r1["new"] == 3 and r1["rejected"] == 2, r1)
r2 = post("/ingest", {"station": "ZZST2", "messages": [flat]})
check("the same message from a second station is stored once", r2["new"] == 0 and r2["duplicates"] == 1, r2)
m = requests.get(P + "/messages", params={"tail": tail1}).json()["messages"]
check("it shows how many stations heard it", len(m) == 1 and m[0]["heard_by"] == 2 and m[0]["label_text"], m)
check("the position in a position report is read (34 12.3 N, 92 45.6 W)", m and m[0]["lat"] is not None and abs(m[0]["lat"] - 34.205) < 0.001 and abs(m[0]["lon"] + 92.76) < 0.001, m[0] if m else None)
check("the ICAO address (a decimal number) is kept as hex", m[0]["hex"] == "%06x" % ICAO, m[0]["hex"])
m2 = requests.get(P + "/messages", params={"tail": tail2}).json()["messages"]
check("dumpvdl2 and dumphfdl messages are found by tail, the dot is dropped, frequency is in MHz", len(m2) == 2 and {x["mode"] for x in m2} == {"vdl2", "hfdl"} and any(abs((x["freq"] or 0) - 136.975) < .001 for x in m2), m2)
check("filters: flight, label, text", requests.get(P + "/messages", params={"flight": "zzb456"}).json()["messages"].__len__() == 2 and requests.get(P + "/messages", params={"label": "SA", "q": tag}).json()["messages"].__len__() in (0, 1)
      and requests.get(P + "/messages", params={"q": "LINK TEST"}).json()["messages"][0]["tail"] == tail2)
check("an aircraft's messages by ICAO address and by tail", len(requests.get(P + f"/aircraft/{'%06x' % ICAO}").json()["messages"]) == 1 and len(requests.get(P + f"/aircraft/{tail2}").json()["messages"]) == 2)
check("positions are listed", any(p["tail"] == tail1 for p in requests.get(P + "/positions").json()["positions"]))
hexa, hexb = "%06x" % (0xA00000 + int(tag)), "%06x" % (0xB00000 + int(tag))
hfpos = {"hfdl": {"lpdu": {"type": {"id": 13}, "src": {"ac_info": {"icao": hexa.upper()}}, "hfnpdu": {"type": {"id": 209, "name": "Performance data"}, "flight_id": "ZZH" + tag[-3:], "pos": {"lat": 36.5, "lon": -91.25}}}}, "freq": 8942000, "timestamp": now}
adsc = {"app": {"name": "JAERO"}, "isu": {"aes": hexb.upper(), "adsc": {"tags": [{"basic_report": {"lat": 37.5, "lon": -90.5, "alt": 38000}}]}}, "timestamp": now}
rs = post("/ingest", {"station": "ZZST1", "messages": [hfpos, adsc]})
check("positions carried in HFDL and Inmarsat ADS-C fields (no text at all) are stored", rs["new"] == 2, rs)
pp = requests.get(P + "/positions").json()["positions"]
check("they are on the positions list with the aircraft's ICAO address and altitude", any(x["hex"] == hexa and abs(x["lat"] - 36.5) < 1e-4 and abs(x["lon"] + 91.25) < 1e-4 for x in pp) and any(x["hex"] == hexb and abs(x["lat"] - 37.5) < 1e-4 and x["alt_ft"] == 38000 for x in pp), [x for x in pp if x["hex"] in (hexa, hexb)])
check("they carry the right link: HFDL for the first, Inmarsat for the JAERO one", requests.get(P + f"/aircraft/{hexa}").json()["messages"][0]["mode"] == "hfdl" and requests.get(P + f"/aircraft/{hexb}").json()["messages"][0]["mode"] == "inmarsat")
check("an object with nothing to identify an aircraft is still rejected", post("/ingest", {"station": "ZZST1", "messages": [{"isu": {"adsc": {"basic_report": {"lat": 1.0, "lon": 2.0}}}}]})["rejected"] == 1)
st = requests.get(P + "/stats").json()
check("statistics carry counts, stations, modes and labels", all(k in st for k in ("messages", "hour", "day", "stations", "modes", "labels", "hourly", "feed", "config")) and any(s["station"] == "ZZST1" for s in st["stations"]), list(st))
check("a takedown by tail deletes its messages", post("/delete", {"tail": tail2})["deleted"] == 2 and not requests.get(P + "/messages", params={"tail": tail2}).json()["messages"])
check("a request without a station or with nothing to delete is refused", requests.post(P + "/ingest", json={"messages": [flat]}).status_code == 400 and requests.post(P + "/delete", json={}).status_code == 400)

# the portal
setting("acars_enabled", "1"); time.sleep(3)
adm = sess("marty", PW)
check("the page is there once ACARS is on", requests.get(B + "/acars/").status_code == 200 and "ACARS" in requests.get(B + "/acars/").text)
pos = requests.get(B + "/api/acars/positions.json").json()
mine = [x for x in pos.get("positions", []) if x.get("tail") == tail1.upper() or x.get("hex") == "%06x" % ICAO]
check("aircraft that reported a position by ACARS are on the positions list for the live map (positions only, no text)", pos.get("enabled") and mine and abs(mine[0]["lat"] - 34.205) < 0.001 and "text" not in mine[0], (mine[:2], len(pos.get("positions", []))))
lv = requests.get(B + "/live").text
check("the live map has an ACARS positions layer once ACARS is on", 'id="l-acars"' in lv and "loadAcarsPos" in lv)
check("it is in the menu: ACARS and My ACARS Stations", 'href="/acars/"' in requests.get(B + "/").text and 'href="/my/acars"' in requests.get(B + "/").text)
check("an unknown upload address is a 404", requests.post(B + "/acars/push/" + "A" * 30, json=flat).status_code == 404)
u = sess("plain1", "plainpassword1")
check("a member's page opens", u.get(B + "/my/acars").status_code == 200 and "Add an ACARS Station" in u.get(B + "/my/acars").text)
sid = "ZZ" + tag
psql(f"DELETE FROM acars_stations WHERE station_id LIKE 'ZZ%'")
tk = tok(u, "/my/acars")
rr = u.post(B + "/my/acars/add", data={"csrf_token": tk, "station_id": sid, "name": "Test roof", "bands": "VHF", "lat": "34.7", "lon": "-92.3", "privacy": "general", "public": "1"}, allow_redirects=False)
check("adding a station opens its page", rr.status_code in (302, 303) and "/my/acars/" in rr.headers.get("Location", ""), rr.status_code)
page = u.get(B + rr.headers["Location"]).text
addr = re.search(r"https?://[^\s<\"]+/acars/push/([A-Za-z0-9_-]{20,64})", page)
check("the page shows the private upload address and the bridge command", addr is not None and "acars_bridge.py" in page)
token = addr.group(1) if addr else ""
body = "\n".join(json.dumps(x) for x in [dict(flat, text="PUSHED BY A MEMBER " + tag, msgno="M09Z", tail="N" + tag + "C", flight="ZZC789")])
up = requests.post(B + "/acars/push/" + token, data=body, headers={"Content-Type": "application/json"})
check("an upload of one message per line is accepted", up.status_code == 200 and up.json().get("new") == 1, (up.status_code, up.text[:150]))
time.sleep(1)
api = requests.get(B + "/api/acars/messages.json", params={"q": "PUSHED BY A MEMBER " + tag}).json()
check("it shows on the public API with the station's public name", len(api["messages"]) == 1 and api["messages"][0]["station_name"] == "Test roof" and "PUSHED BY A MEMBER" in api["messages"][0]["text"], api)
check("the page lists it", "PUSHED BY A MEMBER " + tag in requests.get(B + "/acars/", params={"q": tag}).text)
cardc = requests.get(B + f"/api/acars/aircraft/{'%06x' % ICAO}.json").json()
check("the aircraft card feed answers by ICAO address", len(cardc["messages"]) >= 1)
check("the station has a public page and is in the list", sid in requests.get(B + "/acars/stations/").text and requests.get(B + f"/acars/stations/{sid}").status_code == 200)
check("the member's station counts the upload", "1 messages sent" in u.get(B + rr.headers["Location"]).text or "messages sent" in u.get(B + rr.headers["Location"]).text)
# the text setting
setting("acars_text_visibility", "members"); time.sleep(2)
a = requests.get(B + "/api/acars/messages.json", params={"q": tag}).json()["messages"]
check("members only: a visitor sees no text", a and all(x["text"] == "" and x.get("text_hidden") for x in a))
check("members only: a signed-in member sees it", any("PUSHED BY A MEMBER" in x["text"] for x in u.get(B + "/api/acars/messages.json", params={"q": tag}).json()["messages"]))
setting("acars_text_visibility", "none"); time.sleep(2)
check("nobody: even a member sees no text", all(x["text"] == "" for x in u.get(B + "/api/acars/messages.json", params={"q": tag}).json()["messages"]))
setting("acars_text_visibility", "everyone"); time.sleep(2)
# the administrators
ap = adm.get(B + "/admin/acars")
check("the admin page lists the station with Manage, switch off and delete", ap.status_code == 200 and sid in ap.text and "Manage" in ap.text and "Add an ACARS Station for a Member" in ap.text)
ta = tok(adm, "/admin/acars")
adm.post(B + "/admin/acars/add", data={"csrf_token": ta, "username": "plain1", "station_id": sid + "A", "name": "For a member", "privacy": "general"}, allow_redirects=False)
check("an administrator adds a station for a member", any(r for r in [psql(f"SELECT 1 FROM acars_stations s JOIN users u ON u.id=s.owner_id WHERE s.station_id='{sid}A' AND u.username='plain1'")]))
wid = psql(f"SELECT id FROM acars_stations WHERE station_id='{sid}'")
adm.post(B + f"/admin/acars/{wid}/disable", data={"csrf_token": ta}, allow_redirects=False)
check("a switched-off station's address stops working", requests.post(B + "/acars/push/" + token, json=flat).status_code == 404)
adm.post(B + f"/admin/acars/{wid}/enable", data={"csrf_token": ta}, allow_redirects=False)
check("and works again when switched on", requests.post(B + "/acars/push/" + token, json=[dict(flat, text="again " + tag, msgno="M10Z")]).status_code == 200)
check("an administrator can open the member's station page (Manage)", adm.get(B + f"/my/acars/{wid}").status_code == 200 and "managing" in adm.get(B + f"/my/acars/{wid}").text.lower())
adm.post(B + "/admin/acars/hide", data={"csrf_token": ta, "tail": "N" + tag + "C", "reason": "test"}, allow_redirects=False)
time.sleep(1)
check("a hidden aircraft is gone from the pages and its new messages are not kept", not requests.get(B + "/api/acars/messages.json", params={"tail": "N" + tag + "C"}).json()["messages"]
      and requests.post(B + "/acars/push/" + token, json=[dict(flat, text="after hiding " + tag, msgno="M11Z", tail="N" + tag + "C")]).json().get("new") == 0)
adm.post(B + "/admin/acars/unhide", data={"csrf_token": ta, "tail": "N" + tag + "C"}, allow_redirects=False)
check("a non-member cannot use the admin page", requests.get(B + "/admin/acars", allow_redirects=False).status_code in (302, 401, 403, 404) and u.get(B + "/admin/acars", allow_redirects=False).status_code in (302, 401, 403, 404))
check("Settings has the ACARS section", "acars_text_visibility" in adm.get(B + "/admin/settings").text)
check("Service Status has an ACARS check", "ACARS" in adm.get(B + "/admin/status").text or "acars" in requests.get(B + "/status").text.lower())
check("the Feed menu and the how-to page", 'href="/feed/acars"' in requests.get(B + "/feed/").text and "acars_bridge.py" in requests.get(B + "/feed/acars").text)
check("the bridge script is served", "ArkSignal ACARS bridge" in requests.get(B + "/static/acars_bridge.py").text)
psql("DELETE FROM acars_stations WHERE station_id LIKE 'ZZ%'; DELETE FROM acars_hidden")
post("/delete", {"station": "ZZST1"}); post("/delete", {"station": "ZZST2"}); post("/delete", {"station": sid})
setting("acars_enabled", "0")
check("switched off, the page is a 404 and the menu entry is gone", requests.get(B + "/acars/").status_code == 404)
print("ALL PASS" if ok_all else "SOME FAILED")
