import os
import re, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True


def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra)
    ok_all &= bool(cond)


adm = sess("marty", PW)
anon = requests.Session()
HUB = f"http://{os.environ.get('TEST_HOST', 'test-box.invalid')}:18080"     # what TOOLS_BASE_URL points the test box's tools at (see tests/README.md)
DEFAULT_URLS = {"Heatmap": ("heatmap", "/map/?heatmap&realheat&hideSidebar&hideButtons&lat={lat}&lon={lon}&zoom={zoom_page}", 20),
                "Replay": ("replay", "/map/?replay&lat={lat}&lon={lon}&zoom={zoom_page}", 30),
                }


def set_tool_urls(production):
    """These tests describe the production addresses; the test box normally points the tools at the test hub, so switch and restore."""
    page = adm.get(B + "/admin/tools").text
    for name, (slug, url, order) in DEFAULT_URLS.items():
        m = re.search(r'form="tf-(\d+)" type="text" name="name" value="%s"' % name, page)
        if not m:
            continue
        target = url if production else (HUB + ("/" + url[len("/map/"):] if url.startswith("/map/") else url))
        act(adm, "/admin/tools/save", "/admin/tools", id=m.group(1), name=name, slug=slug, url=target, sort_order=str(order), enabled="1", description="")


set_tool_urls(True)
h = anon.get(B + "/feeders/").text
check("nav shows the seeded tools and no Classic Map any more", "Classic Map" not in h and "Heatmap" in h and "Replay" in h and "Aircraft Statistics" in h and 'href="/view/map"' not in h)
rm = anon.get(B + "/view/map?icao=ae1234", allow_redirects=False)
check("an old link to the classic map opens the live map, on the aircraft it asked for", rm.status_code == 301 and rm.headers["Location"].endswith("/live?icao=ae1234") and anon.get(B + "/view/map", allow_redirects=False).headers["Location"].endswith("/live"), (rm.status_code, rm.headers.get("Location")))
v = anon.get(B + "/view/replay?classic=1").text
check("tool page embeds the address in an iframe inside the portal layout", '<iframe class="tool" src="/map/?replay&amp;lat=34.80&amp;lon=-92.30&amp;zoom=8"' in v and 'class="brand"' in v, re.findall(r'<iframe[^>]*>', v))
check("tool with query string keeps it", 'src="/map/?heatmap&amp;realheat&amp;hideSidebar&amp;hideButtons&amp;lat=34.80&amp;lon=-92.30&amp;zoom=8&amp;heatDuration=24&amp;heatEnd=0"' in anon.get(B + "/view/heatmap?classic=1").text)
check("unknown tool 404", anon.get(B + "/view/nope").status_code == 404)
check("admin tools page loads", adm.get(B + "/admin/tools").status_code == 200)
check("regular user 404 on admin tools", sess("plain1", "plainpassword1").get(B + "/admin/tools").status_code == 404)


def save(**kw):
    d = dict(id="", name="Grafana", slug="grafana", url="/grafana/", sort_order="50", enabled="1")
    d.update(kw)
    for k in [k for k, v in d.items() if v is None]:
        d.pop(k)
    return act(adm, "/admin/tools/save", "/admin/tools", **d)


for label, kw in [("javascript: address", dict(url="javascript:alert(1)")), ("http (not https) address", dict(url="http://evil.example/")),
                  ("protocol-relative address", dict(url="//evil.example/x")), ("address with spaces", dict(url="/a b/")),
                  ("bad slug", dict(slug="Bad Slug!")), ("empty name", dict(name="")), ("bad order", dict(sort_order="abc"))]:
    ok, m, _ = save(**kw)
    check("reject: " + label, not ok, m)
for label, kw in [("portal view page (would nest)", dict(url="/view/map")), ("admin page", dict(url="/admin/")), ("portal feeders page", dict(url="/feeders/")), ("public http address", dict(url="http://example.com/"))]:
    ok, m, _ = save(**kw)
    check("reject: " + label, not ok, m)
ok, m, _ = save(slug="lan-test", name="LAN Test", url="http://test-box.invalid:18080/")
check("plain http allowed for a private-network address", ok, m)
_t = re.search(r'name="id" value="(\d+)"', "")
r = requests.get(B + "/nope-nowhere", headers={"Sec-Fetch-Dest": "iframe"})
check("error page inside a frame is a short message, not the whole portal", r.status_code == 404 and "class=\"brand\"" not in r.text and "not available here" in r.text, r.text[:120])
r = requests.get(B + "/nope-nowhere")
check("error page in a normal window keeps the portal layout", r.status_code == 404 and "class=\"brand\"" in r.text)
hm = anon.get(B + "/view/heatmap?classic=1&duration=48&end=24").text
check("heatmap time controls pass duration and end to the map", "heatDuration=48&amp;heatEnd=24" in hm and 'name="duration"' in hm and 'name="end"' in hm, re.findall(r'src="[^"]*"', hm))
check("heatmap controls show the chosen values selected", re.search(r'<option value="48" selected', hm) and re.search(r'<option value="24" selected', hm))
check("invalid heatmap duration/end fall back to 24 hours, now", "heatDuration=24&amp;heatEnd=0" in anon.get(B + "/view/heatmap?classic=1&duration=999&end=-5").text)
check("the Feeder Statistics dashboard is in the menu and opens", "Aircraft Feeder Statistics" in h and "Network Feeders" not in h and anon.get(B + "/view/network-feeders").status_code == 200)
fh = anon.get(B + "/feeders/").text
check("the Feeders page links to the statistics for all stations", 'href="/view/network-feeders"' in fh)
vs = anon.get(B + "/view/network-feeders?station=KG5DOR").text
check("opening the dashboard for one station selects that station in Grafana", "var-station=KG5DOR" in vs)
check("a station value that is not a plain station ID is ignored", "var-station" not in anon.get(B + "/view/network-feeders?station=a%26b%3D1").text)
check("an embedded tar1090 page is not given a station filter", "var-station" not in anon.get(B + "/view/replay?classic=1&station=KG5DOR").text)
check("non-heatmap tools have no time controls", 'name="duration"' not in anon.get(B + "/view/replay?classic=1").text and 'name="duration"' not in anon.get(B + "/view/statistics").text)
_base = dict(site_name="ArkSignal", registration_open="1", default_privacy="general", feed_mode="open", map_lat="34.80", map_lon="-92.30", map_zoom="7", map_zoom_page="8", timezone="America/Chicago", max_failed_attempts="5", lockout_minutes="15", history_retention_days="730", sample_retention_days="30", feed_host="feed.arksignal.allisononline.org", feed_port="30004", mlat_port="31090", max_rules_per_user="25")
ok, m, _ = act(adm, "/admin/settings/save", "/admin/settings", **dict(_base, map_lat="35.5", map_lon="-93.1", map_zoom_page="7.5"))
check("map defaults accept a decimal zoom and drive the tool maps", ok and "lat=35.5&amp;lon=-93.1&amp;zoom=7.5" in anon.get(B + "/view/replay?classic=1").text, m)
ok, m, _ = act(adm, "/admin/settings/save", "/admin/settings", **dict(_base, map_zoom_page="30"))
check("full-page zoom out of range rejected", not ok, m)
act(adm, "/admin/settings/save", "/admin/settings", **_base)
ok, m, html = save()
check("add a tool", ok and "Grafana" in html, m)
ok, m, _ = save(slug="replay")
check("duplicate short name rejected", not ok, m)
check("new tool appears in nav and works", 'href="/view/grafana"' in anon.get(B + "/feeders/").text and 'src="/grafana/"' in anon.get(B + "/view/grafana").text)
tid = re.search(r'name="id" value="(\d+)"', html.split("Grafana")[0] + html).group(1)
rows = re.findall(r'id="tf-(\d+)"', html)
gid = [i for i in rows if re.search(r'form="tf-%s" type="text" name="name" value="Grafana"' % i, html)][0]
ok, m, _ = save(id=gid, name="Grafana Dashboards", url="https://grafana.example.org/", enabled=None)
check("edit a tool and hide it (https address allowed)", ok, m)
check("hidden tool leaves the nav and its page 404s", "grafana" not in anon.get(B + "/feeders/").text.split("</nav>")[0] and anon.get(B + "/view/grafana").status_code == 404)
ok, m, html = act(adm, f"/admin/tools/{gid}/delete", "/admin/tools")
check("delete a tool", ok and "Grafana" not in html, m)
mapid = re.search(r'form="tf-(\d+)" type="text" name="name" value="Replay"', adm.get(B + "/admin/tools").text).group(1)
ok, m, html = act(adm, f"/admin/tools/{mapid}/delete", "/admin/tools")
check("deleting a seeded tool works and it is not re-seeded", ok and ">Replay<" not in anon.get(B + "/feeders/").text, m)
ok, m, _ = save(id="", name="Replay", slug="replay", url="/map/?replay&lat={lat}&lon={lon}&zoom={zoom_page}", sort_order="30")
check("re-add the replay page", ok, m)
lan = re.search(r'form="tf-(\d+)" type="text" name="name" value="LAN Test"', adm.get(B + "/admin/tools").text)
if lan:
    act(adm, f"/admin/tools/{lan.group(1)}/delete", "/admin/tools")
set_tool_urls(False)
print("ALL PASS" if ok_all else "SOME FAILED")
