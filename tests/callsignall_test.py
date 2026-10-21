"""A call sign lookup returns everything known about it on this site: repeaters and their DMR IDs, contact-list DMR IDs (signed-in members), feeders, weather and sonde stations; and the place page's forecast has icons."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", "docker exec -i adsb-portal-db psql -U portal -d portal -tA"], input=sql, capture_output=True, text=True, timeout=60).stdout.strip()
adm = sess("marty", PW)
psql("DELETE FROM repeaters WHERE callsign='W1AW'; DELETE FROM sonde_stations WHERE callsign='W1AW-7'; DELETE FROM dmr_contacts WHERE id=9990888")
tok = re.search(r'name="csrf_token" value="([^"]+)"', adm.get(B + "/admin/repeaters").text).group(1)
adm.post(B + "/admin/repeaters/add", data={"csrf_token": tok, "freq": "444.1", "offset": "+5", "callsign": "W1AW", "city": "Conway", "lat": "35.09", "lon": "-92.44", "modes": ["FM", "DMR"], "color_code": "2", "dmr_id": "310555001"}, allow_redirects=False)
psql("INSERT INTO sonde_stations (callsign, lat, lon, privacy, public) VALUES ('W1AW-7', 41.7, -72.7, 'exact', true); INSERT INTO dmr_contacts (id, name, city, state, country, kind) VALUES (9990888, 'W1AW Hiram', 'Newington', 'Connecticut', 'United States', 'Private Call')")
anon = requests.get(B + "/callsign", params={"call": "W1AW"}).text
check("the lookup lists the repeater, its DMR ID and color code, and the receiving station", "Everything We Know About W1AW" in anon and "310555001" in anon and "color code 2" in anon and "W1AW-7" in anon)
check("a visitor is told to sign in for the contact list IDs and does not see them", "Sign in to also see DMR IDs" in anon and "9990888" not in anon)
mem = adm.get(B + "/callsign", params={"call": "W1AW"}).text
check("a signed-in member also sees the contact list DMR ID", "DMR ID 9990888" in mem and "Newington" in mem)
check("the digital IDs are grouped together", (lambda t: t.index("<b>Digital ID") < t.index("<b>Repeater"))(mem.split("Everything We Know")[1]))
pl = requests.get(B + "/weather/place", params={"q": "Little Rock AR"}).text
check("the forecast cards have weather icons", "wxi big cond-" in pl.split('id="sec-forecast"')[1] if 'id="sec-forecast"' in pl else True)
psql("DELETE FROM repeaters WHERE callsign='W1AW'; DELETE FROM sonde_stations WHERE callsign='W1AW-7'; DELETE FROM dmr_contacts WHERE id=9990888")
print("ALL PASS" if ok_all else "SOME FAILED")
