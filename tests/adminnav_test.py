"""The admin menu stays organised: one definition (app/adminnav.py), grouped by purpose in a fixed order, and every admin page is in it.
A new admin page that names itself with active="..." but is not listed there fails here, so the menu cannot drift into a random order."""
import glob, os, re, sys, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, ".."))
import importlib.util
spec = importlib.util.spec_from_file_location("adminnav", os.path.join(here, "..", "app", "adminnav.py"))
nav = importlib.util.module_from_spec(spec); spec.loader.exec_module(nav)

ORDER = ["Start", "People", "Aircraft and Feeders", "Weather and Balloons", "Radio", "Map and Data", "Updates", "Site"]
check("the groups are the agreed ones, in the agreed order", [g for g, _ in nav.GROUPS] == ORDER, [g for g, _ in nav.GROUPS])
keys = nav.keys()
labels = [l for _g, items in nav.GROUPS for _k, l, _h, _p in items]
check("every page is listed once, with its own name", len(keys) == len(set(keys)) and len(labels) == len(set(labels)), keys)
check("every address is an admin address", all(h.startswith("/admin/") for _g, items in nav.GROUPS for _k, _l, h, _p in items))

# every admin page in the code names itself with a key that is in the menu, and every menu entry has a page
used = set()
for f in glob.glob(os.path.join(here, "..", "app", "*.py")):
    src = open(f, encoding="utf-8").read()
    for m in re.finditer(r'render_template\(\s*"(?:admin_[a-z_]+|status)\.html"[^\n]*?active="([a-z_]+)"', src):
        used.add(m.group(1))
    for m in re.finditer(r'"active": "([a-z_]+)"', src):
        used.add(m.group(1))
check("every admin page names itself in the menu definition (add new pages to app/adminnav.py)", used <= set(keys), sorted(used - set(keys)))
check("no entry in the menu is without a page", set(keys) <= used | {"overview", "planefence", "dataupdates", "roadmap", "winlink", "allsettings", "set_aircraft", "set_weather", "set_radio", "set_mapdata", "set_updates"}, sorted(set(keys) - used))

# what people see
class Fake:
    def __init__(self, *perms): self.p = set(perms)
    def has_permission(self, x): return x in self.p
vis = nav.visible(Fake("manage_users"))
check("someone who may only manage users sees Start and that one entry", [(g, [k for k, _l, _h in i]) for g, i in vis] == [("Start", ["overview"]), ("People", ["users"])], vis)
check("someone with every permission sees every group in order", [g for g, _ in nav.visible(Fake("manage_users", "manage_roles", "view_history", "manage_feeders", "manage_settings", "manage_weather", "manage_sondes", "manage_map_data"))] == ORDER)
check("a person with no admin permission sees only the overview", [g for g, _ in nav.visible(Fake())] == ["Start"])

adm = sess("marty", PW)
page = adm.get(B + "/admin/hospitals").text
nav_html = re.search(r'<div class="subnav adminnav".*?<!-- /adminnav -->', page, re.S)
check("the admin page shows the grouped menu", nav_html is not None)
check("it is not a <nav> element (the site styles those as the sticky main menu, which put the main menu's dropdowns underneath it)", "<nav" not in nav_html.group(0))
check("the group you are in is marked, and only that one", re.findall(r'<div class="ag has-active"><span class="agl">([^<]+)</span>', nav_html.group(0)) == ["Map and Data"], re.findall(r'class="ag[^"]*"><span class="agl">([^<]+)', nav_html.group(0)))
html = nav_html.group(0)
shown_groups = re.findall(r'<span class="agl">([^<]+)</span>', html)
check("the groups appear in the fixed order", shown_groups == ORDER, shown_groups)
shown_links = re.findall(r'<a class="[^"]*" href="([^"]+)"', html)
expect = [h for _g, items in nav.GROUPS for _k, _l, h, _p in items]
check("the links appear in the defined order, none missing and none extra", shown_links == expect, (shown_links, expect))
check("the page you are on is the one highlighted, and only that one", re.findall(r'<a class="active" href="([^"]+)"', html) == ["/admin/hospitals"], re.findall(r'<a class="active" href="([^"]+)"', html))
bad = []
for href in expect:
    r = adm.get(B + href)
    if r.status_code != 200:
        bad.append((href, r.status_code))
check("every address in the menu opens", not bad, bad)
same = {re.findall(r'<span class="agl">([^<]+)</span>', re.search(r'<div class="subnav adminnav".*?<!-- /adminnav -->', adm.get(B + h).text, re.S).group(0)).__str__() for h in ("/admin/users", "/admin/settings", "/admin/status", "/admin/interesting", "/admin/data-updates/")}
check("the menu is the same on every admin page", len(same) == 1, same)

# the main menu's dropdowns open on top of the admin menu, not underneath it
try:
    from selenium import webdriver
    import time
    o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1500,900")
    d = webdriver.Chrome(options=o)
    try:
        d.get(B + "/about")
        for c in adm.cookies:
            d.add_cookie({"name": c.name, "value": c.value, "path": "/"})
        d.get(B + "/admin/hospitals"); time.sleep(1.5)
        d.execute_script("document.querySelectorAll('details.navmenu')[0].open = true"); time.sleep(.5)
        top = d.execute_script("""var dd = document.querySelector('details.navmenu[open] .navdrop'); var r = dd.getBoundingClientRect(); var items = dd.querySelectorAll('a'); var last = items[1].getBoundingClientRect();   /* a tall flyout scrolls inside itself: test the second link, which is always in view */
            var hit = document.elementFromPoint(last.left + 10, last.top + last.height / 2); return {inside: dd.contains(hit), bottom: Math.round(r.bottom), adminTop: Math.round(document.querySelector('.adminnav').getBoundingClientRect().top)}""")
        check("the Aircraft dropdown reaches into the admin menu's area and is still on top of it", top["bottom"] > top["adminTop"] and top["inside"], top)
        pop = d.execute_script("""var dd = document.querySelector('details.navmenu[open] .navdrop'), cs = getComputedStyle(dd), bs = getComputedStyle(document.body);
            return {bg: cs.backgroundColor, page: bs.backgroundColor, shadow: cs.boxShadow, top: cs.borderTopWidth, dimmed: getComputedStyle(document.querySelector('main > h1')).opacity}""")
        check("the open dropdown stands out: its own lighter surface, a shadow, an accent edge, and the page behind it steps back", pop["bg"] != pop["page"] and pop["shadow"] != "none" and pop["top"] == "3px" and float(pop["dimmed"]) < 0.6, pop)
        look = d.execute_script("""var l = document.querySelector('.adminnav .agi a:not(.active)'), a = document.querySelector('.adminnav .agi a.active'); var cl = getComputedStyle(l), ca = getComputedStyle(a);
            return {text: cl.color, border: cl.borderTopColor, width: cl.borderTopWidth, activeText: ca.color, activeBorder: ca.borderTopColor}""")
        check("every link is outlined in the grey of its own text, so the links are separated", look["border"] == look["text"] and look["width"] == "1px", look)
        check("the page you are on keeps its green outline", look["activeBorder"] == look["activeText"] and look["activeBorder"] != look["border"], look)
        pos = d.execute_script("return getComputedStyle(document.querySelector('.adminnav')).position")
        check("the admin menu is an ordinary part of the page, not a second sticky bar", pos == "static" or pos == "relative", pos)
    finally:
        d.quit()
except ImportError:
    print("SKIP the browser check: selenium not installed")
print("ALL PASS" if ok_all else "SOME FAILED")
