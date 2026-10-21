"""Ultra-wide monitors: page content is limited to a comfortable width by default and centred; the map pages use the whole window; each person can choose full width (Display menu or
profile page, saved with the account); the site default is a setting."""
import json, re, subprocess, sys, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
bad = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n, "" if c else x)
    if not c: bad.append(n)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
try:
    from selenium import webdriver
except ImportError:
    print("SKIP: selenium not installed"); sys.exit(0)
adm = sess("marty", PW)
psql("delete from settings where key in ('page_width_mode','page_max_width')")
def browser(w=3400, h=1000):
    o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument(f"--window-size={w},{h}")
    d = webdriver.Chrome(options=o)
    d.get(B + "/about")
    for c in adm.cookies:
        d.add_cookie({"name": c.name, "value": c.value, "path": "/"})
    return d
def geom(d, path, wait=1.5):
    d.get(B + path); time.sleep(wait)
    return d.execute_script("""var m=document.querySelector('main'); var r=m.getBoundingClientRect(); var vw=document.documentElement.clientWidth; var n=document.querySelector('nav').getBoundingClientRect();
        return {main: Math.round(r.width), left: Math.round(r.left), right: Math.round(vw - r.right), vw: vw, nav: Math.round(n.width), pw: document.documentElement.getAttribute('data-pw'), over: document.documentElement.scrollWidth - vw}""")
d = browser()
try:
    g = geom(d, "/about")
    check("an ultra-wide window: page content is limited (about 1800 px of 3400) and centred", 1790 <= g["main"] <= 1860 and abs(g["left"] - g["right"]) <= 3 and g["pw"] == "limited", g)
    check("the top bar still spans the whole window", g["nav"] >= g["vw"] - 2, g)
    check("the home page, the lists and the admin pages are limited the same way", all(1790 <= geom(d, p)["main"] <= 1860 for p in ("/", "/hospitals/", "/admin/hospitals", "/admin/")))
    g = geom(d, "/live", 3)
    check("the live map is not limited: it uses the whole window", g["main"] >= g["vw"] * 0.95, g)
    g = geom(d, "/skyview/abc123") if False else geom(d, "/live", 1)
    d.get(B + "/about"); time.sleep(1)
    # the person chooses full width in the Display menu
    d.execute_script("var s=document.getElementById('pf-width'); s.value='full'; s.dispatchEvent(new Event('change',{bubbles:true}))"); time.sleep(1)
    g = geom(d, "/about")
    check("choosing Full width in the Display menu uses the whole window, and it is remembered", g["main"] >= g["vw"] * 0.95 and g["pw"] == "full", g)
    check("it is saved with the account", json.loads(psql("select prefs from users where username='marty'") or "{}").get("pageWidth") == "full", psql("select prefs from users where username='marty'"))
    d.get(B + "/my/profile"); time.sleep(1.5)
    sel = d.execute_script("var s=document.getElementById('pf-width-profile'); return s ? s.value : null")
    check("the profile page has the same choice and shows it", sel == "full", sel)
    d.execute_script("var s=document.getElementById('pf-width-profile'); s.value='limited'; s.dispatchEvent(new Event('change',{bubbles:true}))"); time.sleep(1)
    g = geom(d, "/hospitals/")
    check("choosing Limited on the profile page limits it again", 1790 <= g["main"] <= 1860, g)
    d.get(B + "/about"); d.execute_script("var s=document.getElementById('pf-width'); s.value=''; s.dispatchEvent(new Event('change',{bubbles:true}))"); time.sleep(1)
    check("choosing the site default clears the choice", json.loads(psql("select prefs from users where username='marty'") or "{}").get("pageWidth", "") == "")
finally:
    d.quit()
# the site default is a setting, and so is the limit
psql("insert into settings (key, value) values ('page_width_mode', 'full') on conflict (key) do update set value='full'")
d = browser()
try:
    g = geom(d, "/about")
    check("when the site default is full width, everyone without a choice gets it", g["main"] >= g["vw"] * 0.95 and g["pw"] == "full", g)
finally:
    d.quit()
psql("delete from settings where key='page_width_mode'")
psql("insert into settings (key, value) values ('page_max_width', '1400') on conflict (key) do update set value='1400'")
d = browser()
try:
    g = geom(d, "/about")
    check("the limit itself is a setting (1400 px here)", 1390 <= g["main"] <= 1460, g)
finally:
    d.quit()
psql("delete from settings where key in ('page_width_mode','page_max_width')")
d = browser(1400, 900)
try:
    g = geom(d, "/about")
    check("a normal window is unaffected: the content uses it all", g["main"] >= g["vw"] - 2 and g["over"] <= 1, g)
finally:
    d.quit()
print("ALL PASS" if not bad else "SOME FAILED")
