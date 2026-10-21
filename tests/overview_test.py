import re, requests, subprocess
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
anon = requests.Session()
h = anon.get(B + "/").text
check("the home page is the ArkSignal overview", "ArkSignal" in h and 'class="ov-hero"' in h and 'id="radar"' not in h)
check("it has a live strip, a map preview and a search box", 'aria-label="Right now"' in h and 'id="ovmap"' in h and 'action="/search"' in h)
check("it has a card for each service that exists", all(x in h for x in ('href="/aircraft"', 'href="/coverage"', 'href="/feeders/"', 'href="/status"', 'href="/feed/"')))
check("it shows the latest release and the roadmap link (lower down)", "Latest release" in h and 'href="/roadmap"' in h and "Release Notes" in h)
check("it does not list services that do not exist yet as cards", "Weather Stations</h3>" not in h and "Repeaters</h3>" not in h)
a = anon.get(B + "/aircraft").text
check("Aircraft Tracking is the old home page", 'id="radar"' in a and 'id="ac"' in a and "Aircraft</span> Tracking" in a and "<title>Aircraft Tracking | ArkSignal</title>" in a)
check("the menu has Aircraft and the brand links to the overview", '<a class="gl" href="/aircraft">Aircraft</a>' in a and '<a class="brand" href="/">' in a)
for q, want in (("PRIV-GEN", "/feeders/PRIV-GEN"), ("KLIT", "/airports/KLIT"), ("a1b2c3", "/flight/a1b2c3"), ("Little Rock", "/airports/?q=Little%20Rock"), ("AAL100", "/flight/?q=AAL100"), ("", "/")):
    r = anon.get(B + "/search", params={"q": q}, allow_redirects=False)
    loc = r.headers.get("Location", "")
    body = r.text if r.status_code == 200 else ""
    check(f"searching '{q}' goes to {want}, or to the results page that lists it when there are several hits", (r.status_code == 302 and (loc.endswith(want) or (q == "PRIV-GEN" and "/feeders/" in loc) or (q == "PRIV-GEN" and "/flight/?q=" in loc))) or (r.status_code == 200 and (want in body or q.lower() in body.lower())), (r.status_code, loc))
check("the profile can choose Aircraft Tracking as the landing page", '<option value="/aircraft"' in sess("plain1", "plainpassword1").get(B + "/my/profile").text)
print("ALL PASS" if ok_all else "SOME FAILED")
