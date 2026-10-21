import re, requests, subprocess, time
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True


def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra)
    ok_all &= bool(cond)


def post(s, path, page, **data):
    data["csrf_token"] = tok(s, page)
    return s.post(B + path, data=data, allow_redirects=False)


def flashes(r, s=None):
    if r.status_code == 200:
        text = r.text
    elif s is not None and "Location" in r.headers:
        loc = r.headers["Location"]; text = s.get(loc if loc.startswith("http") else B + loc).text
    else:
        text = ""
    return " ".join(re.findall(r'class="flash[^>]*>([^<]*)', text))


def register(user, email, pw="claimpassword1"):
    s = requests.Session()
    t = re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + "/my/register").text).group(1)
    s.post(B + "/my/register", data={"csrf_token": t, "username": user, "email": email, "password": pw, "confirm": pw})
    return sess(user, pw)


A = "testaaaa-0000-4000-8000-000000000001"
A2 = "testaaaa-0000-4000-8000-000000000002"
OLD = "testbbbb-0000-4000-8000-000000000003"
OTHER = "testcccc-0000-4000-8000-000000000004"
c1 = register("claimer1", "claimer1@example.com")
c2 = register("claimer2", "claimer2@example.com")
ip = re.search(r"\(([0-9a-f.:]+)\)\.", c1.get(B + "/my/claim").text).group(1)
subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/mkfeeders.py {ip}"], check=True, capture_output=True)

h = c1.get(B + "/my/claim").text
check("same-IP candidates listed", A in h and A2 in h and OLD not in h and OTHER not in h)
r = post(c1, "/my/claim", "/my/claim", uuid="not-a-uuid")
check("bogus UUID rejected", "No feeder with that UUID" in r.text, flashes(r))
r = post(c1, "/my/claim", "/my/claim", uuid=OLD)
check("feeder unseen for 10 days cannot be claimed", "last 7 days" in r.text, flashes(r))
r = post(c1, "/my/claim", "/my/claim", uuid=A)
check("claim same-IP feeder", r.status_code == 302 and A in r.headers["Location"], r.status_code)
r = post(c2, "/my/claim", "/my/claim", uuid=A)
check("second user cannot claim an owned feeder", "already been claimed" in r.text)
r = post(c2, "/my/claim", "/my/claim", uuid=OTHER.upper())
check("pasted UUID seen within 7 days is claimable (case-insensitive)", r.status_code == 302, r.text[:100])
d = c1.get(B + "/my/").text
check("owner sees feeder on dashboard", A in d and "No Station ID yet" in d)
check("other user cannot open my feeder page", c2.get(B + f"/my/feeders/{A}").status_code == 404 and
      c2.post(B + f"/my/feeders/{A}/save", data={"csrf_token": tok(c2, "/my/")}).status_code == 404)


def save(s, uuid, **kw):
    d = dict(name="Test Station", description="d", notes="", website="", lat="", lon="", antenna_height_ft="",
             height_ref="", ground_elev_ft="", privacy="general")
    d.update(kw)
    d["csrf_token"] = tok(s, f"/my/feeders/{uuid}")
    r = s.post(B + f"/my/feeders/{uuid}/save", data=d, headers={"X-Requested-With": "XMLHttpRequest"})
    j = r.json()
    return j["ok"], j["message"]


for name, kw in [("lat without lon", dict(lat="34.3")), ("lat out of range", dict(lat="95", lon="-92")),
                 ("height without AGL/ASL", dict(antenna_height_ft="25")), ("bad privacy", dict(privacy="public")),
                 ("bad website", dict(website="javascript:alert(1)")), ("non-numeric elevation", dict(ground_elev_ft="high"))]:
    ok, m = save(c1, A, **kw)
    check("reject: " + name, not ok, m)
ok, m = save(c1, A, name="Sheridan Test", website="https://example.com", lat="34.315231", lon="-92.410357",
             antenna_height_ft="25", height_ref="agl", ground_elev_ft="312", privacy="general",
             setup_receiver="RTL-SDR V4", setup_antenna="Ground plane")
check("save full details with location", ok, m)
page = c1.get(B + f"/my/feeders/{A}").text
check("details persisted + AGL/ASL note", "Sheridan Test" in page and "RTL-SDR V4" in page and "337 ft above sea level" in page,
      re.findall(r'id="height-note">([^<]*)', page))
check("map container and leaflet present", 'id="pickmap"' in page and "leaflet" in page)
check("owner sees status, activity chart and connection history", "Connection History" in page and "<svg" in page or "No data for this period yet." in page, "")
check("owner history shows the claim event", "claimed" in page, "")
for _per in ("24h", "7d", "30d", "1y"):
    check(f"owner history period {_per} renders", c1.get(B + f"/my/feeders/{A}?period={_per}").status_code == 200)
e = c1.get(B + "/my/elevation?lat=34.315231&lon=-92.410357").json()
check("elevation lookup (Open-Meteo)", e.get("ok") and 280 < e["feet"] < 340, e)
check("elevation rejects bad input", c1.get(B + "/my/elevation?lat=abc&lon=1").status_code == 400)

r = post(c1, f"/my/feeders/{A}/station-id", f"/my/feeders/{A}", kind="callsign")
check("callsign ID refused without a callsign", "callsign" in flashes(r, c1).lower(), flashes(r, c1))
r = c1.post(B + "/my/profile", data={"csrf_token": tok(c1, "/my/profile"), "display_name": "Claimer", "callsign": "BAD"})
check("invalid callsign rejected", "valid amateur radio callsign" in r.text)
r = c1.post(B + "/my/profile", data={"csrf_token": tok(c1, "/my/profile"), "display_name": "Claimer", "callsign": "kd5vcf"}, allow_redirects=False)
check("valid callsign saved (uppercased)", r.status_code == 302 and "KD5VCF" in c1.get(B + "/my/profile").text)
page = c1.get(B + f"/my/feeders/{A}").text
check("station ID options show callsign and airport candidates",
      "Use My Callsign: KD5VCF" in page and re.search(r"Use Nearest Airport: [A-Z0-9]{3,4}-01", page) is not None, re.findall(r"Use [^<]*", page))
r = post(c1, f"/my/feeders/{A}/station-id", f"/my/feeders/{A}", kind="callsign")
check("choose callsign ID", "Station ID set to KD5VCF" in flashes(r, c1), flashes(r, c1))
post(c1, "/my/claim", "/my/claim", uuid=A2)
page = c1.get(B + f"/my/feeders/{A2}").text
check("second station callsign candidate is KD5VCF-2", "Use My Callsign: KD5VCF-2" in page)
save(c1, A2, lat="34.315231", lon="-92.410357", privacy="general")
page = c1.get(B + f"/my/feeders/{A2}").text
m = re.search(r"Use Nearest Airport: ([A-Z0-9]+-\d\d)", page)
check("airport candidate for Sheridan coordinates", m and m.group(1).startswith("K9M8-"), m and m.group(1))
r = post(c1, f"/my/feeders/{A2}/station-id", f"/my/feeders/{A2}", kind="airport")
check("choose airport ID", "Station ID set to K9M8-01" in flashes(r, c1), flashes(r, c1))
d = c1.get(B + "/my/").text
check("dashboard shows station IDs", "KD5VCF" in d and "K9M8-01" in d)

adm = sess("marty", PW)


def manage(uuid, **kw):
    kw["csrf_token"] = tok(adm, f"/admin/feeders/{uuid}")
    r = adm.post(B + f"/admin/feeders/{uuid}/manage", data=kw, allow_redirects=True)
    return " ".join(re.findall(r'class="flash[^>]*>([^<]*)', r.text))


check("admin sets a custom station ID", "set to ARK-RAINBOW" in manage(OTHER, action="station_id", station_id="ark-rainbow"))
check("admin duplicate station ID rejected", "already in use" in manage(OLD, action="station_id", station_id="ARK-RAINBOW"))
check("admin invalid station ID rejected", "3 to 16 characters" in manage(OLD, action="station_id", station_id="x"))
check("admin ban", "banned" in manage(A2, action="ban").lower())
check("banned shows in admin list", "banned" in adm.get(B + "/admin/feeders").text)
check("admin unban", "unbanned" in manage(A2, action="unban").lower())
uid = int(re.search(r'<option value="(\d+)"[^>]*>claimer2</option>', adm.get(B + f"/admin/feeders/{OLD}").text).group(1))
check("admin assigns owner", "Owner set to claimer2" in manage(OLD, action="owner", owner_id=str(uid)))
check("admin removes owner", "unclaimed" in manage(OLD, action="owner", owner_id=""))
check("non-admin cannot use manage endpoint",
      c1.post(B + f"/admin/feeders/{A}/manage", data={"csrf_token": tok(c1, "/my/"), "action": "ban"}).status_code == 404)
r = post(c1, f"/my/feeders/{A}/release", f"/my/feeders/{A}")
check("release feeder", r.status_code == 302)
f = adm.get(B + f"/admin/feeders/{A}").text
check("released feeder is unclaimed with no station ID", "Not claimed" in f)
print("ALL PASS" if ok_all else "SOME FAILED")
