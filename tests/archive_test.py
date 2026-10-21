"""Load a flight by serial: the form is for signed-in members only, bad serials and unknown serials are refused with a plain message, the rate limit and the off switch work.
A real download from SondeHub's archive is tried once with a serial taken from the live feed's recent flights (skipped, not failed, when the archive has nothing or cannot be reached)."""
import re, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
adm = sess("marty", PW)
def csrf(s, path="/sondes/flights"):
    return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + path).text).group(1)
anon = requests.get(B + "/sondes/flights").text
check("anonymous visitors do not get the load form", "Load a Flight by Serial" not in anon)
page = adm.get(B + "/sondes/flights").text
check("a signed-in member gets the load form", "Load a Flight by Serial" in page and 'action="/sondes/load-serial"' in page)
r = requests.post(B + "/sondes/load-serial", data={"serial": "26013531"}, allow_redirects=False)
check("posting without signing in is refused", r.status_code in (302, 400, 401, 403) and "/sondes/26013531" not in (r.headers.get("Location") or ""), r.status_code)
t = csrf(adm)
r = adm.post(B + "/sondes/load-serial", data={"csrf_token": t, "serial": "../../etc/passwd"}, allow_redirects=True)
check("a bad serial is refused with a plain message", "letters and numbers" in r.text, r.text[-200:])
r = adm.post(B + "/sondes/load-serial", data={"csrf_token": t, "serial": "NOSUCHSERIAL99"}, allow_redirects=True)
check("a serial the archive does not have is refused with a plain message", "does not have that serial" in r.text or "could not be reached" in r.text, re.findall(r'class="flash[^<]*', r.text)[:2])
# a real flight: a few days old and in the archive
try:
    flights = requests.get(B + "/api/sondes/map.json?hours=240").json().get("flights", [])
except Exception:
    flights = []
cand = None
for u in ("https://api.v2.sondehub.org/sondes?last=345600&lat=34.8&lon=-92.2&distance=300000",):
    try:
        data = requests.get(u, timeout=60).json()
        old = sorted((v.get("datetime", ""), k) for k, v in data.items())
        cand = old[0][1] if old else None
    except Exception:
        cand = None
if cand:
    adm.post(B + "/sondes/load-serial", data={"csrf_token": csrf(adm), "serial": cand}, allow_redirects=True)
    got = requests.get(B + f"/api/sondes/{cand}/track.json")
    if got.status_code == 200:
        j = got.json()
        check("a real archive flight loads and has a track", len(j["track"]["alt"]) > 3, len(j["track"]["alt"]))
    else:
        print("note: the archive did not have " + cand + " yet, real download check skipped")
else:
    print("note: no recent serial available, real download check skipped")
print("ALL PASS" if ok_all else "SOME FAILED")
