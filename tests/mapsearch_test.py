"""The one search and the saved views of the maps. The search API (what it finds, what it never offers, short and odd texts), the saved-views API (names, addresses accepted only on map pages,
replacing a view of the same name, deleting, the limit of 25, one person's views not another's), and in a real browser: typing in a map's search box shows suggestions and picking one flies the
map there, and a view can be saved, listed and applied again (kept in the browser when nobody is signed in). Needs selenium and Chrome on the PC."""
import os, re, sys, time, json, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)

# ---- the search API
def srch(q):
    return requests.get(B + "/api/search.json", params={"q": q}, timeout=30).json()["results"]
check("a text shorter than two letters gives nothing", srch("a") == [] and srch("") == [])
r = srch("klit")
check("an airport is found by its code, with a position and its page", any(x["type"] == "Airport" and x["label"].startswith("KLIT") and x["lat"] and x["href"] == "/airports/KLIT" for x in r), r[:3])
r = srch("little rock")
check("a place and an airport can come from the same text, grouped by kind", {x["type"] for x in r} >= {"Airport"} and all(k in r[0] for k in ("type", "label", "sub", "lat", "lon", "zoom", "href")), [x["type"] for x in r])
air = [x for x in r if x["type"] == "Airport"]
check("Arkansas airports are listed before others", not air or air[0]["sub"].endswith("AR"), [x["sub"] for x in air])
check("odd text is handled without an error", all(isinstance(srch(t), list) for t in ("%%", "'; drop table users;--", "a" * 200, "<script>", "  ", "日本")))
check("there are never more than sixteen suggestions", all(len(srch(t)) <= 16 for t in ("a b", "st", "little", "ar", "w5")))
check("nothing a visitor cannot see on the public pages is offered (no private fields)", all(set(x) == {"type", "label", "sub", "lat", "lon", "zoom", "href"} for x in srch("little")))

# ---- the saved views API
adm = sess("marty", PW)
other = sess("plain1", "plainpassword1")
def post(s, path, body):
    s.headers.update({"X-CSRFToken": tok(s, "/my/profile")})
    return s.post(B + path, json=body)
def clear(s):
    for v in s.get(B + "/my/map-views").json()["views"]:
        post(s, "/my/map-views/delete", {"name": v["name"], "url": v["url"].split("?")[0]})
clear(adm); clear(other)
check("anonymous visitors cannot read or save views on the server", requests.get(B + "/my/map-views", allow_redirects=False).status_code in (302, 401) and requests.post(B + "/my/map-views", json={}, allow_redirects=False).status_code in (302, 400, 401))
good = {"name": "Little Rock", "url": "/live?lat=34.746&lon=-92.289&zoom=11&q=ae", "layers": "{\"base\":\"std\",\"on\":[\"nexrad\"]}"}
r = post(adm, "/my/map-views", good)
check("a view of a map page is saved", r.status_code == 200 and r.json()["views"][-1]["name"] == "Little Rock", r.text[:200])
for bad in ({"name": "", "url": "/live"}, {"name": "x", "url": "https://evil.example/live"}, {"name": "x", "url": "//evil.example/live"}, {"name": "x", "url": "/admin/users"}, {"name": "x", "url": "/live?a=<script>"}, {"name": "x", "url": "/live?" + "a" * 2000}):
    check(f"a bad view is refused ({bad['url'][:24]!r})", post(adm, "/my/map-views", bad).status_code == 400)
r = post(adm, "/my/map-views", dict(good, url="/live?lat=35&lon=-92&zoom=9"))
views = r.json()["views"]
check("saving the same name for the same map replaces the view", len([v for v in views if v["name"] == "Little Rock"]) == 1 and "zoom=9" in [v for v in views if v["name"] == "Little Rock"][0]["url"])
post(adm, "/my/map-views", {"name": "Counties", "url": "/counties/?show=feeders", "layers": ""})
check("views of different maps live side by side", {v["url"].split("?")[0] for v in adm.get(B + "/my/map-views").json()["views"]} == {"/live", "/counties/"})
check("another member does not see them", other.get(B + "/my/map-views").json()["views"] == [])
for k in range(30):
    post(adm, "/my/map-views", {"name": f"v{k}", "url": f"/aprs/map?zoom={k % 18}", "layers": ""})
check("at most 25 views are kept (the oldest go)", len(adm.get(B + "/my/map-views").json()["views"]) == 25)
clear(adm)
check("deleting removes a view", adm.get(B + "/my/map-views").json()["views"] == [])

# ---- in a real browser
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
except ImportError:
    print("SKIP the browser part: selenium not installed"); print("ALL PASS" if ok_all else "SOME FAILED"); sys.exit(0)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
d = webdriver.Chrome(options=o)
try:
    for page in ("/counties/", "/repeaters/map", "/live"):
        d.get(B + page); time.sleep(4)
        box = d.find_element(By.ID, "q"); box.clear(); box.send_keys("klit"); time.sleep(1.5)
        shown = d.execute_script("var b=document.getElementById('ms-box'); return b && b.style.display !== 'none' ? b.querySelectorAll('.ms-i').length : 0")
        check(f"{page}: typing in the search box shows suggestions", shown >= 1, shown)
        d.execute_script("document.querySelector('#ms-box .ms-i').dispatchEvent(new MouseEvent('mousedown', {bubbles: true}))"); time.sleep(1)
        c = d.execute_script("var c = window.arkMap.getCenter(); return [c.lat, c.lng, window.arkMap.getZoom()]")
        check(f"{page}: picking an airport flies the map to it", abs(c[0] - 34.729) < .05 and abs(c[1] + 92.224) < .05 and c[2] >= 12, c)
    d.get(B + "/counties/"); time.sleep(4)
    d.execute_script("localStorage.removeItem('lv.views')")
    d.execute_script("document.querySelector('.fsec[data-k=\"display\"]').open = true")
    check("the Saved Views control is in the Display and View section", d.find_elements(By.ID, "lv-views") and d.find_elements(By.ID, "lv-vsave"))
    d.execute_script("arguments[0].click()", d.find_element(By.ID, "lv-vsave")); time.sleep(.5)
    al = d.switch_to.alert; al.send_keys("My county view"); al.accept(); time.sleep(1)
    opts = d.execute_script("return Array.from(document.querySelectorAll('#lv-views option')).map(function (o) { return o.textContent })")
    check("a saved view is listed (kept in the browser when signed out)", "My county view" in opts, opts)
    stored = d.execute_script("return localStorage.getItem('lv.views')")
    check("it holds the map's address and layer choices", stored and "/counties/?" in stored and "lat=" in stored, stored)
    d.get(B + "/counties/?zoom=6&lat=33&lon=-90"); time.sleep(4)
    d.execute_script("document.querySelector('.fsec[data-k=\"display\"]').open = true")
    d.execute_script("var s=document.getElementById('lv-views'); s.value='0'; s.dispatchEvent(new Event('change', {bubbles: true}))"); time.sleep(4)
    check("choosing it opens that view again", "/counties/?" in d.current_url and "zoom=" in d.current_url and "zoom=6" not in d.current_url, d.current_url)
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
