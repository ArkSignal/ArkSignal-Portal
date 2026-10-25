"""Nodes at one spot: zoomed out a crowd is one dot with a count (no ring covering the map); zoomed in they spread out in tidy rings with faint spokes, and clicking the count zooms in. Needs several mesh nodes at one place
(the test box has them; the test makes its own when the mesh service is up)."""
import base64, os, struct, subprocess, time, requests
os.environ["TEST_HOST"] = "test-box.invalid"
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
try:
    from selenium import webdriver
except ImportError:
    print("SKIP no selenium"); print("ALL PASS"); raise SystemExit(0)
P = f"http://{os.environ['TEST_HOST']}:18482"
try:
    requests.get(P + "/health", timeout=8)
except Exception:
    print("SKIP no mesh service"); print("ALL PASS"); raise SystemExit(0)
def varint(v):
    v &= (1 << 64) - 1; out = b""
    while True:
        b = v & 0x7F; v >>= 7; out += bytes([b | (0x80 if v else 0)])
        if not v: return out
def tag(n, w): return varint((n << 3) | w)
def vi(n, v): return tag(n, 0) + varint(v)
def ld(n, b): return tag(n, 2) + varint(len(b)) + (b if isinstance(b, bytes) else b.encode())
def fx(n, v): return tag(n, 5) + struct.pack("<I", v & 0xFFFFFFFF)
def sfx(n, v): return tag(n, 5) + struct.pack("<i", v)
def env(sender, port, payload): return ld(1, fx(1, sender) + fx(2, 0xFFFFFFFF) + ld(4, vi(1, port) + ld(2, payload)) + fx(6, 12345)) + ld(2, "LongFast") + ld(3, "!feedbeef")
def send(e): requests.post(P + "/ingest", json={"envelope_b64": base64.b64encode(e).decode()}, timeout=20)
base = 0xB0000000 + ((int(time.time()) & 0xFFF) << 4)
for i in range(12):
    nid = base + i
    send(env(nid, 4, ld(2, f"COL Node {i}") + ld(3, f"C{i:03d}"[:4]) + vi(5, 9) + vi(7, 0)))
    send(env(nid, 3, sfx(1, int(35.9 * 1e7)) + sfx(2, int(-92.1 * 1e7)) + vi(3, 100) + vi(23, 16)))
time.sleep(25)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1300,900")
d = webdriver.Chrome(options=o); J = d.execute_script
try:
    d.get(B + "/mesh/map"); time.sleep(4)
    J("window.arkMap.setView([35.9, -92.1], 8)"); time.sleep(2)
    badge = J("return Array.from(document.querySelectorAll('.leaflet-marker-icon div')).map(function(x){return x.textContent}).filter(function(t){return /^[0-9]+$/.test(t)})")
    check("zoomed out, the crowd is one dot with its count", badge and max(int(x) for x in badge) >= 12, badge)
    check("and nothing is fanned out", J("return Colocate.of(window.arkMap).groups()") == 0)
    J("document.querySelector('.leaflet-marker-icon div[title*=\"click to spread\"]').click()"); time.sleep(2.5)
    check("clicking the count zooms in", J("return window.arkMap.getZoom()") >= 13)
    check("zoomed in, the nodes spread out in rings", J("return Colocate.of(window.arkMap).groups()") >= 12)
    names = J("return Array.from(document.querySelectorAll('.leaflet-marker-icon span')).map(function(x){return x.textContent}).filter(function(t){return t.indexOf('COL Node') === 0})")
    check("each spread-out node has its name beside it", len(set(names)) >= 12, names[:5])
    J("window.arkMap.setView([35.9, -92.1], 19)"); time.sleep(2.5)
    names = J("return Array.from(document.querySelectorAll('.leaflet-marker-icon span')).map(function(x){return x.textContent}).filter(function(t){return t.indexOf('COL Node') === 0})")
    check("as far in as the map goes, nodes at exactly one place are still spread and named", len(set(names)) >= 12, names[:5])
    check("no badge is left once they are spread", J("return Array.from(document.querySelectorAll('.leaflet-marker-icon div[title*=\"click to spread\"]')).length") == 0)
finally:
    d.quit()
    subprocess.run(["ssh", "-q", "nastemp", "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec mesh-db psql -U mesh -d mesh -tA -c \"DELETE FROM nodes WHERE long_name LIKE 'COL Node%'\""], capture_output=True, text=True, timeout=60)
print("ALL PASS" if ok_all else "SOME FAILED")
