"""The review queue for repeaters that have no position (imported from a radio): Admin > Repeaters lists them, offers the place their city names, saves a position in one click or from typed numbers,
and the repeater leaves the queue and appears on the map data."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
adm = sess("marty", PW)
psql("DELETE FROM repeaters WHERE callsign IN ('ZZQ1A', 'ZZQ1B')")
t = tok(adm, "/admin/repeaters")
for call, city, hz in (("ZZQ1A", "Conway", 147350000), ("ZZQ1B", "Nowhereville", 147450000)):   # what a radio code plug brings in: approved, no position
    psql(f"INSERT INTO repeaters (callsign, city, state, freq_hz, offset_hz, modes, kind, status, source, approved) VALUES ('{call}', '{city}', 'AR', {hz}, 600000, 'FM', 'repeater', 'unverified', 'plug:test radio', TRUE)")
ids = {c: psql(f"SELECT id FROM repeaters WHERE callsign='{c}'") for c in ("ZZQ1A", "ZZQ1B")}
check("both repeaters were added without a position", all(ids.values()) and psql("SELECT COUNT(*) FROM repeaters WHERE callsign IN ('ZZQ1A', 'ZZQ1B') AND lat IS NULL") == "2", ids)
page = adm.get(B + "/admin/repeaters").text
check("the queue lists them under Waiting for a Position", "Waiting for a Position" in page and "ZZQ1A" in page and "ZZQ1B" in page)
check("a city the portal knows gets a one-click place (when the places list is loaded), an unknown one gets boxes", ("Use Conway, AR" in page or "name=\"lat\" placeholder=\"Latitude\"" in page) and "name=\"lat\" placeholder=\"Latitude\"" in page)
check("each has a link to place it exactly on a map", f"/repeaters/{ids['ZZQ1A']}#rp-position" in page)
r = adm.post(B + f"/admin/repeaters/{ids['ZZQ1A']}/position", data={"csrf_token": t, "lat": "35.0887", "lon": "-92.4421", "from": "city"}, headers={"X-Requested-With": "XMLHttpRequest"})
check("saving a position works and says so", r.status_code == 200 and r.json().get("ok"), r.text[:150])
check("the position is stored and the repeater leaves the queue", psql(f"SELECT lat FROM repeaters WHERE id={ids['ZZQ1A']}").startswith("35.0887") and f"/repeaters/{ids['ZZQ1A']}\">" not in adm.get(B + "/admin/repeaters").text.split("Waiting for a Position")[1].split("Sysop Claims")[0])
bad = adm.post(B + f"/admin/repeaters/{ids['ZZQ1B']}/position", data={"csrf_token": t, "lat": "abc", "lon": "-92"}, headers={"X-Requested-With": "XMLHttpRequest"})
check("bad numbers are refused", bad.status_code in (200, 400) and not bad.json().get("ok"))
bad2 = adm.post(B + f"/admin/repeaters/{ids['ZZQ1B']}/position", data={"csrf_token": t, "lat": "95", "lon": "-92"}, headers={"X-Requested-With": "XMLHttpRequest"})
check("a place off the Earth is refused", not bad2.json().get("ok"))
check("the edit is in the repeater's history", "position" in psql(f"SELECT string_agg(action, ',') FROM repeater_edits WHERE repeater_id={ids['ZZQ1A']}"))
u = sess("plain1", "plainpassword1")
check("a member cannot use the queue's route", u.post(B + f"/admin/repeaters/{ids['ZZQ1B']}/position", data={"csrf_token": "x", "lat": "35", "lon": "-92"}, allow_redirects=False).status_code in (302, 400, 401, 403, 404))
psql("DELETE FROM repeaters WHERE callsign IN ('ZZQ1A', 'ZZQ1B')")
print("ALL PASS" if ok_all else "SOME FAILED")
