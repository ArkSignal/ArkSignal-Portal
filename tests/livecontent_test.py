import json, re, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def ssh(cmd): return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True).stdout.strip()
adm = sess("marty", PW)
def many(path, n=24):
    """Several requests, so both of the web server's worker processes answer at least once."""
    return [requests.get(B + path).text for _ in range(n)]
def post(path, data):
    j = adm.post(B + path, data=dict(data, csrf_token=tok(adm, "/admin/updates")), headers={"X-Requested-With": "XMLHttpRequest"}).json(); return j["ok"], j["message"]
base = dict(enabled="1", repo="mallison01/adsb-portal-content", branch="main", minutes="2", token="", base="")

# the built-in copies (live content off first, so the answer does not depend on what the public repository holds today)
_off = dict(base); _off.pop("enabled")
post("/admin/updates/content", _off); many("/roadmap"); many("/release-notes")
rn = requests.get(B + "/release-notes").text
check("Release Notes shows the built-in list when nothing live is configured", "Coming Next" in rn and 'id="v1.0.24"' in rn)
check("the Roadmap page is public and grouped", requests.get(B + "/roadmap").status_code == 200 and any(h in requests.get(B + "/roadmap").text for h in ("Coming in the next release", "In progress", "Recently released")))
rm = requests.get(B + "/roadmap").text
check("it has area filters and plain-language items", 'data-area="Map"' in rm and "Planefence and Plane-Alert" in rm and "Places, roads, counties and hospitals" in rm)
api = requests.get(B + "/api/roadmap.json").json()
check("the roadmap JSON lists the items and says where they came from", len(api["items"]) > 30 and api["source"] in ("built-in", "github"), api["source"])
check("the footer links to it", 'href="/roadmap"' in requests.get(B + "/").text)

# the real public content repository on GitHub (no token needed)
ok, msg = post("/admin/updates/content", base)
ok, msg = post("/admin/updates/content/test", {})
check("the default source is the public content repository on GitHub and it is read without a token", ok and "read from GitHub" in msg, msg)
live = requests.get(B + "/release-notes").text
check("the pages say they are live from the repository", "Live from the project repository" in live and 'id="v1.0.24"' in live)
# a live source: a tiny web server on the test box
ssh("mkdir -p ~/lcserve && cd ~/lcserve && pkill -f 'http.server 18099' ; true")
notes = json.load(open(__import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "..", *r"app\release_notes.json".split("\\")), encoding="utf-8"))
notes[0]["title"] = "LIVE TEST TITLE"; notes[0]["added"] = ["a live change that was never in the build"]
road = {"updated": "2026-10-07", "items": [{"title": "A live roadmap item", "summary": "only on the live server", "status": "next", "area": "Hub"}]}
for name, obj in (("release_notes.json", notes), ("roadmap.json", road)):
    open(name, "w").write(json.dumps(obj)); subprocess.run(["scp", "-q", name, f"nastemp:lcserve/{name}"], check=True, capture_output=True)
import os; [os.remove(f) for f in ("release_notes.json", "roadmap.json")]
subprocess.Popen(["ssh", "-q", "nastemp", "cd ~/lcserve && nohup python3 -m http.server 18099 >/dev/null 2>&1 &"]); time.sleep(3)
ok, msg = post("/admin/updates/content", dict(base, base=f"http://{os.environ.get('TEST_HOST_INNER', os.environ.get('TEST_HOST', 'test-box.invalid'))}:18099"))
check("the live content settings save", ok, msg)
ok, msg = post("/admin/updates/content/test", {})
check("Test Now reads both lists from the live source", ok and "read from GitHub" in msg, msg)
check("Release Notes now shows the live change (whichever worker answers)", all("LIVE TEST TITLE" in t for t in many("/release-notes")))
check("and says it is live", all("Live from the project repository" in t for t in many("/release-notes")))
check("Roadmap shows the live item", all("A live roadmap item" in t for t in many("/roadmap")))
# change it on the server: the page follows within the refresh interval (read it fresh first so the one-minute interval starts now, however slow the earlier checks were)
post("/admin/updates/content/test", {})
road["items"][0]["title"] = "Changed on the server"
open("roadmap.json", "w").write(json.dumps(road)); subprocess.run(["scp", "-q", "roadmap.json", "nastemp:lcserve/roadmap.json"], check=True, capture_output=True); os.remove("roadmap.json")
check("it does not refetch before the interval is up", all("A live roadmap item" in t for t in many("/roadmap")))
time.sleep(128)
check("after the interval the page follows the change", all("Changed on the server" in t for t in many("/roadmap")))
# a broken document and an unreachable server both fall back
open("roadmap.json", "w").write("{not json"); subprocess.run(["scp", "-q", "roadmap.json", "nastemp:lcserve/roadmap.json"], check=True, capture_output=True); os.remove("roadmap.json")
time.sleep(128)
_pages = many("/roadmap")
check("a broken live document keeps showing the last good one", all("Changed on the server" in t for t in _pages), [(("Changed on the server" in t), re.findall(r"Live from[^<]{0,80}|could not[^<]{0,80}|built-in[^<]{0,60}", t)[:2]) for t in _pages])
ok, msg = post("/admin/updates/content", dict(base, base=f"http://{os.environ.get('TEST_HOST_INNER', os.environ.get('TEST_HOST', 'test-box.invalid'))}:18097"))
ok, msg = post("/admin/updates/content/test", {})
check("an unreachable source falls back to the built-in copy and says why", (not ok) and "built-in" in msg and all("Planefence and Plane-Alert" in t for t in many("/roadmap")), msg)
page = adm.get(B + "/admin/updates").text
check("the Updates page shows the Live Content settings and the last error", 'id="sec-live-content"' in page and "mallison01/adsb-portal-content" in page)
check("a bad repository name is refused", not post("/admin/updates/content", dict(base, repo="not a repo"))[0])
plain = sess("plain1", "plainpassword1")
check("an ordinary user cannot change it", plain.post(B + "/admin/updates/content", data={}, allow_redirects=False).status_code in (302, 400, 401, 403, 404))
post("/admin/updates/content", dict(base, base=""))
ssh("pkill -f 'http.server 18099'; rm -rf ~/lcserve")
print("ALL PASS" if ok_all else "SOME FAILED")
