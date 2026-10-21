"""Admin pages: one width for every section (the full width of the admin area, like Admin > Users) and a responsive layout.
Fails when any admin section, panel or form sets its own width (max-width or width in a style attribute): sizing belongs in the shared CSS in base.html."""
import os, re, sys, time
import requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
bad = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n, "" if c else x)
    if not c: bad.append(n)
adm = sess("marty", PW)
home = adm.get(B + "/admin/").text
links = sorted({m for m in re.findall(r'href="(/admin/[a-z0-9/-]*)"', home) if not m.endswith(("template.csv",)) and "/ui/" not in m})
extra = ["/admin/users", "/admin/feeders", "/admin/settings", "/admin/smtp", "/admin/tools", "/admin/contacts", "/admin/layers", "/admin/airports", "/admin/places", "/admin/hospitals",
         "/admin/air-ambulance", "/admin/interesting", "/admin/backups", "/admin/updates", "/admin/data-updates/", "/admin/status", "/admin/support", "/admin/audit", "/admin/signins", "/admin/weather"]
pages = sorted(set(links) | set(extra))
SECTION = re.compile(r'<(?:details|form|div|section)\b[^>]*\bclass="[^"]*\b(?:setsec|panel)\b[^"]*"[^>]*>', re.S)
WIDTH = re.compile(r'(?<![-\w])(?:max-width|width)\s*:')
offenders, seen = [], 0
for p in pages:
    r = adm.get(B + p)
    if r.status_code != 200:
        continue
    seen += 1
    if '<main class="' in r.text and " admin" not in re.search(r'<main class="([^"]*)"', r.text).group(1):
        offenders.append((p, "main is not the admin area"))
    for tag in SECTION.findall(r.text):
        if re.search(r'\bclass="[^"]*\btile\b', tag):
            continue   # the small summary cards are tiles, not sections
        m = re.search(r'style="([^"]*)"', tag)
        if m and WIDTH.search(m.group(1)):
            offenders.append((p, tag[:110]))
check(f"{seen} admin pages were looked at", seen >= 15, pages)
check("no admin section, panel or form sets its own width", not offenders, offenders[:6])
css = adm.get(B + "/admin/users").text
check("the admin area follows the same page width as every page (no cap of its own) and its sections fill it", not re.search(r"main\.admin\{[^}]*max-width", css) and re.search(r"main\.admin details\.setsec[^{]*\{[^}]*max-width:none;width:100%", css))
try:
    from selenium import webdriver
except ImportError:
    print("SKIP the browser checks: selenium not installed"); print("ALL PASS" if not bad else "SOME FAILED"); sys.exit(0)
def browse(width, height, label, paths):
    o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument(f"--window-size={width},{height}")
    d = webdriver.Chrome(options=o)
    try:
        d.get(B + "/about")
        for c in adm.cookies:
            d.add_cookie({"name": c.name, "value": c.value, "path": "/"})
        for p in paths:
            d.get(B + p); time.sleep(1.2)
            info = d.execute_script("""var m=document.querySelector('main'); var mr=m.getBoundingClientRect(); var cs=getComputedStyle(m);
              var inner=mr.width-parseFloat(cs.paddingLeft)-parseFloat(cs.paddingRight);
              var secs=Array.from(document.querySelectorAll('main > details.setsec, main > form.panel, main > #updates-panel')).filter(function(e){return e.offsetParent!==null}).map(function(e){return Math.round(e.getBoundingClientRect().width)});
              return {main: Math.round(mr.width), inner: Math.round(inner), secs: secs, over: document.documentElement.scrollWidth - document.documentElement.clientWidth}""")
            if width >= 1500:
                check(f"{label} {p}: the admin area uses the window ({info['main']} of {width}) and every section is {info['inner']} wide", info["main"] >= width * 0.95 and all(abs(w - info["inner"]) <= 2 for w in info["secs"]) and info["secs"], info)
            else:
                check(f"{label} {p}: nothing scrolls sideways", info["over"] <= 1, info)
    finally:
        d.quit()
paths = ["/admin/settings", "/admin/hospitals", "/admin/air-ambulance", "/admin/interesting", "/admin/smtp", "/admin/support", "/admin/updates", "/admin/places", "/admin/airports"]
browse(1800, 1000, "wide", paths)
browse(390, 800, "phone", paths + ["/admin/users", "/admin/backups"])
print("ALL PASS" if not bad else "SOME FAILED")
