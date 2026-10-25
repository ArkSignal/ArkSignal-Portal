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
NAVRE = r'<div class="my-side adminside" id="ad-side".*?</script>'
nav_html = re.search(NAVRE, page, re.S)
check("the admin page shows the grouped menu", nav_html is not None)
check("it is not a <nav> element (the site styles those as the sticky main menu and hides their children on a phone)", "<nav" not in nav_html.group(0) and 'role="navigation"' in nav_html.group(0))
check("the group you are in is marked, and only that one", re.findall(r'<div class="my-side-group has-current expanded">\s*<button[^>]*><span>([^<]+)</span>', nav_html.group(0)) == ["Map and Data"], re.findall(r'class="my-side-group[^"]*">\s*<button[^>]*><span>([^<]+)', nav_html.group(0)))
html = nav_html.group(0)
shown_groups = re.findall(r'class="my-side-head"[^>]*><span>([^<]+)</span>', html)
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
same = {str(re.findall(r'class="my-side-head"[^>]*><span>([^<]+)</span>', re.search(NAVRE, adm.get(B + h).text, re.S).group(0))) for h in ("/admin/users", "/admin/settings", "/admin/status", "/admin/interesting", "/admin/data-updates/")}
check("the menu is the same on every admin page", len(same) == 1, same)
full = adm.get(B + "/admin/users").text
check("the page's title comes before the menu (it runs across the top)", full.index("<h1") < full.index('id="ad-side"') < full.index("<table") if "<table" in full else full.index("<h1") < full.index('id="ad-side"'))

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
        pos = d.execute_script("var m = document.getElementById('ad-side'), h = document.querySelector('main > h1'); var rm = m.getBoundingClientRect(), rh = h.getBoundingClientRect(); return {menuLeft: Math.round(rm.left), menuTop: Math.round(rm.top), titleBottom: Math.round(rh.bottom), titleWidth: Math.round(rh.width), mainLeft: Math.round(document.querySelector('main').getBoundingClientRect().left), sticky: getComputedStyle(m).position, openGroups: document.querySelectorAll('#ad-side .my-side-items:not([hidden])').length}")
        check("on a wide screen the menu is a column down the left side, under the title, and stays in view as the page scrolls", pos["menuLeft"] < 100 and pos["menuTop"] >= pos["titleBottom"] - 2 and pos["sticky"] == "sticky", pos)
        check("only the group of the current page is open", pos["openGroups"] == 1, pos)
        act_ = d.execute_script("var a = document.querySelector('#ad-side a.active'); var c = getComputedStyle(a); return {href: a.getAttribute('href'), border: c.borderTopColor, text: c.color}")
        check("the page you are on is highlighted in the accent color", act_["href"] == "/admin/hospitals" and act_["border"] == act_["text"], act_)
        d.execute_script("document.querySelector('#ad-side a[href=\"/admin/places\"]').click()"); time.sleep(2)
        now = d.execute_script("return {url: location.pathname, title: document.querySelector('main > h1').textContent, active: document.querySelector('#ad-side a.active').getAttribute('href'), n: document.querySelectorAll('#ad-side').length}")
        check("clicking another admin page swaps the page in place: new address, new title, the menu marks the new page, one menu only", now["url"] == "/admin/places" and "Places" in now["title"] and now["active"] == "/admin/places" and now["n"] == 1, now)
        d.back(); time.sleep(2)
        check("the back button returns to the earlier page", d.execute_script("return location.pathname") == "/admin/hospitals" and "Hospitals" in d.execute_script("return document.querySelector('main > h1').textContent"))
        d.set_window_size(412, 900); time.sleep(1)
        ph = d.execute_script("var b = document.getElementById('ad-side-btn'), l = document.getElementById('ad-side-list'); var r = b.getBoundingClientRect(); var before = getComputedStyle(l).display; b.click(); return {btn: getComputedStyle(b).display, tall: Math.round(r.height), words: b.textContent.trim(), listBefore: before, listAfter: getComputedStyle(l).display, noscroll: document.documentElement.scrollWidth <= window.innerWidth}")
        check("on a phone the menu is one button naming the current page that opens the list, and the page does not scroll sideways", ph["btn"] == "flex" and ph["tall"] >= 44 and "Hospitals" in ph["words"] and ph["listBefore"] == "none" and ph["listAfter"] == "block" and ph["noscroll"], ph)
        d.set_window_size(1500, 900); time.sleep(1)
        d.get(B + "/admin/hospitals"); time.sleep(1.5)
        d.execute_script("document.querySelectorAll('details.navmenu')[0].open = true"); time.sleep(.5)
        top = d.execute_script("""var dd = document.querySelector('details.navmenu[open] .navdrop'); var items = dd.querySelectorAll('a'); var last = items[1].getBoundingClientRect();
            var hit = document.elementFromPoint(last.left + 10, last.top + last.height / 2); return {inside: dd.contains(hit)}""")
        check("the main menu's dropdown opens on top of the admin page", top["inside"], top)
    finally:
        d.quit()
except ImportError:
    print("SKIP the browser check: selenium not installed")
print("ALL PASS" if ok_all else "SOME FAILED")
