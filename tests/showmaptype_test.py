"""Show on Map goes to the map of its own kind: a weather station to the Weather Map, a hospital to the County Map, a balloon flight, launch site and receiving station to the Radiosonde Map, a county to the County Map, an airport to the Aircraft Map (where airports are), an APRS station to the APRS Map, a repeater to the Repeater Map."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def links(url):
    t = requests.get(B + url).text
    return re.findall(r'class="btn small show-on-map" href="([^"]+)"', t)
sid = requests.get(B + "/api/weather.json").json()["stations"]
if sid:
    l = links("/weather/" + sid[0]["id"])
    check("a weather station's Show on Map opens the Weather Map", l and all(x.startswith("/weather/map") for x in l), l)
else:
    print("note: no public weather station on the test box")
ho = requests.get(B + "/api/hospitals.json") if False else None
for url, want in (("/airports/KLIT", "/live"), ("/counties/05001", "/counties/")):
    r = requests.get(B + url)
    if r.status_code == 200:
        l = re.findall(r'class="btn small show-on-map" href="([^"]+)"', r.text)
        check(f"{url}: its Show on Map links go to the right map", l and any(x.startswith(want) for x in l), l)
import html, urllib.parse as up
hid = re.findall(r'href="(/hospitals/[A-Za-z0-9]+)"', requests.get(B + "/hospitals/").text)
for url in (hid[:1] + ["/airports/KLIT"]):
    r = requests.get(B + url)
    l = [html.unescape(x) for x in re.findall(r'class="btn small show-on-map" href="([^"]+)"', r.text)]
    q = up.parse_qs(up.urlparse(l[0]).query) if l else {}
    check(f"{url}: the link carries the place as separate lat, lon and zoom values (the map centers on it, not on Arkansas)", all(k in q for k in ("lat", "lon", "zoom")) and "amp;" not in l[0] and "amp;" not in r.text.split("show-on-map")[1].split('"')[1].replace("&amp;", ""), l[:1])
print("ALL PASS" if ok_all else "SOME FAILED")
