import re, requests, subprocess
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True


def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra)
    ok_all &= bool(cond)


anon = requests.Session()
h = anon.get(B + "/feed/").text
check("feed page loads with the feed line", "adsb,feed.arksignal.allisononline.org,30004,beast_reduce_plus_out;" in h)
check("MLAT line hidden while MLAT is off", "mlat,feed.arksignal.allisononline.org" not in h)
for url in ("github.com/sdr-enthusiasts/docker-adsb-ultrafeeder", "rtl-sdr.com", "airspy.com", "flightaware.com/adsb/prostick", "raspberrypi.com/software",
            "docs.docker.com/engine/install", "heywhatsthat.com", "sdr-enthusiasts.gitbook.io"):
    check("page links to " + url, url in h)
check("compose example present with UUID placeholder", "READSB_DEVICE_TYPE=rtlsdr" in h and "UUID=YOUR_UUID" in h)
h2 = anon.get(B + "/feed/check").text
check("no check results before pressing Check Now", "No feeder has connected from" not in h2)
ip = re.search(r'public IP address is <span class="mono">([^<]+)</span>', h2).group(1)
check("the old /feed/?check=1 address still works", anon.get(B + "/feed/?check=1", allow_redirects=False).headers.get("Location", "").endswith("/feed/check?check=1"))
r = anon.get(B + "/feed/?check=1").text
check("check with nothing connected explains clearly", "No feeder has connected from" in r)
subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/mkfeeders.py {ip}"], check=True, capture_output=True)
A = "testaaaa-0000-4000-8000-000000000001"
r = anon.get(B + "/feed/?check=1").text
check("check finds unclaimed feeders on this IP and shows UUID + claim link", A in r and "Claim this feeder" in r and r.count("online") >= 2, re.findall(r"testaaaa[^<]*", r))
# claim and name it: UUID must disappear, station link appears
c1 = sess("claimer1", "claimpassword1")
t = tok(c1, "/my/claim")
c1.post(B + "/my/claim", data={"csrf_token": t, "uuid": A})
adm = sess("marty", PW)
t = tok(adm, f"/admin/feeders/{A}")
adm.post(B + f"/admin/feeders/{A}/manage", data={"csrf_token": t, "action": "station_id", "station_id": "FEED-TEST"})
r = anon.get(B + "/feed/?check=1").text
check("claimed + named feeder shows station link and hides its UUID", "/feeders/FEED-TEST" in r and A not in r, re.findall(r"testaaaa[^<]*", r))
t = tok(adm, f"/admin/feeders/{A}")
adm.post(B + f"/admin/feeders/{A}/manage", data={"csrf_token": t, "action": "ban"})
check("banned feeder not shown", "FEED-TEST" not in anon.get(B + "/feed/?check=1").text)
# settings: change host, enable mlat
base = dict(site_name="ArkSignal", registration_open="1", default_privacy="general", feed_mode="open", map_lat="34.80", map_lon="-92.30",
            map_zoom="7", timezone="America/Chicago", max_failed_attempts="5", lockout_minutes="15", history_retention_days="730",
            sample_retention_days="30", feed_host="feed.arksignal.allisononline.org", feed_port="30004", mlat_port="31090", max_rules_per_user="25")


def save(**kw):
    d = dict(base)
    d.update(kw)
    for k in [k for k, v in d.items() if v is None]:
        d.pop(k)
    return act(adm, "/admin/settings/save", "/admin/settings", **d)


ok, m, _ = save(feed_host="bad host!"); check("bad feed hostname rejected", not ok, m)
ok, m, _ = save(feed_port="99999"); check("bad feed port rejected", not ok, m)
ok, m, _ = save(feed_host="feed.arksignal.allisononline.org", feed_port="31004", mlat_enabled="1", mlat_port="31090", max_rules_per_user="25")
check("save custom feed host and enable MLAT", ok, m)
h = anon.get(B + "/feed/").text
check("page uses the configured host/port and shows MLAT line", "adsb,feed.arksignal.allisononline.org,31004,beast_reduce_plus_out;" in h and "mlat,feed.arksignal.allisononline.org,31090" in h)
ok, m, _ = save(); check("restore defaults", ok, m)
check("defaults restored on page", "mlat,feed.arksignal.allisononline.org" not in anon.get(B + "/feed/").text)
print("ALL PASS" if ok_all else "SOME FAILED")
