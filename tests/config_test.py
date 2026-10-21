import json, re, requests, subprocess, time
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True


def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra)
    ok_all &= bool(cond)


def ssh(cmd):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker {cmd}"], capture_output=True, text=True)


def sql(q):
    return ssh(f"exec adsb-portal-db psql -U portal -d portal -At -c \"{q}\"").stdout


adm = sess("marty", PW)
anon = requests.Session()
BASE = dict(site_name="ArkSignal", registration_open="1", default_privacy="general", feed_mode="open", map_lat="34.80", map_lon="-92.30",
            map_zoom="7", map_zoom_page="8", timezone="America/Chicago", max_failed_attempts="5", lockout_minutes="15", history_retention_days="730",
            sample_retention_days="30", feed_host="feed.arksignal.allisononline.org", feed_port="30004", mlat_port="31090", max_rules_per_user="25",
            collector_poll_seconds="30", offline_after_seconds="90", claim_same_ip_minutes="10", claim_paste_days="7", check_feeding_hours="24",
            general_precision_decimals="1", default_rank_period="24h", default_rank_sort="positions", heat_default_duration="24", page_size="50",
            map_tile_url="https://tile.openstreetmap.org/{z}/{x}/{y}.png", map_attribution="&copy; OpenStreetMap contributors",
            leaflet_base_url="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4", elevation_api_url="https://api.open-meteo.com/v1/elevation",
            airports_source_url="https://davidmegginson.github.io/ourairports-data/", min_password_length="10", session_hours="12",
            alert_poll_seconds="15", alert_default_cooldown_minutes="30", pre_restore_keep="5")


def save(**kw):
    d = dict(BASE)
    d.update(kw)
    return act(adm, "/admin/settings/save", "/admin/settings", **d)


page = adm.get(B + "/admin/settings").text
labels = ["Hub Poll Interval", "Mark Offline After", "Same-Network Claim Window", "Claim by UUID Window", "Am I Feeding Lookback", "General Location Precision",
          "Default Ranking Period", "Default Ranking Order", "Default Heatmap Duration", "Rows per Page", "Map Tile Server Address", "Map Credit Text",
          "Map Library Address", "Elevation Lookup Address", "Airport Data Address", "Minimum Password Length", "Longest Sign-In Session",
          "Alert Check Interval", "Default Alert Repeat Interval", "Safety Backups to Keep"]
check("settings page shows every tunable", all(l in page for l in labels), [l for l in labels if l not in page])

for label, kw in [("poll interval too low", dict(collector_poll_seconds="2")), ("offline not a number", dict(offline_after_seconds="x")),
                  ("precision above 3", dict(general_precision_decimals="9")), ("bad rank period", dict(default_rank_period="1y")),
                  ("bad rank order", dict(default_rank_sort="name")), ("bad heat duration", dict(heat_default_duration="5")),
                  ("tile url without {z}{x}{y}", dict(map_tile_url="https://tiles.example.org/tile.png")),
                  ("tile url over public http", dict(map_tile_url="http://tiles.example.org/{z}/{x}/{y}.png")),
                  ("airport url without trailing slash", dict(airports_source_url="https://example.org/data")),
                  ("elevation url not a url", dict(elevation_api_url="nonsense")), ("password length 4", dict(min_password_length="4")),
                  ("session of 0 hours", dict(session_hours="0")), ("page size 3", dict(page_size="3")), ("empty map credit", dict(map_attribution=""))]:
    ok, m, _ = save(**kw)
    check("reject: " + label, not ok, m)

# ---- minimum password length drives registration
ok, m, _ = save(min_password_length="12"); check("set minimum password length to 12", ok, m)
check("register page shows the configured length", "At least 12 characters" in anon.get(B + "/my/register").text)
s = requests.Session()
t = re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + "/my/register").text).group(1)
r = s.post(B + "/my/register", data={"csrf_token": t, "username": "cfguser1", "email": "cfg1@example.com", "password": "elevenchars", "confirm": "elevenchars"})
check("11-character password rejected at length 12", "at least 12" in r.text, re.findall(r'class="flash[^>]*>([^<]*)', r.text))
r = s.post(B + "/my/register", data={"csrf_token": t, "username": "cfguser1", "email": "cfg1@example.com", "password": "twelvecharss", "confirm": "twelvecharss"}, allow_redirects=False)
check("12-character password accepted", r.status_code == 302, r.status_code)
save()

# ---- session length drives the cookie lifetime (the cookie lives as long as the longest limit that can apply to anyone)
def cookie_ttl(user, pw):
    s = requests.Session()
    t = re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + "/my/login").text).group(1)
    s.post(B + "/my/login", data={"csrf_token": t, "username": user, "password": pw}, allow_redirects=False)
    c = [c for c in s.cookies if c.name == "session"][0]
    return (c.expires - time.time()) / 3600 if c.expires else None


save(session_hours="1", admin_session_hours="1")
h1 = cookie_ttl("marty", PW)
save(session_hours="48", admin_session_hours="8")
h48 = cookie_ttl("marty", PW)
check("sign-in cookie lifetime follows the session setting (1 h, then 48 h)", h1 and 0.9 < h1 < 1.1 and h48 and 47 < h48 < 49, (h1, h48))
save()

# ---- rows per page
for i in range(14):
    x = requests.Session()
    tt = re.search(r'name="csrf_token" value="([^"]+)"', x.get(B + "/my/login").text).group(1)
    x.post(B + "/my/login", data={"csrf_token": tt, "username": "pagetest", "password": "x" * 12})
save(page_size="10")
h = adm.get(B + "/admin/signins?q=pagetest").text
rows = len(re.findall(r"<tr><td class=\"dim\"", h))
check("rows per page follows the setting (10 rows, 2 pages)", rows == 10 and "page 1 of 2" in h, (rows, re.findall(r"page \d of \d", h)))
save()

# ---- location precision and map settings (public test feeders)
subprocess.run(["ssh", "-q", "nastemp", "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/mkpublic.py"], check=True, capture_output=True)
api = anon.get(B + "/api/feeders.json").json()
a = [f for f in api["feeders"] if f["station_id"] == "PUB-ALPHA"][0]
check("default precision rounds to 0.1 degree", a["lat"] == 34.3 and a["lon"] == -92.4, a)
check("default circle radius for General privacy is about 5.5 km (each station carries its own radius; this is the fallback)", '"radius_m": 5566' in anon.get(B + "/feeders/").text and "|| 5566" in anon.get(B + "/feeders/").text)
save(general_precision_decimals="2")
api = anon.get(B + "/api/feeders.json").json()
a = [f for f in api["feeders"] if f["station_id"] == "PUB-ALPHA"][0]
check("precision 2 rounds to 0.01 degree and the circle shrinks", a["lat"] == 34.32 and a["lon"] == -92.41 and "|| 556," in anon.get(B + "/feeders/").text, a)
save(general_precision_decimals="0")
a = [f for f in anon.get(B + "/api/feeders.json").json()["feeders"] if f["station_id"] == "PUB-ALPHA"][0]
check("precision 0 rounds to whole degrees", a["lat"] == 34 and a["lon"] == -92, a)
save()
save(default_rank_period="7d", default_rank_sort="uptime")
h = anon.get(B + "/feeders/").text
check("default ranking period and order follow the settings", re.search(r'<option value="7d" selected', h) and re.search(r'<option value="uptime" selected', h))
check("explicit choices still win", re.search(r'<option value="24h" selected', anon.get(B + "/feeders/?period=24h").text))
save()
save(map_tile_url="https://tiles.example.org/{z}/{x}/{y}.png", map_attribution="Tiles by Example", leaflet_base_url="https://cdn.example.org/leaflet")
h = anon.get(B + "/feeders/").text
check("map pages use the configured tile server, credit and library address",
      '"https://tiles.example.org/{z}/{x}/{y}.png"' in h and '"Tiles by Example"' in h and 'src="https://cdn.example.org/leaflet/leaflet.js"' in h and "openstreetmap" not in h.lower())
save()
save(heat_default_duration="48")
h = anon.get(B + "/view/heatmap?classic=1").text
check("heatmap default duration follows the setting", "heatDuration=48" in h and re.search(r'<option value="48" selected', h))
check("heatmap explicit duration still wins", "heatDuration=6&" in anon.get(B + "/view/heatmap?classic=1&duration=6").text)
save()

# ---- elevation and airport data sources
ok, m, _ = save(elevation_api_url="https://invalid.invalid/elevation")
c1 = sess("claimer1", "claimpassword1")
e = c1.get(B + "/my/elevation?lat=34.3&lon=-92.4")
check("elevation lookup uses the configured service (bad one fails clearly)", e.status_code == 502 and "did not respond" in e.json()["message"], e.text[:150])
save()
e = c1.get(B + "/my/elevation?lat=34.315231&lon=-92.410357").json()
check("default elevation service works again", e.get("ok") and 280 < e["feet"] < 340, e)
save(airports_source_url="https://invalid.invalid/data/")
ok, m, _ = act(adm, "/admin/airports/import", "/admin/airports", countries="US")
check("airport import uses the configured source (bad one fails clearly)", not ok and "could not be updated" in m, m)
save()
ok, m, _ = act(adm, "/admin/airports/import", "/admin/airports", countries="US")
check("default airport source works again", ok, m)

# ---- claim windows
ip = re.search(r"\(([0-9a-f.:]+)\)\.", c1.get(B + "/my/claim").text).group(1)
subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/mkfeeders.py {ip}"], check=True, capture_output=True)
A, OLD, OTHER = "testaaaa-0000-4000-8000-000000000001", "testbbbb-0000-4000-8000-000000000003", "testcccc-0000-4000-8000-000000000004"
sql(f"update feeders set last_seen = now() - interval '5 minutes', online = true where uuid = '{A}'")
check("a feeder seen 5 minutes ago is claimable from the list at the default 10 minute window", A in c1.get(B + "/my/claim").text)
save(claim_same_ip_minutes="3")
check("with a 3 minute window it is no longer offered", A not in c1.get(B + "/my/claim").text)
save()
c2 = sess("claimer2", "claimpassword1")


def claim(s, uuid):
    t = tok(s, "/my/claim")
    return s.post(B + "/my/claim", data={"csrf_token": t, "uuid": uuid}, allow_redirects=False)


save(claim_paste_days="1")
r = claim(c2, OTHER)
check("pasted UUID seen 2 days ago is refused when the window is 1 day, and the message says so", r.status_code == 200 and "last 1 day," in r.text, re.findall(r"last \d+ days?[^<]*", r.text))
save(claim_paste_days="14")
r = claim(c2, OLD)
check("pasted UUID seen 10 days ago is accepted when the window is 14 days", r.status_code == 302, r.status_code)
save()
sql(f"update feeders set last_seen = now() - interval '5 hours', online = false where uuid = '{OTHER}'")
sql(f"update feeders set owner_id = null where uuid = '{OTHER}'")
sql(f"update feeders set last_ip = '{ip}' where uuid = '{OTHER}'")
check("Am I Feeding looks back 24 hours by default", OTHER in anon.get(B + "/feed/?check=1").text)
save(check_feeding_hours="1")
h = anon.get(B + "/feed/?check=1").text
check("with a 1 hour lookback it finds nothing and says so", OTHER not in h and "last 1 hour." in h, re.findall(r"in the last [^<.]*", h))
save()

# ---- alert default repeat interval
save(alert_default_cooldown_minutes="45")
al = sess("alerter1", "alertpassword1") if False else c1
check("new alert form starts with the configured repeat interval", re.search(r'name="cooldown_minutes" value="45"', c1.get(B + "/my/alerts").text) is not None)
save()

# ---- safety backups to keep (last: restores revert data to the backup, settings are at defaults here)
save(pre_restore_keep="2")
ok, m, html = act(adm, "/admin/backups/run", "/admin/backups")
name = re.findall(r"portal-backup-\d{8}-\d{6}-manual\.tar\.gz", html)[0]   # the newest, made just now with the setting in it
for _ in range(4):
    act(adm, f"/admin/backups/{name}/restore", "/admin/backups")
    time.sleep(1.1)
    adm = sess("marty", PW)
pre = re.findall(r"portal-backup-\d{8}-\d{6}-pre-restore\.tar\.gz", adm.get(B + "/admin/backups").text)
check("only the newest 2 safety backups are kept", len(set(pre)) == 2, sorted(set(pre)))

# ---- everything changed is in the audit log
aud = adm.get(B + "/admin/audit?action=settings.update&days=0").text
check("setting changes are audited with old and new values", "min_password_length: 10 -&gt; 12" in aud or "min_password_length: 10 -> 12" in aud, "")
print("ALL PASS" if ok_all else "SOME FAILED")
