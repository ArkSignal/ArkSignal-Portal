"""The site-wide search (the box on the home page, /search): it lists every hit, grouped by kind, instead of jumping to one; a single hit still goes straight to it, an aircraft address goes to the aircraft."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def psql(sql): return subprocess.run(["ssh", "-q", "nastemp", "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"" + sql + "\""], capture_output=True, text=True).stdout.strip()
psql("DELETE FROM repeaters WHERE callsign LIKE 'ZZ8%'")
for c, f in (("ZZ8AAA", 146940000), ("ZZ8AAB", 147120000)):
    psql(f"INSERT INTO repeaters (callsign, freq_hz, approved, city, state) VALUES ('{c}', {f}, TRUE, 'Searchville', 'AR')")
try:
    r = requests.get(B + "/search?q=ZZ8A", allow_redirects=False)
    check("two matches give a list, not a jump", r.status_code == 200 and "ZZ8AAA" in r.text and "ZZ8AAB" in r.text and "2 matches" in r.text, (r.status_code, r.headers.get("Location")))
    check("the hits are grouped by kind with a count (the call signs here)", "Call Signs" in r.text and re.search(r"Call Signs <span[^>]*>\(2\)", r.text) is not None, re.findall(r"<h2[^>]*>[^<]*", r.text))
    check("it offers the flight search and the aircraft map for the same text", "/flight/?q=ZZ8A" in r.text and "/live?q=ZZ8A" in r.text)
    one = requests.get(B + "/search?q=ZZ8AAA", allow_redirects=False)
    check("a single hit goes straight to its page", one.status_code in (301, 302) and one.headers["Location"].endswith("/callsign?call=ZZ8AAA"), (one.status_code, one.headers.get("Location")))
    none = requests.get(B + "/search?q=qqqzzz9", allow_redirects=False)
    check("no match says so and still offers the flight search", none.status_code == 200 and "Nothing on the site matches" in none.text)
    hexr = requests.get(B + "/search?q=ae1234", allow_redirects=False)
    check("an aircraft address goes to the aircraft", hexr.status_code in (301, 302) and hexr.headers["Location"].endswith("/flight/ae1234"), hexr.headers.get("Location"))
    check("an empty search goes home", requests.get(B + "/search?q=", allow_redirects=False).headers.get("Location", "").endswith("/"))
    check("the map's suggestion list still answers", requests.get(B + "/api/search.json?q=ZZ8A").status_code == 200)
finally:
    psql("DELETE FROM repeaters WHERE callsign LIKE 'ZZ8%'")
print("ALL PASS" if ok_all else "SOME FAILED")
