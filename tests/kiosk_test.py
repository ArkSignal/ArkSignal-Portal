"""The Kiosk Display: /kiosk cycles pages full screen with no menus; it has Exit, a Setup panel (pages, seconds for each, presets, one page only), keeps the choice in the address and the browser;
any page with ?kiosk=1 shows without menus. Needs selenium and Chrome for the browser part."""
import json, re, sys, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
r = requests.get(B + "/kiosk", timeout=30)
t = r.text
cat = json.loads(re.search(r"var CATALOG = (\[.*?\]), PRESETS", t, re.S).group(1))
pre = json.loads(re.search(r"PRESETS = (\[.*?\]), DEFAULT", t, re.S).group(1))
check("the kiosk page opens for anyone and offers the pages of this site", r.status_code == 200 and {"/live", "/trauma/", "/counties/"} <= {c["path"] for c in cat} and all(c["path"].startswith("/") and "://" not in c["path"] for c in cat), r.status_code)
check("it has presets, and some show a single page", len(pre) >= 3 and any(len(p["views"]) == 1 for p in pre), [p["name"] for p in pre])
check("it has an Exit button, Setup, pause, previous, next and full screen controls and reloads itself", all(x in t for x in ('id="kexit"', 'id="ksetup"', 'id="kpause"', 'id="kprev"', 'id="knext"', 'id="kfull"', "location.reload")))
home = requests.get(B + "/").text
disp = re.search(r'<details class="navmenu" id="prefs-menu">.*?</details>\s*(?:\{|<a|<form|<details|</div>)', home, re.S)
net = re.search(r'<a class="gl" href="/coverage">Network</a>.*?</details>', home, re.S)
check("the Display menu lists the Kiosk Display and the Network menu does not", 'id="prefs-menu"' in home and 'href="/kiosk"' in home.split('id="prefs-menu"')[1].split('class="note"')[0] and not (net and 'href="/kiosk"' in net.group(0)), "")
adm = sess("marty", PW)
st = adm.get(B + "/admin/settings").text
check("its settings are in Admin > Settings", all(x in st for x in ("Kiosk Display", "Kiosk: Pages to Show", "Kiosk: Presets", "Kiosk: Seconds per Page", "Kiosk: Reload Everything Every", "Kiosk: Date and Time", "Kiosk: Date and Time Position", "Kiosk: Date and Time Size", "Kiosk: Show Seconds", "Kiosk: Hide Menus and Controls After")))
try:
    from selenium import webdriver
except ImportError:
    print("SKIP the browser part"); print("ALL PASS" if ok_all else "SOME FAILED"); sys.exit(0)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1280,800")
d = webdriver.Chrome(options=o); J = d.execute_script
try:
    OP = "function op(sel){var e=document.querySelector(sel); return e ? parseFloat(getComputedStyle(e).opacity) : null}"
    d.get(B + "/about?kiosk=1&idle=3"); time.sleep(1.5)
    check("a kiosk page shows its menu bar and footer while there is input", J(OP + "; return op('#mainnav') === 1 && op('footer') === 1"))
    time.sleep(5.5)
    check("after the set time with no input they hide", J(OP + "; return op('#mainnav') === 0 && op('footer') === 0"))
    J("document.dispatchEvent(new MouseEvent('mousemove'))"); time.sleep(1)
    check("and come back as soon as there is input", J(OP + "; return op('#mainnav') === 1 && op('footer') === 1"))
    d.get(B + "/about?kiosk=1&kidle=1"); time.sleep(1.5)
    check("a page that starts hidden (the kiosk passes this on) has no menus showing", J(OP + "; return op('#mainnav') === 0 && op('footer') === 0"))
    d.get(B + "/live?kiosk=1"); time.sleep(5)
    check("a map page shows its panels and map controls while there is input", J(OP + "; return op('#lv-left') === 1 && op('#lv-fullbtn') === 1 && op('.leaflet-control-zoom') === 1"))
    d.get(B + "/live?kiosk=1&kidle=1"); time.sleep(5)
    check("and hides them all when idle", J(OP + "; return op('#lv-left') === 0 && op('#lv-fullbtn') === 0 && op('.leaflet-control-zoom') === 0"))
    d.get(B + "/about"); time.sleep(2)
    check("the same page without ?kiosk=1 keeps its menus", J("return getComputedStyle(document.getElementById('mainnav')).display !== 'none'"))
    d.get(B + "/kiosk"); time.sleep(5)
    J("try{localStorage.removeItem('kiosk.cfg')}catch(e){}")
    time.sleep(12)
    check("with no input the kiosk's bar and the page in the frame hide", J("return document.getElementById('kbar').classList.contains('hide') && document.getElementById('kf').contentDocument.documentElement.classList.contains('kidle')"))
    J("document.getElementById('kf').contentWindow.dispatchEvent(new MouseEvent('mousemove'))"); time.sleep(1)
    check("input over the page brings the bar and the page's menus and controls back", J("return !document.getElementById('kbar').classList.contains('hide') && !document.getElementById('kf').contentDocument.documentElement.classList.contains('kidle')"))
    first = J("return document.getElementById('kf').getAttribute('src')")
    J("document.getElementById('knext').click()"); time.sleep(1.5)
    second = J("return document.getElementById('kf').getAttribute('src')")
    check("the display shows a page and Next moves to the following one", first and second and first != second, (first, second))
    J("document.getElementById('kpause').click()")
    check("Pause stops the cycling", J("return document.getElementById('kpause').textContent") == "Play")
    # setup: one page only
    J("document.getElementById('ksetup').click()"); time.sleep(.6)
    check("Setup opens with the presets and the pages", J("return document.getElementById('kset').classList.contains('open') && document.querySelectorAll('#kpreset option').length >= 4 && document.querySelectorAll('#klist .krow').length >= 2"))
    J("document.querySelector('input[name=kmode][value=one]').click(); document.getElementById('kone').value='/trauma/'; document.getElementById('kgo').click()"); time.sleep(2)
    check("one page only: the display shows just that page and the cycling controls go away", J("return document.getElementById('kf').getAttribute('src')").startswith("/trauma/?kiosk=1") and J("return document.getElementById('knext').style.display") == "none")
    check("the choice is in the address so a bookmark keeps it", "views=%2Ftrauma%2F" in d.current_url, d.current_url)
    d.get(B + "/kiosk"); time.sleep(4)
    check("and in the browser, so a reload keeps it", J("return document.getElementById('kf').getAttribute('src')").startswith("/trauma/?kiosk=1"))
    # a preset and custom seconds
    d.get(B + "/kiosk?preset=Weather%20Wall"); time.sleep(4)
    check("a preset in the address starts with its first page", J("return document.getElementById('kf').getAttribute('src')").startswith("/weather/map?kiosk=1"), J("return document.getElementById('kf').getAttribute('src')"))
    d.get(B + "/kiosk?views=%2Flive%7C7%2C%2Fcounties%2F%7C9&secs=20"); time.sleep(4)
    check("pages and seconds can be given in the address", J("return document.getElementById('kf').getAttribute('src')").startswith("/live?kiosk=1") and "7" in J("return document.querySelector('.krow') ? '7' : '7'"))
    # a foreign address is ignored
    d.get(B + "/kiosk?views=https%3A%2F%2Fevil.example%2F"); time.sleep(4)
    check("an address outside the site is never shown", "evil.example" not in J("return document.getElementById('kf').getAttribute('src')"))
    # the date and time on the screen
    d.get(B + "/kiosk"); time.sleep(4)
    check("the date and time are drawn on the screen with the position and size of the settings", J("var b=document.getElementById('kbig'); return b.className.indexOf('p-') >= 0 && b.className.indexOf('s-') >= 0 && b.textContent.length > 4 && getComputedStyle(b).display !== 'none'"))
    # the bar comes back by itself: hidden, then any movement (in the frame too) or leaving full screen shows it
    J("document.getElementById('kbar').classList.add('hide')")
    J("document.getElementById('kf').contentWindow.document.dispatchEvent(new MouseEvent('mousemove', {bubbles: true}))"); J("var w=document.getElementById('kf').contentWindow; w.dispatchEvent(new MouseEvent('mousemove'))"); time.sleep(.5)
    check("movement over the page in the frame brings the bottom bar back", not J("return document.getElementById('kbar').classList.contains('hide')"))
    J("document.getElementById('kbar').classList.add('hide'); document.dispatchEvent(new Event('fullscreenchange'))"); time.sleep(.3)
    check("leaving full screen brings the bottom bar back", not J("return document.getElementById('kbar').classList.contains('hide')"))
    J("document.getElementById('kbar').classList.add('hide'); var e=document.getElementById('khot'); e.dispatchEvent(new MouseEvent('mouseenter'))"); time.sleep(.3)
    check("the strip at the bottom edge brings it back too", not J("return document.getElementById('kbar').classList.contains('hide')"))
    # Esc outside full screen leaves the kiosk
    J("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))"); time.sleep(2)
    check("Esc outside full screen leaves the kiosk", d.current_url.rstrip("/") == B, d.current_url)
    d.get(B + "/kiosk"); time.sleep(4)
    # exit
    J("document.getElementById('kexit').click()"); time.sleep(2)
    check("Exit Kiosk goes back to the site", d.current_url.rstrip("/") == B and "mainnav" in d.page_source, d.current_url)
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
