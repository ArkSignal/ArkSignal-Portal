"""A repeater's position can be set or corrected by the person who looks after it or an editor, on the map or from an address answer (like hospitals); ordinary visitors cannot."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", "docker exec -i adsb-portal-db psql -U portal -d portal -tA"], input=sql, capture_output=True, text=True, timeout=60).stdout.strip()
psql("DELETE FROM repeaters WHERE callsign='W5POS'; INSERT INTO repeaters (callsign, city, state, lat, lon, freq_hz, offset_hz, modes, source, approved) VALUES ('W5POS', 'Conway', 'AR', NULL, NULL, 147330000, 600000, 'FM', 'local', TRUE)")
rid = psql("select id from repeaters where callsign='W5POS'")
adm = sess("marty", PW); ajax = {"X-Requested-With": "XMLHttpRequest"}
pg = adm.get(B + f"/repeaters/{rid}").text
check("an editor sees Set the Position, open, with a map and an address search", 'id="rp-position"' in pg and "Set the Position" in pg and 'id="rp-pickmap"' in pg and 'id="rp-addr"' in pg)
check("a visitor does not", 'id="rp-position"' not in requests.get(B + f"/repeaters/{rid}").text)
tok = re.search(r'name="csrf_token" value="([^"]+)"', pg).group(1)
r = adm.post(B + f"/repeaters/{rid}/position", data={"csrf_token": tok, "lat": "35.0887", "lon": "-92.4421"}, headers=ajax)
check("an editor saves a position", r.status_code == 200 and psql(f"select round(lat::numeric,3)||','||round(lon::numeric,3) from repeaters where id={rid}") == "35.089,-92.442", r.text[:100])
check("it is in the history of changes", "position" in psql(f"select string_agg(action, ',') from repeater_edits where repeater_id={rid}"))
check("a bad position is refused", adm.post(B + f"/repeaters/{rid}/position", data={"csrf_token": tok, "lat": "95", "lon": "-92"}, headers=ajax).status_code == 400 and adm.post(B + f"/repeaters/{rid}/position", data={"csrf_token": tok, "lat": "", "lon": ""}, headers=ajax).status_code == 400)
check("a visitor cannot", requests.post(B + f"/repeaters/{rid}/position", data={"lat": "1", "lon": "1"}, allow_redirects=False).status_code in (302, 400, 401, 403))
pg2 = adm.get(B + f"/repeaters/{rid}").text
check("now the page shows the position and the map link, and the section says Correct the Position", "Correct the Position" in pg2 and "Show on Repeater Map" in pg2)
check("the repeater is on the repeater map data", any(p["id"] == int(rid) for p in requests.get(B + "/api/repeaters/map.json").json().get("repeaters", requests.get(B + "/api/repeaters/map.json").json().get("points", []))) or str(rid) in requests.get(B + "/api/repeaters/map.json").text)
psql("DELETE FROM repeaters WHERE callsign='W5POS'")
print("ALL PASS" if ok_all else "SOME FAILED")
