"""Shopping links for identified hardware: a Where to Buy link on a mesh node page and a Buy link in the node list that searches Amazon for the hardware's name, nothing for a model the site could not name, an
Associates tag added to every Amazon link (marked sponsored, with the disclosure sentence shown), exact product links that win over the search, a tag that is not added to another shop, an Amazon address
that is not Amazon's refused, the switch, and the settings. Needs the mesh service on the test box for the node pages (sh tests/deploy_mesh.sh)."""
import base64, os, re, struct, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
HOST = os.environ.get("TEST_HOST", "test-box.invalid")
P = f"http://{HOST}:18482"
ADMPW = PW
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def mpsql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec mesh-db psql -U mesh -d mesh -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
try:
    requests.get(P + "/health", timeout=8)
except Exception as e:
    print("SKIP shop_test: the mesh service is not running on the test box (sh tests/deploy_mesh.sh)", e); raise SystemExit(0)
def varint(v):
    v &= (1 << 64) - 1
    out = b""
    while True:
        b = v & 0x7F; v >>= 7
        out += bytes([b | (0x80 if v else 0)])
        if not v:
            return out
def tag(num, wt): return varint((num << 3) | wt)
def vi(num, v): return tag(num, 0) + varint(v)
def ld(num, b): return tag(num, 2) + varint(len(b)) + (b if isinstance(b, bytes) else b.encode())
def fx(num, v): return tag(num, 5) + struct.pack("<I", v & 0xFFFFFFFF)
def sfx(num, v): return tag(num, 5) + struct.pack("<i", v)
def envelope(sender, port, payload): return ld(1, fx(1, sender) + fx(2, 0xFFFFFFFF) + ld(4, vi(1, port) + ld(2, payload)) + fx(6, 12345)) + ld(2, "LongFast") + ld(3, "!feedbeef")
def send(env): requests.post(P + "/ingest", json={"envelope_b64": base64.b64encode(env).decode()}, timeout=20)
base = 0xE8000000 + ((int(time.time()) & 0xFFF) << 4)
def node(i, name, hw):
    nid = base + i
    send(envelope(nid, 4, ld(2, name) + ld(3, name[:4]) + vi(5, hw) + vi(7, 0)))
    send(envelope(nid, 3, sfx(1, int(34.9 * 1e7)) + sfx(2, int(-92.1 * 1e7)) + vi(3, 100) + vi(23, 16)))
    return "%08x" % nid
mpsql("DELETE FROM nodes WHERE long_name LIKE 'SHOP %'")
known, unknown = node(1, "SHOP Known", 71), node(2, "SHOP Unknown", 250)
setting("mesh_enabled", "1"); setting("shop_enabled", "1")
psql("DELETE FROM settings WHERE key IN ('shop_amazon_tag','shop_overrides','shop_amazon_domain')")
anon = requests.Session()
def buy(path=None):
    page = anon.get(B + (path or f"/mesh/{known}")).text
    m = re.search(r'<a href="([^"]+)"[^>]*>Where to buy</a>', page)
    return page, m
page, m = buy()
check("a node page has a Where to Buy link that searches Amazon for the hardware's name and the project", m is not None and m.group(1).startswith("https://www.amazon.com/s?") and "Tracker+T1000-E+Meshtastic" in m.group(1), page[page.find("Hardware"):page.find("Hardware") + 200])
check("with no tag the link is an ordinary one: no tag, not marked sponsored, no disclosure", m and "tag=" not in m.group(1) and 'rel="nofollow noopener"' in page[m.start():m.start() + 300] and "Amazon Associate" not in page)
check("a model the site could not name gets no link", "Where to buy" not in anon.get(B + f"/mesh/{unknown}").text)
nl = anon.get(B + "/mesh/nodes?q=SHOP").text
check("the node list has a Buy link next to the hardware's name, and none for the unlisted one", nl.count(">Buy</a>") >= 1 and "Tracker+T1000-E" in nl and "Unlisted+hardware" not in nl)
setting("shop_amazon_tag", "arksignal-20")
page, m = buy()
check("with an Associates tag every Amazon link carries it and is marked sponsored", m is not None and "tag=arksignal-20" in m.group(1) and 'rel="sponsored nofollow noopener"' in page[m.start():m.start() + 300])
check("and the disclosure sentence is on the pages", "As an Amazon Associate we earn from qualifying purchases." in page and "As an Amazon Associate" in anon.get(B + "/mesh/nodes?q=SHOP").text)
setting("shop_overrides", "Tracker T1000-E = https://www.amazon.com/dp/B0TEST1234?th=1; Other Thing = https://example.org/shop; bad = http://insecure.example/")
page, m = buy()
check("an exact product link wins over the search, and an Amazon one gets the tag too", m is not None and m.group(1).startswith("https://www.amazon.com/dp/B0TEST1234") and "tag=arksignal-20" in m.group(1) and "th=1" in m.group(1), m and m.group(1))
setting("shop_overrides", "Tracker T1000-E = https://example.org/shop/t1000")
page, m = buy()
check("another shop's address is used as it is, without the Amazon tag, and is not marked sponsored", m is not None and m.group(1) == "https://example.org/shop/t1000" and 'rel="nofollow noopener"' in page[m.start():m.start() + 300], m and m.group(1))
setting("shop_overrides", "Tracker T1000-E = http://insecure.example/t")
page, m = buy()
check("an address that is not https is ignored (back to the search)", m is not None and m.group(1).startswith("https://www.amazon.com/s?"))
setting("shop_overrides", "")
setting("shop_amazon_domain", "evil.example.com")
page, m = buy()
check("an address that is not one of Amazon's is never used for the search", m is not None and m.group(1).startswith("https://www.amazon.com/s?"), m and m.group(1))
setting("shop_amazon_domain", "www.amazon.co.uk")
page, m = buy()
check("another Amazon country can be chosen", m is not None and m.group(1).startswith("https://www.amazon.co.uk/s?"))
setting("shop_amazon_tag", "bad tag!")
page, m = buy()
check("a tag that is not letters, digits and dashes is not used, and no disclosure is shown", m is not None and "tag=" not in m.group(1) and "Amazon Associate" not in page)
setting("shop_enabled", "0")
check("switched off: no shopping links anywhere", "Where to buy" not in anon.get(B + f"/mesh/{known}").text and ">Buy</a>" not in anon.get(B + "/mesh/nodes?q=SHOP").text)
setting("shop_enabled", "1")
st = sess("marty", ADMPW).get(B + "/admin/settings/site").text
check("the choices are settings under Shopping Links", all(x in st for x in ("Links to Buy Hardware", "Amazon Associates Tag", "Associates Disclosure", "Exact Product Links", "Amazon Address")))
psql("DELETE FROM settings WHERE key IN ('shop_amazon_tag','shop_overrides','shop_amazon_domain')")
mpsql("DELETE FROM nodes WHERE long_name LIKE 'SHOP %'")
print("ALL PASS" if ok_all else "SOME FAILED")
