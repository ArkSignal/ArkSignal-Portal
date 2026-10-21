import re, requests, subprocess, time
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
adm = sess("marty", PW)
def search(q, **kw):
    return requests.get(B + "/api/places/search.json", params=dict(q=q, **kw)).json()["results"]
check("the Places admin page needs sign-in", requests.get(B + "/admin/places", allow_redirects=False).status_code in (302, 401, 403, 404))
check("the Places admin page loads for an administrator", adm.get(B + "/admin/places").status_code == 200 and "Census Bureau" in adm.get(B + "/admin/places").text)
check("an ordinary user cannot import", sess("plain1", "plainpassword1").post(B + "/admin/places/import", data={}, allow_redirects=False).status_code in (302, 400, 401, 403, 404))
j = adm.post(B + "/admin/places/import", data={"csrf_token": tok(adm, "/admin/places")}, headers={"X-Requested-With": "XMLHttpRequest"}).json()
check("the Census files download and import", j["ok"] and "cities and towns" in j["message"], j)
check("the page shows the counts", re.search(r'class="tv">(\d+)</div><div class="tl">ZIP Areas', adm.get(B + "/admin/places").text) and int(re.search(r'class="tv">(\d+)</div><div class="tl">ZIP Areas', adm.get(B + "/admin/places").text).group(1)) > 30000)
r = search("Little Rock")
check("a city name finds the Arkansas city first", r and r[0]["label"] == "Little Rock, AR" and abs(r[0]["lat"] - 34.73) < 0.2 and r[0]["kind"] == "place", r[:2])
r = search("72201")
check("a ZIP code finds its area", r and r[0]["label"] == "ZIP 72201" and r[0]["kind"] == "zip", r[:2])
r = search("722")
check("part of a ZIP code lists matching areas", len(r) >= 5 and all(x["kind"] == "zip" and x["label"].startswith("ZIP 722") for x in r), r[:2])
r = search("Pulaski")
check("a county is found", any(x["label"] == "Pulaski County, AR" and x["kind"] == "county" for x in r), r)
r = search("Springfield, MO")
check("a state after a comma narrows the search", r and all(x["label"].endswith(", MO") for x in r) and r[0]["label"] == "Springfield, MO", r[:3])
r = search("Springfield")
check("home states come first", r and r[0]["label"].endswith(", AR"), r[:3])
r = search("Conway")
check("Conway, AR is found", any(x["label"] == "Conway, AR" for x in r), r)
check("one letter or nothing returns nothing", search("a") == [] and search("") == [])
check("odd characters do not break it", requests.get(B + "/api/places/search.json", params={"q": "%_';--"}).status_code == 200)
check("the settings are on the page", all(f'name="{k}"' in adm.get(B + "/admin/settings").text for k in ("places_year", "places_base_url", "places_home_states")))
check("the About page credits the Census Bureau", "Census Bureau" in requests.get(B + "/about").text)
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,1500")
    d = webdriver.Chrome(options=o)
    try:
        d.get(B + "/live"); time.sleep(3)
        box = d.find_element(By.ID, "q"); box.send_keys("Hot Springs"); time.sleep(1.5)
        links = d.find_elements(By.CSS_SELECTOR, "#lv-places a")
        check("typing a city offers places on the live map", links and "Hot Springs" in links[0].text, [l.text for l in links])
        before = d.execute_script("var c=lvDebug().zoom;return c")
        d.execute_script("document.getElementById('lv-tools').scrollTop = 0"); time.sleep(.3)   # the results are at the top of the panel; the panel is long now, so show them
        links[0].click(); time.sleep(1.2)
        check("clicking a place moves the map to it", d.execute_script("return lvDebug().zoom") >= 11, d.execute_script("return lvDebug().zoom"))
    finally:
        d.quit()
except ImportError:
    print("note: selenium not installed, browser part skipped")
print("ALL PASS" if ok_all else "SOME FAILED")
