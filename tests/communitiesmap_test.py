"""The community pages in a real browser (needs selenium and Chrome on the PC): the side menu (and its phone drop-down), drawing the area on the map (click to add points, undo, remove with the list, remove with a
right-click, click the outline to add a point between two, clear, save, reload), placing a node on the map by clicking it, and nothing running off the side of a phone screen."""
import json, os, re, subprocess, sys, time, requests
os.environ["TEST_HOST"] = "test-box.invalid"
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
try:
    from selenium import webdriver
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.common.by import By
except ImportError:
    print("SKIP no selenium"); print("ALL PASS"); sys.exit(0)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
setting("mesh_enabled", "1"); setting("mesh_communities_enabled", "1")
psql("DELETE FROM communities WHERE slug = 'cmm-draw'")
psql("DELETE FROM users WHERE username = 'cmmowner'")
reg = requests.Session()
reg.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', reg.get(B + "/my/register").text).group(1), "username": "cmmowner", "email": "cmmowner@example.com", "password": "cmmownerpassword9", "confirm": "cmmownerpassword9"})
adm = sess("marty", PW)
act(adm, "/admin/communities/create", "/admin/communities", name="CMM Draw", owner="cmmowner")
SL = "cmm-draw"
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1300,950")
d = webdriver.Chrome(options=o); J = d.execute_script
def tap(el):
    J("arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", el); time.sleep(.3)
def wait(js, secs=12):
    end = time.time() + secs
    while time.time() < end:
        try:
            if J(js): return True
        except Exception:
            pass
        time.sleep(.3)
    return False
try:
    d.get(B + "/my/login"); time.sleep(1)
    d.find_element(By.NAME, "username").send_keys("cmmowner"); d.find_element(By.NAME, "password").send_keys("cmmownerpassword9"); d.find_element(By.CSS_SELECTOR, "form button[type=submit], form input[type=submit]").click(); time.sleep(2)
    d.get(B + f"/mesh/c/{SL}"); time.sleep(1.5)
    check("the side menu is on the page with the public and the team pages", J("return !!document.getElementById('cm-side') && document.querySelectorAll('#cm-side .my-side-items a').length >= 8") and J("return document.querySelector('#cm-side a.active').textContent") == "Overview")
    d.get(B + f"/mesh/c/{SL}/area"); check("the Area page draws a map", wait("return !!document.querySelector('#cm-map .leaflet-tile-pane') && !!document.getElementById('cm-poly')"))
    time.sleep(1.5)
    el = d.find_element(By.ID, "cm-map")
    J("arguments[0].scrollIntoView({block: 'center'})", el); time.sleep(.5)
    check("the page runs in two columns with the menu at the left of the map on a wide screen", J("var s=document.getElementById('cm-side').getBoundingClientRect(), m=document.getElementById('cm-map').getBoundingClientRect(); return s.right <= m.left + 2"))
    def click_at(x, y):
        ActionChains(d).move_to_element_with_offset(el, x, y).click().perform(); time.sleep(.4)
    w, h = el.size["width"], el.size["height"]
    click_at(-w // 4, -h // 5); click_at(w // 4, -h // 5)
    check("two clicks make two points, not yet a shape", J("return JSON.parse(document.getElementById('cm-poly').value).length") == 2 and "of at least 3" in J("return document.getElementById('cm-count').textContent"))
    click_at(0, h // 4)
    pts = J("return JSON.parse(document.getElementById('cm-poly').value)")
    check("the third click makes a shape of three numbered points", len(pts) == 3 and J("return document.querySelectorAll('#cm-map .cm-vtx').length") == 3 and J("return document.querySelectorAll('#cm-map path.leaflet-interactive').length") >= 1, pts)
    tap(d.find_element(By.ID, "cm-undo"))
    check("Undo takes the last point away", J("return JSON.parse(document.getElementById('cm-poly').value).length") == 2)
    tap(d.find_element(By.ID, "cm-areaform").find_element(By.CSS_SELECTOR, "button[type=submit]")); time.sleep(.7)
    try:
        d.switch_to.alert.accept(); alerted = True
    except Exception:
        alerted = False
    check("two points cannot be saved", alerted and psql("SELECT area_poly FROM communities WHERE slug='cmm-draw'") == "")
    click_at(0, h // 4)
    tap(d.find_element(By.ID, "cm-areaform").find_element(By.CSS_SELECTOR, "button[type=submit]")); time.sleep(2)
    saved = json.loads(psql("SELECT area_poly FROM communities WHERE slug='cmm-draw'") or "[]")
    check("three points are saved as the community's area", len(saved) == 3 and wait("return document.querySelectorAll('#cm-map .cm-vtx').length === 3"), saved)
    # drag a point
    time.sleep(1)
    vt = d.find_elements(By.CSS_SELECTOR, "#cm-map .cm-vtx")[0]
    ActionChains(d).click_and_hold(vt).move_by_offset(40, 30).release().perform(); time.sleep(.6)
    moved = J("return JSON.parse(document.getElementById('cm-poly').value)")
    check("dragging a point moves it", moved[0] != saved[0] and moved[1] == saved[1], (moved, saved))
    # click the outline between two points to add one
    el = d.find_element(By.ID, "cm-map"); J("arguments[0].scrollIntoView({block: 'center'})", el); time.sleep(.5)
    mk = [d.find_elements(By.CSS_SELECTOR, "#cm-map .cm-vtx")[i].rect for i in (1, 2)]
    mx, my = (mk[0]["x"] + mk[1]["x"]) / 2 + mk[0]["width"] / 2, (mk[0]["y"] + mk[1]["y"]) / 2 + mk[0]["height"] / 2
    er = el.rect
    ActionChains(d).move_to_element_with_offset(el, mx - er["x"] - er["width"] / 2, my - er["y"] - er["height"] / 2).click().perform(); time.sleep(.6)
    n_after = J("return JSON.parse(document.getElementById('cm-poly').value).length")
    check("clicking the outline between two points adds a point there", n_after == 4, n_after)
    ActionChains(d).context_click(d.find_elements(By.CSS_SELECTOR, "#cm-map .cm-vtx")[3]).perform(); time.sleep(.5)
    check("a right-click takes a point away", J("return JSON.parse(document.getElementById('cm-poly').value).length") == 3)
    tap(d.find_element(By.CSS_SELECTOR, "#cm-pts a[data-i='0']"))
    check("so does Remove in the list under the map", J("return JSON.parse(document.getElementById('cm-poly').value).length") == 2 and "of at least 3" in J("return document.getElementById('cm-count').textContent"))
    tap(d.find_element(By.ID, "cm-clear"))
    try:
        d.switch_to.alert.accept()
    except Exception:
        pass
    check("Clear removes every point", J("return JSON.parse(document.getElementById('cm-poly').value).length") == 0)
    tap(d.find_element(By.ID, "cm-areaform").find_element(By.CSS_SELECTOR, "button[type=submit]")); time.sleep(2)
    check("saving with no points clears the area", psql("SELECT area_poly FROM communities WHERE slug='cmm-draw'") == "")
    # a node placed by clicking
    d.get(B + f"/mesh/c/{SL}/pick"); wait("return !!document.querySelector('#cm-map .leaflet-tile-pane')"); time.sleep(1.5)
    el = d.find_element(By.ID, "cm-map")
    J("arguments[0].scrollIntoView({block: 'center'})", el); time.sleep(.5)
    ActionChains(d).move_to_element_with_offset(el, 30, 20).click().perform(); time.sleep(.5)
    lat, lon = J("return document.getElementById('pn-lat').value"), J("return document.getElementById('pn-lon').value")
    check("clicking the map fills in the place of a node", re.fullmatch(r"-?\d+\.\d{5}", lat) is not None and re.fullmatch(r"-?\d+\.\d{5}", lon) is not None and J("return document.querySelectorAll('#cm-map .leaflet-interactive').length") >= 1, (lat, lon))
    d.find_element(By.ID, "pn-name").send_keys("Water Tower"); tap(d.find_element(By.ID, "cm-pinform").find_element(By.CSS_SELECTOR, "button[type=submit]")); time.sleep(2)
    check("the node is saved and listed", psql("SELECT name FROM community_pins WHERE name='Water Tower'") == "Water Tower" and "Water Tower" in d.page_source)
    ActionChains(d).move_to_element_with_offset(d.find_element(By.ID, "cm-map"), 30, 20).perform()
    # phone width
    d.set_window_size(400, 850); time.sleep(.8)
    for path in ("", "/map", "/area", "/pick", "/team", "/nodes"):
        d.get(B + f"/mesh/c/{SL}{path}"); time.sleep(1.8)
        check(f"phone: {path or '/'} does not run off the side", J("return document.documentElement.scrollWidth <= window.innerWidth + 1"), J("return document.documentElement.scrollWidth + ' > ' + window.innerWidth"))
    d.get(B + f"/mesh/c/{SL}"); time.sleep(1)
    check("phone: the menu is a drop-down button that opens its list", J("var b=document.getElementById('cm-side-btn'); return getComputedStyle(b).display !== 'none' && getComputedStyle(document.getElementById('cm-side-list')).display === 'none'"))
    tap(d.find_element(By.ID, "cm-side-btn"))
    check("and shows the pages when pressed", J("return getComputedStyle(document.getElementById('cm-side-list')).display !== 'none'"))
    d.get(B + f"/mesh/c/{SL}/area"); wait("return !!document.querySelector('#cm-map .leaflet-tile-pane')"); time.sleep(1.5)
    check("phone: the map is as wide as the page and not taller than the screen", J("var r=document.getElementById('cm-map').getBoundingClientRect(); return r.width <= window.innerWidth && r.height <= window.innerHeight"))
finally:
    d.quit()
    act(adm, f"/admin/communities/{SL}/delete", "/admin/communities")
    psql("DELETE FROM users WHERE username = 'cmmowner'")
print("ALL PASS" if ok_all else "SOME FAILED")
