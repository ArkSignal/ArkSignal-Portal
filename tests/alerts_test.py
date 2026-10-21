import json, re, requests, subprocess
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True


def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra)
    ok_all &= bool(cond)


s = requests.Session()
t = re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + "/my/register").text).group(1)
s.post(B + "/my/register", data={"csrf_token": t, "username": "alerter1", "email": "alerter1@example.com", "password": "alertpassword1", "confirm": "alertpassword1"})
u = sess("alerter1", "alertpassword1")
other = sess("plain1", "plainpassword1")
check("alerts page loads", u.get(B + "/my/alerts").status_code == 200)
check("alerts require sign-in", requests.get(B + "/my/alerts", allow_redirects=False).status_code == 302)


def save(**kw):
    d = dict(kind="any", value="", name="", area_lat="", area_lon="", area_radius_nm="", alt_min_ft="", alt_max_ft="", cooldown_minutes="30")
    d.update(kw)
    for k in [k for k, v in d.items() if v is None]:
        d.pop(k)
    return act(u, "/my/alerts/save", "/my/alerts", **d)


for label, kw in [("no kind", dict(kind="")), ("icao not hex", dict(kind="icao", value="XYZ123")), ("squawk with digit 8", dict(kind="squawk", value="1289")),
                  ("registration empty", dict(kind="registration", value="")), ("any with no limits", dict(kind="any")),
                  ("area missing radius", dict(kind="military", area_lat="34", area_lon="-92")), ("latitude out of range", dict(kind="military", area_lat="95", area_lon="-92", area_radius_nm="5")),
                  ("min above max", dict(kind="military", alt_min_ft="5000", alt_max_ft="1000")), ("bad cooldown", dict(kind="military", cooldown_minutes="0"))]:
    ok, m, _ = save(**kw)
    check("reject: " + label, not ok, m)

rules = [
    dict(kind="icao", value="a1b2c3", name="R-icao"),
    dict(kind="callsign_prefix", value="aal", name="R-callsign"),
    dict(kind="military", name="R-military"),
    dict(kind="emergency", name="R-emergency"),
    dict(kind="any", area_lat="34.75", area_lon="-92.29", area_radius_nm="5", name="R-area"),
    dict(kind="any", alt_max_ft="1000", name="R-low"),
    dict(kind="squawk", value="4321", name="R-squawk"),
    dict(kind="type", value="c17", name="R-type"),
    dict(kind="registration", value="n123aa", name="R-reg"),
    dict(kind="military", name="R-disabled"),
]
html = ""
for r in rules:
    ok, m, html = save(**r)
    check("add rule " + r["name"], ok, m)
check("rules listed on the page", all(r["name"] in html for r in rules))
rid = {n: i for i, n in re.findall(r'id="rule-(\d+)".*?role-name">([^<]+)<', html, re.S)}
ok, m, _ = act(u, f"/my/alerts/{rid['R-disabled']}/toggle", "/my/alerts")
check("turn a rule off", ok and "now off" in m, m)
ok, m, html = save(id=rid["R-reg"], kind="registration", value="N123AA", name="R-reg", cooldown_minutes="30")
check("edit a rule", ok, m)
check("another user cannot toggle, edit or delete my rule",
      other.post(B + f"/my/alerts/{rid['R-icao']}/toggle", data={"csrf_token": tok(other, "/my/")}).status_code == 404 and
      other.post(B + f"/my/alerts/{rid['R-icao']}/delete", data={"csrf_token": tok(other, "/my/")}).status_code == 404 and
      other.post(B + "/my/alerts/save", data={"csrf_token": tok(other, "/my/"), "id": rid["R-icao"], "kind": "military"}).status_code == 404)

out = subprocess.run(["ssh", "-q", "nastemp", "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/alerts_tick.py"], capture_output=True, text=True)
line = [l for l in out.stdout.splitlines() if l.startswith("{")]
check("evaluator script ran", bool(line), out.stderr[-300:] + out.stdout[-300:])
res = json.loads(line[-1])
un = res["unit"]
check("match: icao", un["icao"] == ["a1b2c3"], un["icao"])
check("match: callsign prefix ignores trailing spaces and case", un["callsign"] == ["a1b2c3"], un["callsign"])
check("match: military via dbFlags", un["military"] == ["ae1234"], un["military"])
check("match: emergency by squawk or emergency field", un["emergency"] == ["c0ffee"], un["emergency"])
check("match: squawk (aircraft without position)", un["squawk"] == ["00face"], un["squawk"])
check("match: type and registration", un["type"] == ["ae1234"] and un["registration"] == ["a1b2c3"])
check("match: area radius around KLIT", sorted(un["area_klit_5nm"]) == ["a1b2c3", "beef01"], un["area_klit_5nm"])
check("match: altitude bounds (ground counts as 0 ft; unknown altitude never matches a bound)", un["alt_le_1000"] == ["beef01"] and un["alt_ge_30000"] == ["a1b2c3", "00face"], (un["alt_le_1000"], un["alt_ge_30000"]))
check("match: aircraft with no position never matches an area", un["no_position_never_in_area"] == [])
t1, t2, t3 = res["counts"]
check("first pass creates events for every enabled matching rule", t1 == 10, t1)
check("second pass inside the repeat interval creates none", t2 == t1, (t1, t2))
check("after the repeat interval it alerts again", t3 == 2 * t1, (t1, t3))
pr = res["per_rule"]
check("per-rule event counts are right (each fired twice)", pr["R-icao"] == 2 and pr["R-callsign"] == 2 and pr["R-military"] == 2 and pr["R-emergency"] == 2
      and pr["R-area"] == 4 and pr["R-low"] == 2 and pr["R-squawk"] == 2 and pr["R-type"] == 2 and pr["R-reg"] == 2, pr)
check("disabled rule never fires", pr["R-disabled"] == 0, pr)
page = u.get(B + "/my/alerts").text
check("matches show on the user's page with a map link", "R-military" in page and "/live?icao=ae1234" in page)
check("other users do not see these matches", "R-military" not in other.get(B + "/my/alerts").text)
check("map link opens the live map", u.get(B + "/live?icao=ae1234").status_code == 200)
check("a bad icao in an old classic map link is ignored (it opens the live map)", u.get(B + "/view/map?icao=zzz", allow_redirects=False).headers.get("Location", "").endswith("/live"))
ok, m, html = act(u, f"/my/alerts/{rid['R-icao']}/delete", "/my/alerts")
check("delete a rule (its events go too)", ok and "R-icao" not in html.split("Recent Matches")[0], m)
ok, m, _ = act(adm := sess("marty", PW), "/admin/settings/save", "/admin/settings", site_name="ArkSignal", registration_open="1", default_privacy="general", feed_mode="open", map_lat="34.80", map_lon="-92.30", map_zoom="7", timezone="America/Chicago", max_failed_attempts="5", lockout_minutes="15", history_retention_days="730", sample_retention_days="30", feed_host="feed.arksignal.allisononline.org", feed_port="30004", mlat_port="31090", max_rules_per_user="3")
check("admin lowers the rules-per-user limit", ok, m)
ok, m, _ = save(kind="military", name="over limit")
check("rule limit enforced", not ok and "up to 3" in m, m)
act(adm, "/admin/settings/save", "/admin/settings", site_name="ArkSignal", registration_open="1", default_privacy="general", feed_mode="open", map_lat="34.80", map_lon="-92.30", map_zoom="7", timezone="America/Chicago", max_failed_attempts="5", lockout_minutes="15", history_retention_days="730", sample_retention_days="30", feed_host="feed.arksignal.allisononline.org", feed_port="30004", mlat_port="31090", max_rules_per_user="25")
print("ALL PASS" if ok_all else "SOME FAILED")
