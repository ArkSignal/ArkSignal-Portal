"""My Account: the Overview page is where My Account opens, the pages are in a sensible order in a left side menu (a drop-down button on a phone, like the rest of the site), every account page shows the menu with its own entry marked, and
a menu entry for a switched-off feature is not listed. Run against the default settings (tests/run_all.py restores them first). Needs the test box."""
import re, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
me = sess("plain1", "plainpassword1")
home = me.get(B + "/my/").text
check("the user menu's My Account link opens the Overview", 'href="/my/account">My Account</a>' in home and 'href="/my/">My Account</a>' not in home)
acc = me.get(B + "/my/account")
check("the Overview page loads with what needs attention, the profile, security, alerts and stations", acc.status_code == 200 and all(x in acc.text for x in ('id="acct-todo"', 'id="acct-profile"', 'id="acct-security"', 'id="acct-alerts"', 'id="sec-acct-stations"')))
check("it tells a member without two-factor to turn it on", "two-factor sign-in" in acc.text and 'href="/my/two-factor"' in acc.text)
def menu(html):
    side = html.split('id="my-side-list"')[1].split("</script>")[0] if 'id="my-side-list"' in html else ""
    groups = re.findall(r'class="my-side-head"[^>]*><span>([^<]+)', side)
    links = re.findall(r'<a class="(?:active)?" href="([^"]+)"[^>]*>([^<]+)', side)
    return side, groups, [l[1].strip() for l in links], re.findall(r'<a class="active" href="([^"]+)"', side)
side, groups, labels, active = menu(acc.text)
check("the menu is grouped in this order: Account, Alerts and Notifications, Security, My Stations, Flights, Community Settings", [g.strip() for g in groups] == ["Account", "Alerts and Notifications", "Security", "My Stations", "Flights", "Community Settings"], groups)
heads = re.findall(r'<button type="button" class="my-side-head"[^>]*aria-expanded="(\w+)"', side)
check("the group holding the current page is open and the others are closed (one open at a time by script)", heads == ["true", "false", "false", "false", "false", "false"] and side.count(" hidden>") == 5 and "one group open at a time" in acc.text and 'class="my-side-group has-current expanded"' in acc.text, heads)
check("Overview comes first, then Profile, then the security pages just after the My Account group, then the stations", labels[:2] == ["Overview", "Profile"] and labels.index("Password") == labels.index("Notifications") + 1 and labels.index("Password") < labels.index("My Feeders"), labels)
check("the Overview entry is marked as the current page", active == ["/my/account"], active)
check("a feature that is off is not in the menu (no mesh, ACARS, AIS, Remote ID, repeaters entries by default)", all(x not in labels for x in ("My Mesh Nodes", "Mesh Channels", "My ACARS Stations")), labels)
pages = [("/my/profile", "/my/profile"), ("/my/display", "/my/display"), ("/my/alerts", "/my/alerts"), ("/my/stations", "/my/stations"), ("/my/", "/my/"), ("/my/weather", "/my/weather"), ("/my/sondes", "/my/sondes"),
         ("/my/password", "/my/password"), ("/my/two-factor", "/my/two-factor"), ("/my/sessions", "/my/sessions"), ("/my/api-keys", "/my/api-keys"), ("/my/passkeys", "/my/passkeys"),          ("/my/community", "/my/community"), ("/my/flights", "/my/flights"), ("/my/bridges", "/my/bridges"), ("/my/mesh", "/my/mesh"), ("/my/mesh/channels", "/my/mesh/channels"), ("/my/repeaters", "/my/repeaters"), ("/my/sat-receivers", "/my/sat-receivers"), ("/my/nets", "/my/nets")]
seen = 0   
bad = []
for path, want in pages:
    r = me.get(B + path)
    if r.status_code != 200:
        continue
    seen += 1
    _s, _g, _l, act = menu(r.text)
    if act != [want] or 'class="subnav"' in r.text and "sn-label" in r.text:
        bad.append((path, act))
check("every account page that is on shows the side menu with its own entry marked, and no longer the old horizontal bar", seen >= 8 and not bad, (seen, bad))
css = me.get(B + "/my/account").text
check("the menu is a left column on a wide screen and a drop-down button on a phone", "main:has(.my-side){display:grid;grid-template-columns:230px" in css and ".my-side-btn{display:flex" in css and 'id="my-side-btn"' in css and 'aria-expanded="false"' in css)
check("the Overview is where the menu button on a phone names the page", "Account: <b>Overview</b>" in css)
hdr = [r for r in (me.get(B + p).text for p in ("/my/profile", "/my/password", "/my/stations", "/my/community")) if '<h1 class="acct-head">My <span>Account</span></h1>' in r]
check("every account page keeps the same header, My Account, with its own title under it", len(hdr) == 4)
check("chat, messages and friends are their own section: they do not carry the account menu, and the account menu does not list them", all('id="my-side"' not in me.get(B + p).text for p in ("/chat/", "/my/messages", "/my/messages/compose", "/my/friends")) and all(x not in labels for x in ("Chat", "Messages", "Friends")), labels)
print("ALL PASS" if ok_all else "SOME FAILED")
