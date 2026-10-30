"""My Account menu and top bar: the user name in the top bar is a link to My Account, Notifications sit in an Alerts and Notifications group (not under Community Settings),
and My Places has an address, city and ZIP search that fills in the coordinates."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
adm = sess("marty", PW)
home = adm.get(B + "/").text
check("the user name in the top bar is a link to My Account", re.search(r'<details class="navmenu" id="me-menu"><summary><a class="gl" href="/my/account">[^<]+</a>', home) is not None)
check("the menu under it still has My Account and Sign out", 'id="me-menu"' in home and "Sign out" in home)
check("the phone drawer keeps the name as a plain link (the group toggle ignores it)", "navmenu:not(#me-menu)>summary a.gl" in home)

pg = adm.get(B + "/my/notifications")
t = pg.text
check("the Notifications page opens", pg.status_code == 200 and "My Places" in t, pg.status_code)
groups = re.findall(r'<span>([^<]+)</span><span class="my-side-caret"', t)
check("the account menu has an Alerts and Notifications group after Account", "Alerts and Notifications" in groups and groups.index("Alerts and Notifications") == groups.index("Account") + 1, groups)
def group_items(name):
    m = re.search(r'<span>' + re.escape(name) + r'</span>.*?<div class="my-side-items"[^>]*>(.*?)</div>', t, re.S)
    return re.findall(r'<a [^>]*href="([^"]+)"', m.group(1)) if m else []
check("it holds Aircraft Alerts and Notifications", group_items("Alerts and Notifications") == ["/my/alerts", "/my/notifications"], group_items("Alerts and Notifications"))
check("Account no longer holds Aircraft Alerts and Community Settings no longer holds Notifications", "/my/alerts" not in group_items("Account") and "/my/notifications" not in group_items("Community Settings"), (group_items("Account"), group_items("Community Settings")))
check("the Notifications page highlights itself in the new group", 'class="active" href="/my/notifications"' in t)
check("Aircraft Alerts is in the same group", 'class="active" href="/my/alerts"' in adm.get(B + "/my/alerts").text and "Alerts and Notifications" in adm.get(B + "/my/alerts").text)

check("My Places has the address, city and ZIP search", 'id="pl-q"' in t and 'id="pl-go"' in t and "Find an address, city or ZIP code" in t and "/api/geocode.json" in t)
check("the search still leaves Use My Location and the coordinates", 'id="pl-here"' in t and 'id="pl-lat"' in t and 'id="pl-lon"' in t)
g = adm.get(B + "/api/geocode.json?q=72201")
check("the search it calls answers with places that have names and coordinates", g.status_code == 200 and isinstance(g.json().get("results"), list) and all({"label", "lat", "lon"} <= set(x) for x in g.json()["results"]), g.text[:200])
check("a one-letter search gives nothing", adm.get(B + "/api/geocode.json?q=a").json()["results"] == [])
print("ALL PASS" if ok_all else "SOME FAILED")
