"""The Flights search and Flight History show the same things for a flight: flight number and registration as links to every logged flight, and the airline, make and model and route filled in from the outside lookups (one batch call per page, cached)."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
MEM = sess("plain1", "plainpassword1")   # Planefence, the code plug exports, downloads and the My ... pages are for signed-in members
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
anon = requests.Session()
psql("DELETE FROM flight_log WHERE hex LIKE 'ed%'")
psql("INSERT INTO flight_log (hex, callsign, reg, type, first_seen, last_seen, start_lat, start_lon, end_lat, end_lon, max_alt, max_gs, positions, mil) VALUES ('ed0001', 'DAL530', 'N377DN', 'A321', now() - interval '3 hours', now() - interval '2 hours', 33.6, -84.4, 39.8, -104.7, 34000, 500, 80, false)")
hp = anon.get(B + "/history/?q=DAL530&duration=24").text
check("History: the flight number and the registration are links, and the row asks for more", 'href="/history/?q=DAL530&amp;duration=8760"' in hp and 'href="/history/?q=N377DN&amp;duration=8760"' in hp and 'data-x data-hex="ed0001"' in hp and 'class="x-route"' in hp and 'class="x-airline"' in hp and "<th>Airline</th>" in hp and "<th>Route</th>" in hp)
fs = anon.get(B + "/flight/").text
check("Flights: the same columns and the same links", all(x in fs for x in ('hist(fl)', 'hist(a.r)', 'x-airline', 'x-route', "Airline", "Route", "flightextra.js")), fs[:200])
check("both load the shared script", "/static/flightextra.js" in hp and anon.get(B + "/static/flightextra.js").status_code == 200)
check("an empty or bad batch answers nothing and does not fail", anon.get(B + "/api/flight-extra.json").json() == {} and anon.get(B + "/api/flight-extra.json?items=zzz:x").json() == {})
j = anon.get(B + "/api/flight-extra.json", params={"items": "ed0001:DAL530:33.6:-84.4,a403df:AAL2242:34.8:-92.7"}).json()
check("the batch answers for each flight asked about", set(j) <= {"ed0001", "a403df"} and all(isinstance(v, dict) for v in j.values()), j)
if j.get("ed0001", {}).get("route"):
    r = j["ed0001"]["route"]
    check("a route has both ends", r["from"]["code"] and r["to"]["code"], r)
check("more than 40 flights in one call are cut to 40", len(anon.get(B + "/api/flight-extra.json", params={"items": ",".join(f"{i:06x}:X" for i in range(60))}).json()) <= 40)
ab = MEM.get(B + "/about").text
check("the About page credits the new sources (Meshtastic, MeshCore, Mosquitto, the time zone database, AirTrail as inspiration) and the flight data from adsbdb", all(x in ab for x in ("Meshtastic", "MeshCore", "Eclipse Mosquitto", "IANA Time Zone Database", "AirTrail", "airlines and flight routes")))
check("under the Flights and Flight History lists the data is credited", "adsbdb" in fs and "adsbdb" in hp and "adsb.im" in hp)
adm = sess("marty", PW)
st = adm.get(B + "/admin/status/data.json").text + adm.get(B + "/admin/status").text
check("Service Status probes the sources the new features use (adsbdb with its wider job, the Meshtastic protocol list)", "adsbdb Aircraft and Callsign Data" in st and "Meshtastic Protocol Definitions" in st, "")
psql("DELETE FROM flight_log WHERE hex LIKE 'ed%'")
print("ALL PASS" if ok_all else "SOME FAILED")
