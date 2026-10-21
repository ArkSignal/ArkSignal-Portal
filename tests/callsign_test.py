"""Call Sign Lookup page (FCC license data through Callook.info, QRZ link, what this site knows) and the radiosonde station coverage drawn like the ADS-B coverage (sector outline per exact-position station)."""
import os, subprocess, requests
HOST = os.environ.get("TEST_HOST", "test-box.invalid")
B = os.environ.get("MOBILE_BASE", f"http://{HOST}:18430")
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", "docker exec -i adsb-portal-db psql -U portal -d portal -tA"], input=sql, capture_output=True, text=True, timeout=60).stdout
nav = requests.get(B + "/").text
check("Call Sign Lookup is in the menu", "/callsign" in nav and "Call Signs and Talkgroups" in nav)
p = requests.get(B + "/callsign")
check("the page has a search for a call sign", p.status_code == 200 and 'name="call"' in p.text)
r = requests.get(B + "/callsign", params={"call": "W1AW"})
check("a known call sign shows its license record with FCC and QRZ links", "W1AW" in r.text and "FCC License Page" in r.text and "https://www.qrz.com/db/W1AW" in r.text, r.text[:200])
check("a grid square, when shown, is four characters", __import__("re").search(r"Grid square</dt><dd[^>]*>([A-Z0-9]+)", r.text) is None or len(__import__("re").search(r"Grid square</dt><dd[^>]*>([A-Z0-9]+)", r.text).group(1)) == 4)
r = requests.get(B + "/callsign", params={"call": "ZZ9ZZZZ"})
check("an unknown call sign says so and still offers QRZ", "No US license was found" in r.text and "qrz.com/db/ZZ9ZZZZ" in r.text)
check("odd characters are dropped, not run", 'value="SCRIPTX"' in requests.get(B + "/callsign", params={"call": "<script>x"}).text)
psql("DELETE FROM sonde_stations WHERE callsign IN ('ZZCOVX', 'ZZCOVA'); INSERT INTO sonde_stations (callsign, lat, lon, privacy, public, reach_km, reach_sectors) VALUES "
     "('ZZCOVX', 34.7, -92.3, 'exact', true, 120, '[" + ",".join(["80"] * 36) + "]'), ('ZZCOVA', 35.1, -92.0, 'general', true, 90, '[" + ",".join(["60"] * 36) + "]');")
j = {s["id"]: s for s in requests.get(B + "/api/sondes/stations.json").json()["stations"]}
check("an exact-position station carries its 36 sectors", j.get("ZZCOVX", {}).get("sectors") and len(j["ZZCOVX"]["sectors"]) == 36, j.get("ZZCOVX"))
check("an approximate-position station carries its sectors too (drawn around the position shown)", j.get("ZZCOVA", {}).get("sectors") and len(j["ZZCOVA"]["sectors"]) == 36, j.get("ZZCOVA"))
cv = requests.get(B + "/coverage").text
check("the Coverage Map draws stations in their own color like the ADS-B coverage", "hueOf(s.id)" in cv and "#c58bff" not in cv.split('id: "sonde"')[1].split('id: "balloons"')[0])
psql("DELETE FROM sonde_stations WHERE callsign IN ('ZZCOVX', 'ZZCOVA')")
print("ALL PASS" if ok_all else "SOME FAILED")
