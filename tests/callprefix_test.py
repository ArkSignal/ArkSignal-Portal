"""Call Sign Lookup: the start of a call sign (KD5) lists every call sign on the site that begins with it; a whole call sign still shows its license and everything the site knows."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def psql(sql): return subprocess.run(["ssh", "-q", "nastemp", "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"" + sql + "\""], capture_output=True, text=True).stdout.strip()
psql("DELETE FROM repeaters WHERE callsign LIKE 'ZZ9%'")
for c, f in (("ZZ9ABC", 146940000), ("ZZ9ABD", 147120000), ("ZZ9XYZ", 444100000)):
    psql(f"INSERT INTO repeaters (callsign, freq_hz, approved, city, state) VALUES ('{c}', {f}, TRUE, 'Testville', 'AR')")
try:
    h = requests.get(B + "/callsign?call=ZZ9").text
    check("the start of a call sign lists every call sign on the site that begins with it", all(c in h for c in ("ZZ9ABC", "ZZ9ABD", "ZZ9XYZ")) and "Start With ZZ9" in h, re.findall(r"Start With[^<]*", h))
    check("each is a link to its own lookup and says what it is on the site", 'href="/callsign?call=ZZ9ABC"' in h and ">Repeater<" in h)
    h2 = requests.get(B + "/callsign?call=ZZ9AB").text
    check("a longer start narrows the list", "ZZ9ABC" in h2 and "ZZ9ABD" in h2 and "ZZ9XYZ" not in h2.split("Start With")[1].split("</table>")[0])
    h3 = requests.get(B + "/callsign?call=ZZ9ABC").text
    check("a whole call sign still shows what the site knows about it", "Everything We Know About ZZ9ABC" in h3 and "Repeater" in h3)
    check("one letter is not enough to list anything", "Start With" not in requests.get(B + "/callsign?call=Z").text)
finally:
    psql("DELETE FROM repeaters WHERE callsign LIKE 'ZZ9%'")
print("ALL PASS" if ok_all else "SOME FAILED")
