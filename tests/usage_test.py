"""Admin > Usage: every outside request the portal makes is counted by host, the services report their database sizes, and the page lists requests, stored data and live feeds.
Needs the portal and (for the sizes) the service containers on the test box."""
import re, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def psql(sql):
    import subprocess
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
adm = sess("marty", PW)
adm.get(B + "/admin/status")   # the status page asks a number of services and outside sources
time.sleep(1)
pg = adm.get(B + "/admin/usage")
check("the Usage page opens for an administrator with its three sections", pg.status_code == 200 and all(x in pg.text for x in ("Requests to Outside Services", "Stored Data", "Live Feeds")))
n = psql("select coalesce(sum(calls),0) from api_usage")
check("requests were counted by host in the database", n.isdigit() and int(n) > 0, n)
check("the table lists a host with its request counts", re.search(r'<td class="mono">[a-z0-9.-]+', pg.text) is not None and "usage-out" in pg.text)
check("the portal's own database size and its tables by service are shown", "The portal's own database:" in pg.text and re.search(r"about [\d,]+ rows", pg.text) is not None)
check("the stored data table lists every service container", all(x in pg.text for x in ("APRS", "ACARS", "Mesh nodes", "Prediction and archive")))
check("the Usage page has date and time pickers and an All Time button", all(x in pg.text for x in ('id="u-from"', 'id="u-to"', 'id="u-alltime"', 'datetime-local')))
check("the table of outside requests can be filtered by text, kind, failures and use", all(x in pg.text for x in ('data-table="usage-out"', 'id="u-kind"', 'id="u-fail"', 'id="u-used"')))
check("the page's link to Settings > Service Status leads somewhere", adm.get(B + "/admin/settings/site").status_code == 200 and "/admin/settings/general" not in pg.text)
pr = adm.get(B + "/admin/usage?from=2000-01-01T00:00:00Z&to=2000-01-02T00:00:00Z")
check("a range with nothing in it shows zero requests in range but still lists the hosts", pr.status_code == 200 and 'data-calls="0"' in pr.text)
pa = adm.get(B + "/admin/usage?all=1")
check("All Time opens", pa.status_code == 200 and 'data-calls="' in pa.text)
check("a nonsense range does not break the page", adm.get(B + "/admin/usage?from=banana&to=2999-99-99").status_code == 200)
check("the hourly and feed history tables exist", all(psql(f"select count(*) from {t}").isdigit() for t in ("api_usage_hourly", "feed_usage_hourly", "svc_usage", "usage_state")))
hn = psql("select coalesce(sum(calls),0) from api_usage_hourly")
check("requests are also kept by the hour", hn.isdigit() and int(hn) > 0, hn)
for _ in range(16):   # the feed sampler runs once a minute
    fn = psql("select count(*) from usage_state where k like 'feed|%'")
    if fn.isdigit() and int(fn) > 0: break
    time.sleep(10)
check("the feed counters are sampled into the all-time tables", fn.isdigit() and int(fn) > 0, fn)
check("the left menu has Usage", 'href="/admin/usage"' in pg.text)
check("a member cannot open it", sess("plain1", "plainpassword1").get(B + "/admin/usage", allow_redirects=False).status_code in (302, 401, 403, 404))
check("an anonymous visitor cannot", requests.get(B + "/admin/usage", allow_redirects=False).status_code in (302, 401, 403, 404))
for name, port in (("mesh", 18482), ("predict", 18470)):
    try:
        d = requests.get(f"http://{__import__("os").environ.get("TEST_HOST", "test-box.invalid")}:{port}/stats", timeout=5).json()
        check(f"the {name} service reports its database size", isinstance(d.get("db_bytes"), int) and d["db_bytes"] > 0, d.get("db_bytes"))
    except Exception as e:
        print("SKIP the", name, "service is not running on the test box:", type(e).__name__)
print("ALL PASS" if ok_all else "SOME FAILED")
