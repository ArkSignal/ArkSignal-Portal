"""The whole site: every page is the full width of the window and every section on it is the same width. Fails when a page, section, panel or form sets its own
width (max-width or width in a style attribute; sidebars beside a map and small summary tiles are layout, not sections, and are exempt)."""
import os, re, sys, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
bad = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n, "" if c else x)
    if not c: bad.append(n)
adm = sess("marty", PW)
base_css = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "templates", "base.html"), encoding="utf-8").read()
check("the page container is limited by a setting and centred; full width and map pages are the exceptions", re.search(r"\nmain\{[^}]*max-width:var\(--maxw", base_css) and "html[data-pw=full] main,main.wide{max-width:none}" in base_css and "max-width:1100px" not in base_css)
public = ["/", "/about", "/aircraft", "/hospitals/", "/airports/", "/feeders/", "/status", "/release-notes", "/roadmap", "/contact", "/feed/", "/feed/check", "/feed/weather", "/weather/", "/coverage", "/history/", "/flight/",
          "/support", "/my/login", "/my/register", "/my/forgot"]
mine = sorted({h for h in re.findall(r'href="(/my/[a-z0-9/-]*)"', adm.get(B + "/my/").text)} | {"/my/", "/my/profile", "/my/claim", "/my/alerts"})
pages = [(p, requests) for p in public] + [(p, adm) for p in mine]
SECTION = re.compile(r'<(?:details|form|div|section)\b[^>]*\bclass="[^"]*\b(?:setsec|panel)\b[^"]*"[^>]*>', re.S)
WIDTH = re.compile(r'(?<![-\w])(?:max-width|width)\s*:')
offenders, seen = [], 0
for p, s in pages:
    r = s.get(B + p)
    if r.status_code != 200:
        continue
    seen += 1
    for tag in SECTION.findall(r.text):
        if "flex:0 0" in tag or re.search(r'\bclass="[^"]*\btile\b', tag):
            continue
        m = re.search(r'style="([^"]*)"', tag)
        if m and WIDTH.search(m.group(1)):
            offenders.append((p, tag[:110]))
check(f"{seen} pages were looked at", seen >= 18, [p for p, _ in pages])
check("no section, panel or form on any page sets its own width", not offenders, offenders[:6])
try:
    from selenium import webdriver
except ImportError:
    print("SKIP the browser checks: selenium not installed"); print("ALL PASS" if not bad else "SOME FAILED"); sys.exit(0)
def browse(width, height, label):
    o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument(f"--window-size={width},{height}")
    d = webdriver.Chrome(options=o)
    try:
        d.get(B + "/about")
        for c in adm.cookies:
            d.add_cookie({"name": c.name, "value": c.value, "path": "/"})
        for p, s in pages:
            if s is requests and p in ("/my/login", "/my/register", "/my/forgot"):
                d2 = None
            d.get(B + p); time.sleep(.9)
            info = d.execute_script("""var m=document.querySelector('main'); if(!m) return null; var mr=m.getBoundingClientRect(), cs=getComputedStyle(m);
              var inner=mr.width-parseFloat(cs.paddingLeft)-parseFloat(cs.paddingRight);
              var secs=Array.from(document.querySelectorAll('main > details.setsec, main > form.panel, main > div.panel')).filter(function(e){return e.offsetParent!==null && e.getBoundingClientRect().width>0}).map(function(e){return Math.round(e.getBoundingClientRect().width)});
              return {main: Math.round(mr.width), inner: Math.round(inner), secs: secs, over: document.documentElement.scrollWidth - document.documentElement.clientWidth, path: location.pathname}""")
            if not info or info["path"] in ("/my/login",) and p != "/my/login":
                continue   # redirected to sign in: nothing of this page to measure
            if width >= 1500:
                check(f"{label} {p}: the page uses the window ({info['main']} of {width}) and its sections are {info['inner']} wide", info["main"] >= width * 0.95 and all(abs(w - info["inner"]) <= 2 or (info["path"].startswith("/my/") and abs(w - (info["inner"] - 258)) <= 4) for w in info["secs"]), info)   # a My Account page has its side menu (258 pixels with its gap) beside the sections
            else:
                check(f"{label} {p}: nothing scrolls sideways", info["over"] <= 1, info)
    finally:
        d.quit()
browse(1800, 1000, "wide")
browse(390, 800, "phone")
print("ALL PASS" if not bad else "SOME FAILED")
