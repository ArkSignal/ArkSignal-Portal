"""Site themes: the schedule (themes_unit.py inside the portal container), the Display menu, the Normal Look button when the site put a seasonal theme on, the administrator's page and what a forced theme does.
Needs selenium and Chrome on the PC for the browser part."""
import json, os, re, subprocess, sys, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def ssh(c): return subprocess.run(["ssh", "-q", "nastemp", c], capture_output=True, text=True, timeout=120).stdout
D = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker "
def psql(sql): return ssh(D + f"exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\"").strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
subprocess.run(["scp", "-q", "themes_unit.py", "nastemp:adsb-portal/themes_unit.py"], check=True)
out = ssh("cd ~/adsb-portal && " + D + "cp themes_unit.py adsb-portal:/tmp/themes_unit.py; " + D + "exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/themes_unit.py")
check("themes_unit.py passes inside the portal container", "ALL PASS" in out, out[-400:])
for k, v in (("themes_offseason", "0"), ("theme_force", ""), ("themes_config", "{}"), ("themes_seasonal", "1"), ("themes_enabled", "1"), ("theme_site_default", "dark"), ("themes_decor", "1")):
    setting(k, v)
time.sleep(2)
home = requests.get(B + "/").text
check("every page carries the themes (colors, symbols, which one the site shows) and the script that applies them", "window.ARK_THEMES" in home and "/static/themes.js" in home and '"halloween"' in home)
check("the Display menu has the theme list and the decorations box", 'id="pf-theme"' in home and 'id="pf-deco"' in home)
adm = sess("marty", PW)
pg = adm.get(B + "/admin/themes")
check("Admin > Themes opens with every seasonal theme, their dates and Try It", pg.status_code == 200 and all(x in pg.text for x in ("Halloween", "Thanksgiving", "Christmas", "Independence Day", "Try It", "Show a Theme to Everyone Now")) and 'href="/admin/themes"' in pg.text)
t = tok(adm, "/admin/themes")
form = {"csrf_token": t}
for tid in ("halloween", "thanksgiving", "christmas", "newyear", "july4", "easter", "stpatrick", "valentines", "fall", "winter", "spring", "summer"):
    form["en_" + tid] = "1"
RANGES = {"halloween": ("10-15", "11-01"), "christmas": ("12-01", "12-26"), "newyear": ("12-27", "01-03"), "july4": ("06-28", "07-05"), "stpatrick": ("03-10", "03-17"), "valentines": ("02-07", "02-14"),
          "fall": ("09-22", "11-30"), "winter": ("12-21", "03-19"), "spring": ("03-20", "06-20"), "summer": ("06-21", "09-21")}
for k, (a, b) in RANGES.items():
    form["from_" + k], form["to_" + k] = a, b
bad = dict(form, from_halloween="13-45")
res = adm.post(B + "/admin/themes/save", data=bad, headers={"X-Requested-With": "XMLHttpRequest"})
check("a date that is not month-day is refused", "month-day" in res.text)
adm.post(B + "/admin/themes/save", data=dict(form, force="halloween"), allow_redirects=False)
time.sleep(1)
check("showing a theme to everyone sets it as the site's theme in every page", re.search(r'"site":\s*"halloween"', requests.get(B + "/").text) is not None and psql("select value from settings where key='theme_force'") == "halloween")
adm.post(B + "/admin/themes/save", data=dict(form, force=""), allow_redirects=False)
check("the audit log has the change", int(psql("select count(*) from audit_log where action='themes.save'") or 0) >= 1)
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
except ImportError:
    print("SKIP the browser part: selenium not installed"); print("ALL PASS" if ok_all else "SOME FAILED"); sys.exit(0)
adm.post(B + "/admin/themes/save", data=dict(form, force="halloween"), allow_redirects=False); setting("themes_offseason", "1"); time.sleep(2)   # every theme is listed for the first part
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
d = webdriver.Chrome(options=o)
J = lambda s: d.execute_script(s)
ACC = "return getComputedStyle(document.documentElement).getPropertyValue('--acc').trim()"
TH = "return document.documentElement.getAttribute('data-ark-theme')"
try:
    d.get(B + "/"); time.sleep(2)
    check("with nothing chosen a visitor gets the site's theme: Halloween colors on the page", J(TH) == "halloween" and J(ACC) == "#ff8a1f", (J(TH), J(ACC)))
    check("a Normal Look button is on the page, named for the theme", len(d.find_elements(By.ID, "ark-normal")) == 1 and "Halloween" in d.find_element(By.ID, "ark-normal").text and "Normal look" in d.find_element(By.ID, "ark-normal").text and d.find_element(By.ID, "ark-normal").size["width"] > 600 and d.find_element(By.ID, "ark-normal-btn").is_displayed())
    check("the page is dressed with still things (a ribbon of the theme's colors, a symbol beside the site name, a cobweb, a greeting in the footer) and nothing falls", len(d.find_elements(By.ID, "ark-stripe")) == 1 and len(d.find_elements(By.ID, "ark-badge")) == 1 and len(d.find_elements(By.ID, "ark-art")) == 1 and "Halloween" in J("return document.getElementById('ark-greet').textContent") and len(d.find_elements(By.ID, "ark-deco")) == 0)
    d.get(B + "/sondes/map"); time.sleep(3)   # the map pages carry the balloon icons
    check("on Halloween every balloon in the air gets a Halloween symbol (the same one each time) and the marker carries it", J("return ArkThemes.balloon('R1234567')") is not None and J("return ArkThemes.balloon('R1234567')") == J("return ArkThemes.balloon('R1234567')")
          and J("return SondeIcons.marker('climbing', '#3ee6a8', '', {size: 34, serial: 'R1234567'})").count("border-radius:50%") == 1 and "<svg" not in J("return SondeIcons.marker('climbing', '#3ee6a8', '', {size: 34, serial: 'R1234567'})"))
    check("a marker without a flight, and a balloon on the ground, keep their normal icons", "<svg" in J("return SondeIcons.marker('climbing', '#3ee6a8', '', {size: 34})") and "<svg" in J("return SondeIcons.marker('landed', '#3ee6a8', '', {size: 34, serial: 'R1234567'})"))
    share = J("var n=0; for (var i=0;i<600;i++) { if (ArkThemes.plane('a'+i.toString(16))) n++; } return n")
    check("Halloween gives some of the aircraft its own symbol (about one in six)", 60 <= share <= 150, share)
    d.find_element(By.ID, "ark-normal-btn").click(); time.sleep(1)
    check("in the normal look there are no seasonal symbols on the maps", J("return ArkThemes.balloon('R1234567')") is None and J("return ArkThemes.plane('abc123')") is None)
    check("pressing it goes back to the normal look at once: the dark theme, no button, no decorations", J(TH) == "dark" and len(d.find_elements(By.ID, "ark-normal")) == 0 and J("return document.querySelectorAll('#ark-stripe, #ark-badge, #ark-art, #ark-greet').length") == 0)
    d.get(B + "/"); time.sleep(2)
    check("and it is remembered on the next page", J(TH) == "dark" and len(d.find_elements(By.ID, "ark-normal")) == 0 and J("return JSON.parse(localStorage.getItem('adsb.prefs')).theme") == "normal")
    J("var s=document.getElementById('pf-theme'); s.value='christmas'; s.dispatchEvent(new Event('change'))"); time.sleep(1)
    check("picking Christmas in the Display menu puts on its colors and decorations (and no button: it is their choice)", J(TH) == "christmas" and J(ACC) == "#ff5252" and len(d.find_elements(By.ID, "ark-stripe")) == 1 and len(d.find_elements(By.ID, "ark-lights")) == 1 and len(d.find_elements(By.ID, "ark-normal")) == 0)
    share = J("var n=0; for (var i=0;i<600;i++) { if (ArkThemes.plane('a'+i.toString(16))) n++; } return n")
    check("at Christmas about one aircraft in five is Santa, always the same ones", 80 <= share <= 170 and J("return ArkThemes.plane('a1f') === ArkThemes.plane('a1f')"), share)
    J("var c=document.getElementById('pf-deco'); c.checked=false; c.dispatchEvent(new Event('change'))"); time.sleep(1)
    check("the decorations can be switched off and the colors stay", J("return document.querySelectorAll('#ark-stripe, #ark-badge, #ark-art, #ark-lights, #ark-greet').length") == 0 and J(TH) == "christmas")
    J("var s=document.getElementById('pf-theme'); s.value='light'; s.dispatchEvent(new Event('change'))"); time.sleep(1)
    check("Light still works, and clears the seasonal colors", J("return document.documentElement.getAttribute('data-theme')") == "light" and J(ACC) in ("#0b9a6e", ""), J(ACC))
    J("var s=document.getElementById('pf-theme'); s.value='ocean'; s.dispatchEvent(new Event('change'))"); time.sleep(1)
    check("a color scheme (Ocean) changes the colors", J(ACC) == "#2fd6d0")
    setting("themes_offseason", "0"); J("localStorage.clear()"); time.sleep(1); d.get(B + "/"); time.sleep(2)
    opts = J("return Array.prototype.map.call(document.getElementById('pf-theme').options, function(o){return o.value})")
    check("the Display menu lists site default, normal, auto, the basic looks and color schemes, and the seasonal themes whose dates are on now (plus the person's own out-of-season choice)", all(x in opts for x in ("site", "normal", "auto", "dark", "light", "midnight", "ocean", "halloween", "fall")), opts)
    check("a seasonal theme outside its dates is not offered to anyone else", "july4" not in opts and "thanksgiving" not in opts and "valentines" not in opts, opts)
    J("localStorage.setItem('adsb.prefs', JSON.stringify({theme: 'christmas', themeV: '2'}))"); d.get(B + "/"); time.sleep(2)
    opts2 = J("return Array.prototype.map.call(document.getElementById('pf-theme').options, function(o){return o.value})")
    check("a person who chose Christmas in October still sees it in their own list, marked out of season", "christmas" in opts2 and "out of season" in J("return document.getElementById('pf-theme').selectedOptions[0].text"), opts2)
    J("localStorage.clear()")
    J("localStorage.setItem('adsb.prefs', JSON.stringify({theme: 'dark'}))"); d.get(B + "/"); time.sleep(2)
    check("a look saved before themes existed (Dark, no version) follows the site once: the Halloween theme is on", J(TH) == "halloween" and len(d.find_elements(By.ID, "ark-normal")) == 1, J(TH))
    J("localStorage.setItem('adsb.prefs', JSON.stringify({theme: 'dark', themeV: '2'}))"); d.get(B + "/"); time.sleep(2)
    check("and a Dark chosen since is kept (no seasonal theme, no button)", J(TH) == "dark" and len(d.find_elements(By.ID, "ark-normal")) == 0)
    J("localStorage.clear()")
finally:
    d.quit()
adm.post(B + "/admin/themes/save", data=dict(form, force=""), allow_redirects=False)
setting("theme_force", "")
print("ALL PASS" if ok_all else "SOME FAILED")
