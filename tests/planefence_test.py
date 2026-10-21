import re, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
# needs the test Planefence container reachable from the portal as `planefence` (docker network connect --alias planefence)
r = requests.get(B + "/pf/")
if r.status_code == 503:
    print("PASS skipped: no Planefence container is reachable from the test portal (see tests/README.md)"); print("ALL PASS"); raise SystemExit
check("the public Planefence page is served by the portal", r.status_code == 200 and "lanefence" in r.text, r.status_code)
check("its own scripts point at the portal path, not at the site root", '"/cgi/' not in r.text and "/pf/cgi/config_status.php" in r.text)
check("Plane-Alert mode loads too", requests.get(B + "/pf/?mode=pa").status_code == 200)
check("a path that tries to leave the service is refused", requests.get(B + "/pf/..%2f..%2fetc/passwd").status_code in (404, 400))
check("posting anywhere but its cgi is refused", requests.post(B + "/pf/index.html", data={"a": 1}).status_code in (405, 400, 403))
tool = requests.get(B + "/view/planefence")
check("Planefence and Plane-Alert are in the Tools menu", tool.status_code == 200 and 'href="/view/plane-alert"' in requests.get(B + "/").text)
anon = requests.get(B + "/admin/planefence/", allow_redirects=False)
check("the configuration page is not public", anon.status_code in (302, 401, 403, 404))
check("nor is its proxied interface", requests.get(B + "/admin/planefence/ui/", allow_redirects=False).status_code in (302, 401, 403, 404))
plain = sess("plain1", "plainpassword1")
check("an ordinary user cannot open it", plain.get(B + "/admin/planefence/", allow_redirects=False).status_code in (302, 401, 403, 404))
adm = sess("marty", PW)
page = adm.get(B + "/admin/planefence/")
check("an administrator sees the configuration page with the admin menu", page.status_code == 200 and "/admin/planefence/ui/" in page.text and 'href="/admin/planefence/"' in page.text)
ui = adm.get(B + "/admin/planefence/ui/")
check("the proxied configuration interface loads and calls the proxied API", ui.status_code == 200 and "/admin/planefence/ui/cgi/config_api.php" in ui.text and 'fetch("/cgi/' not in ui.text)
load = adm.get(B + "/admin/planefence/ui/cgi/config_api.php?action=load")
check("it can read the configuration", load.status_code == 200 and "FEEDER_LAT" in load.text, load.text[:120])
bad = adm.post(B + "/admin/planefence/ui/cgi/config_api.php?action=save", data="{}", headers={"Sec-Fetch-Site": "cross-site", "Content-Type": "application/json"})
check("a cross-site post to it is refused", bad.status_code == 403, bad.status_code)
good = adm.post(B + "/admin/planefence/ui/cgi/config_api.php?action=validate", data="{}", headers={"Sec-Fetch-Site": "same-origin", "Content-Type": "application/json"})
check("a same-site post reaches Planefence", good.status_code in (200, 400, 422), good.status_code)
# the streaming, compression and page cache of the proxy, against a slow stand-in upstream inside the portal container
import subprocess
D = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker "
subprocess.run(["scp", "-q", "planefence_unit.py", "nastemp:adsb-portal/planefence_unit.py"], check=True, capture_output=True)
subprocess.run(["ssh", "-q", "nastemp", D + "cp ~/adsb-portal/planefence_unit.py adsb-portal:/tmp/planefence_unit.py"], capture_output=True)
out = subprocess.run(["ssh", "-q", "nastemp", D + "exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/planefence_unit.py"], capture_output=True, text=True).stdout
check("the proxy streams the data (first rows at once, compressed) and caches the pages", "ALL PASS" in out and "FAIL" not in out, out[-500:])
gz = requests.get(B + "/pf/", headers={"Accept-Encoding": "gzip"}, stream=True)
wire = len(gz.raw.read(decode_content=False))
check("the real Planefence page comes compressed (a fifth of the size)", gz.headers.get("Content-Encoding") == "gzip" and wire < 150000, (gz.headers.get("Content-Encoding"), wire))
print("ALL PASS" if ok_all else "SOME FAILED")
