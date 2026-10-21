"""Animation loops: the GOES page (rolling timed pictures, full disk areas, the player, settings, limits) and loops built from the pictures members send (grouping by receiver, satellite, product and time gap,
privacy, hidden pictures). The GOES part needs NOAA's server from the test box and is skipped when it cannot be reached. Runs against the test portal."""
import base64, re, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
def setting(k, v):
    psql(f"INSERT INTO settings (key, value) VALUES ('{k}', '{v}') ON CONFLICT (key) DO UPDATE SET value = '{v}'")
def token(s, path):
    return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + path).text).group(1)
AJAX = {"X-Requested-With": "XMLHttpRequest"}
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
KEYS = ("goes_enabled", "goes_sectors", "goes_products", "goes_loop_frames", "goes_loop_max", "goes_default_view", "goes_loop_keep_hours", "goes_loop_per_request", "goes_fd_size", "satimg_enabled", "satimg_loop_min", "satimg_loop_gap_minutes", "satimg_loop_max")
def reset():
    for k in KEYS:
        psql(f"DELETE FROM settings WHERE key = '{k}'")
reset()
psql("DELETE FROM goes_frames")
psql("DELETE FROM sat_receivers")
anon = requests.Session()
adm = sess("marty", PW)
me = sess("plain1", "plainpassword1")

# ---------------------------------------------------------------- GOES loops
pg = anon.get(B + "/weather/goes", timeout=90).text
check("the GOES page is one viewer: area, newest or loop, length and view controls, the picture, both full disk areas, and no second section for the stills", all(x in pg for x in ("Satellite Viewer", 'id="goes-sector"', "Newest Picture", "Length of the loop", 'data-fit="1"', 'data-product="13"', "/goes/smv/GEOCOLOR/frames.json?n=1", "Full Disk, East", "Full Disk, West", "/static/looper.js")) and "Every View" not in pg and "Animation Loop" not in pg)
d = anon.get(B + "/weather/goes?sector=conus&product=13&mode=loop&n=24", timeout=60).text
check("the choices in the link are respected (area, view, loop mode and length)", "/goes/conus/13/frames.json?n=24" in d and 'value="24" selected' in d and "Clean Infrared" in d)
check("a length that is not offered falls back to the default and a made-up mode to the newest picture", 'value="12" selected' in anon.get(B + "/weather/goes?mode=loop&n=7", timeout=60).text and "frames.json?n=1" in anon.get(B + "/weather/goes?mode=bogus", timeout=60).text)
check("the length choices run up to the longest-loop setting", 'value="48"' in pg and 'value="96"' not in pg)
js = anon.get(B + "/static/looper.js")
check("the player script is served and has play, step, speed, scrub, loop and zoom (buttons, pinch, wheel, double click, pan)", js.status_code == 200 and all(x in js.text for x in ("Pause", "Speed", "looper-scrub", " Loop", "frames", "Zoom in", "Zoom out", "pinch", "wheel", "dblclick", "pointermove", "looper-zoom")) and "ctrlKey" not in js.text and "data-fit" in js.text)
check("a loop for an area or picture that is not offered is a 404", anon.get(B + "/goes/nowhere/GEOCOLOR/frames.json").status_code == 404 and anon.get(B + "/goes/smv/bogus/frames.json").status_code == 404)
check("a frame that is not held is a 404", anon.get(B + "/goes/frame/smv/GEOCOLOR/1000000000.jpg").status_code == 404)
setting("goes_loop_max", "8"); setting("goes_loop_frames", "4"); setting("goes_loop_per_request", "2")
r = anon.get(B + "/goes/smv/GEOCOLOR/frames.json", timeout=120)
live = r.status_code == 200 and (r.json()["frames"] or r.json()["more"])
if not live:
    print("SKIP the live GOES loop checks: NOAA's server could not be reached from the test box", r.status_code)
else:
    j = r.json()
    check("the first ask fetches only the per-request number of pictures and says more are coming", len(j["frames"]) == 2 and j["more"] and j["wanted"] == 4, str(j)[:300])
    for _ in range(6):
        j = anon.get(B + "/goes/smv/GEOCOLOR/frames.json", timeout=120).json()
        if not j["more"]:
            break
    ts = [f["t"] for f in j["frames"]]
    check("asking again completes the loop to the wanted number, oldest first, 5 minutes or so apart", len(j["frames"]) == 4 and not j["more"] and ts == sorted(ts), str(ts))
    im = anon.get(B + j["frames"][-1]["url"])
    check("a frame is a real JPEG served from our copy and cached", im.status_code == 200 and im.content[:3] == b"\xff\xd8\xff" and len(im.content) > 50000 and "immutable" in im.headers.get("Cache-Control", ""))
    check("the frames are stored", psql("select count(*) from goes_frames where sector = 'smv' and product = 'GEOCOLOR'") == "4")
    j6 = None
    for _ in range(8):
        j6 = anon.get(B + "/goes/smv/GEOCOLOR/frames.json?n=6", timeout=120).json()
        if not j6["more"]:
            break
    check("the viewer can ask for a longer loop (n) and gets it, ending at the same newest picture", len(j6["frames"]) == 6 and j6["wanted"] == 6 and j6["frames"][-1]["t"] >= j["frames"][-1]["t"])
    j1 = anon.get(B + "/goes/smv/GEOCOLOR/frames.json?n=1", timeout=120).json()
    check("n=1 is the newest picture alone", len(j1["frames"]) == 1 and j1["frames"][0]["t"] == j6["frames"][-1]["t"])
    j99 = anon.get(B + "/goes/smv/GEOCOLOR/frames.json?n=99", timeout=120).json()
    check("a length above the longest-loop setting is held to it, and a bad one means the default", j99["wanted"] == 8 and anon.get(B + "/goes/smv/GEOCOLOR/frames.json?n=abc", timeout=120).json()["wanted"] == 4)
    setting("goes_loop_max", "3")
    j2 = anon.get(B + "/goes/smv/GEOCOLOR/frames.json", timeout=120).json()
    check("a smaller longest-loop setting trims the stored pictures to the newest ones", len(j2["frames"]) == 3 and psql("select count(*) from goes_frames where sector = 'smv' and product = 'GEOCOLOR'") == "3")
    setting("goes_loop_max", "8"); setting("goes_loop_frames", "3"); setting("goes_loop_per_request", "6"); setting("goes_fd_size", "678x678")
    jf = anon.get(B + "/goes/fd/GEOCOLOR/frames.json", timeout=180).json()
    check("the full disk loop works (small size by setting) and is smaller than a large one would be", len(jf["frames"]) == 3 and len(anon.get(B + jf["frames"][0]["url"]).content) < 900000)
    jw = anon.get(B + "/goes/fd-west/GEOCOLOR/frames.json", timeout=180).json()
    check("the GOES-West full disk loop works", len(jw["frames"]) == 3)
    psql("UPDATE goes_frames SET frame_time = frame_time - interval '30 hours' WHERE sector = 'fd'")
    anon.get(B + "/goes/smv/GEOCOLOR/frames.json", timeout=120)
    check("pictures older than the keep-hours setting are deleted", psql("select count(*) from goes_frames where frame_time < now() - interval '7 hours'") == "0")
    setting("goes_loop_keep_hours", "1")
    psql("UPDATE goes_frames SET frame_time = frame_time - interval '60 minutes' WHERE sector = 'smv'")
    n0 = psql("select count(*) from goes_frames where sector = 'smv'")
    anon.get(B + "/goes/conus/13/frames.json", timeout=120)
    check("the age limit is never shorter than the longest loop needs: with the keep-hours setting at 1 and a longest loop of 8 pictures (40 minutes, plus an hour), pictures an hour old are kept", psql("select count(*) from goes_frames where sector = 'smv'") == n0 and int(n0) > 0, n0)
    psql("UPDATE goes_frames SET frame_time = frame_time - interval '3 hours' WHERE sector = 'smv'")
    anon.get(B + "/goes/conus/13/frames.json", timeout=120)
    check("and pictures past that span are deleted", psql("select count(*) from goes_frames where sector = 'smv'") == "0")
    sv = anon.get(B + "/goes/conus/13/frames.json", timeout=180).json()
    check("another area and picture also loops", len(sv["frames"]) >= 1)
    check("Service Status counts the timed pictures held", "timed picture" in adm.get(B + "/admin/status/data.json").text)

st = adm.get(B + "/admin/settings").text
check("the new settings are in Admin > Settings", all(x in st for x in ("GOES: Default Loop Length", "GOES: Longest Loop", "GOES: Page Opens With", "GOES: Keep Loop Pictures For", "GOES: Pictures Fetched at a Time", "GOES: Loop Speed", "GOES: Full Disk Picture Size", "Satellite Images: Pictures Needed for a Loop", "Satellite Images: Longest Gap in a Loop", "Satellite Images: Most Pictures in a Loop", "Satellite Images: Loop Speed", "Satellite Images: Loops Listed")))
setting("goes_sectors", "smv")
check("an area the settings do not offer has no loop and no page button", anon.get(B + "/goes/fd/GEOCOLOR/frames.json").status_code == 404 and "Full Disk" not in anon.get(B + "/weather/goes", timeout=60).text)
setting("goes_enabled", "0")
check("switched off, loops are gone with the page", anon.get(B + "/goes/smv/GEOCOLOR/frames.json").status_code == 404 and anon.get(B + "/weather/goes").status_code == 404)

# ---------------------------------------------------------------- loops from members' pictures
t = token(me, "/my/sat-receivers")
known = set()
def add(name, public):
    r = me.post(B + "/my/sat-receivers/add", data={"csrf_token": t, "name": name, "kind": "goes", "place": "Conway", **({"public": "1"} if public else {})}, headers=AJAX).json()
    found = [x for x in re.findall(r"/sat/push/([A-Za-z0-9_-]{20,})", r["html"]) if x not in known]
    known.add(found[0])
    return found[0]
tok = add("Loop Dish", True)
URL = B + "/sat/push/" + tok
def send(url, sat, prod, when):
    r = requests.post(url, files={"image": ("a.png", PNG)}, data={"satellite": sat, "product": prod, "captured": when})
    return r.json()["id"] if r.status_code == 200 else None
ids = [send(URL, "GOES-19", "Full Disk", f"2026-10-16T12:{m:02d}:00Z") for m in (0, 10, 20, 30)]
far = send(URL, "GOES-19", "Full Disk", "2026-10-16T18:00:00Z")
other = send(URL, "GOES-19", "Band 13", "2026-10-16T12:05:00Z")
check("the pictures were accepted", all(ids) and far and other)
g = anon.get(B + "/radio/satellite-images").text
check("the gallery lists one loop (four pictures close together; the late one and the other product are not a loop)", "Animation Loops (1)" in g and "4 pictures" in g and f"/radio/satellite-images/loop/{ids[-1]}" in g, g[g.find("Animation Loops") - 20:g.find("Animation Loops") + 400])
pj = anon.get(B + f"/sat/loop/{ids[1]}.json")
fr = pj.json()["frames"] if pj.status_code == 200 else []
check("the loop's pictures come oldest first and only from that series and run", [f["url"] for f in fr] == [f"/sat/image/{i}" for i in ids], str(fr))
lp = anon.get(B + f"/radio/satellite-images/loop/{ids[0]}")
check("the loop page shows the player and says what the loop is", lp.status_code == 200 and f"/sat/loop/{ids[0]}.json" in lp.text and "GOES-19" in lp.text and "4 pictures" in lp.text and "/static/looper.js" in lp.text)
check("the late picture has no loop of its own (too few)", anon.get(B + f"/sat/loop/{far}.json").status_code == 404)
setting("satimg_loop_gap_minutes", "5")
check("a shorter gap setting splits the series so no loop is left", "Animation Loops" not in anon.get(B + "/radio/satellite-images").text and anon.get(B + f"/sat/loop/{ids[0]}.json").status_code == 404)
setting("satimg_loop_gap_minutes", "90"); setting("satimg_loop_max", "3")
check("the most-pictures setting keeps the newest ones", len(anon.get(B + f"/sat/loop/{ids[0]}.json").json()["frames"]) == 3 and anon.get(B + f"/sat/loop/{ids[0]}.json").json()["frames"][-1]["url"] == f"/sat/image/{ids[-1]}")
setting("satimg_loop_max", "36"); setting("satimg_loop_min", "5")
check("the pictures-needed setting hides a loop that is too short", "Animation Loops" not in anon.get(B + "/radio/satellite-images").text)
setting("satimg_loop_min", "3")
# a hidden picture leaves the loop
psql(f"UPDATE sat_images SET hidden = true WHERE id = {ids[1]}")
check("a hidden picture is left out of the loop for the public", [f["url"] for f in anon.get(B + f"/sat/loop/{ids[0]}.json").json()["frames"]] == [f"/sat/image/{i}" for i in (ids[0], ids[2], ids[3])])
check("administrators still see the hidden one in the loop", len(adm.get(B + f"/sat/loop/{ids[0]}.json").json()["frames"]) == 4)
psql(f"UPDATE sat_images SET hidden = false WHERE id = {ids[1]}")
# private receivers
tok2 = add("Private Dish", False)
pids = [send(B + "/sat/push/" + tok2, "NOAA 19", "APT", f"2026-10-15T12:{m:02d}:00Z") for m in (0, 5, 10)]
check("a private receiver's loop is not in the public gallery and is a 404 to the public", "NOAA 19" not in anon.get(B + "/radio/satellite-images").text.split("Pictures (")[0] and anon.get(B + f"/sat/loop/{pids[0]}.json").status_code == 404 and anon.get(B + f"/radio/satellite-images/loop/{pids[0]}").status_code == 404)
check("its owner and an administrator can play it", me.get(B + f"/sat/loop/{pids[0]}.json").status_code == 200 and len(adm.get(B + f"/sat/loop/{pids[0]}.json").json()["frames"]) == 3)
check("a member who does not own it cannot", sess("claimer1", "claimpassword1").get(B + f"/sat/loop/{pids[0]}.json").status_code == 404)
check("the How to Feed page explains that a steady series makes a loop", "makes a loop" in anon.get(B + "/feed/satellites").text)
setting("satimg_enabled", "0")
check("switched off, member loops are gone too", anon.get(B + f"/sat/loop/{ids[0]}.json").status_code == 404)
reset()
psql("DELETE FROM sat_receivers")
psql("DELETE FROM goes_frames")
print("ALL PASS" if ok_all else "SOME FAILED")
