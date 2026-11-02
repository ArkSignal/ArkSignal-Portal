"""The Weather Map's labels do not jump around: with every source on, the markers are drawn once the first answer is in and once more when the airport weather has all arrived, and then they stay put (the same
stations always merge into the same labels). Uses the persistent test portal (:18431). Needs selenium and Chrome."""
import os, sys, time
HOST = os.environ.get("TEST_HOST", "test-box.invalid"); P = os.environ.get("COUNTY_BASE", f"http://{HOST}:18431")
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
try:
    from selenium import webdriver
except ImportError:
    print("SKIP: selenium not installed"); sys.exit(0)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900"); o.set_capability("goog:loggingPrefs", {"browser": "ALL"})
d = webdriver.Chrome(options=o); J = d.execute_script
try:
    d.get(P + "/weather/map"); J("localStorage.setItem('lv.wm.sources', JSON.stringify({members:true,aprs:true,airport:true}))"); d.get(P + "/weather/map")
    prev, changes = None, []
    for i in range(22):
        time.sleep(2.5)
        snap = J("return Array.from(document.querySelectorAll('#lvmap .leaflet-marker-icon')).map(function(e){var r=e.getBoundingClientRect(); return [Math.round(r.left/4)*4, Math.round(r.top/4)*4]})")
        key = sorted(map(tuple, snap)); changes.append(0 if prev is None else len(set(key) ^ set(prev))); prev = key
    check("the labels are drawn at most twice while loading", sum(1 for c in changes[:12] if c) <= 2, changes)
    check("and then they do not move at all", sum(changes[12:]) == 0, changes)
    check("no script errors", not [l["message"][:160] for l in d.get_log("browser") if l["level"] == "SEVERE" and "favicon" not in l["message"]])
finally:
    d.quit()
    try: pass
    except Exception: pass
# the tiles' expiry no longer takes airports off the map (the in-container check)
import subprocess
out = subprocess.run(["ssh", "-q", "nastemp", "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker cp ~/adsb-portal/tests/wxstale_unit.py adsb-portal:/tmp/wxstale_unit.py; echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/wxstale_unit.py"], capture_output=True, text=True, timeout=180).stdout
check("airport weather tiles keep their last answer while a fresh one is fetched (wxstale_unit.py inside the portal container)", "ALL PASS" in out and "FAIL" not in out, out[-500:])
print("ALL PASS" if ok_all else "SOME FAILED")
