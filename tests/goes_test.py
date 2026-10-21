"""GOES imagery: the page, the stored pictures fetched from NOAA's public image server (needs the internet from the test box), served from our own copy, limited to the areas and pictures the settings offer."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
def setting(k, v):
    psql(f"INSERT INTO settings (key, value) VALUES ('{k}', '{v}') ON CONFLICT (key) DO UPDATE SET value = '{v}'")
for k in ("goes_enabled", "goes_sectors", "goes_products", "goes_refresh_minutes", "goes_default_sector"):
    psql(f"DELETE FROM settings WHERE key = '{k}'")
psql("DELETE FROM goes_images")
anon = requests.Session()
adm = sess("marty", PW)

pg = anon.get(B + "/weather/goes", timeout=90)
t = pg.text
check("the page is public and offers areas and pictures, starting with the Arkansas area and GeoColor", pg.status_code == 200 and "Southern Mississippi Valley" in t and "Southern Plains" in t and "GeoColor" in t and "Clean Infrared" in t and "Satellite Viewer" in t)
check("the Weather menu lists it (and the old Radio address still leads there)", 'href="/weather/goes"' in anon.get(B + "/").text and anon.get(B + "/radio/goes", allow_redirects=False).status_code == 301)
im = anon.get(B + "/goes/smv/GEOCOLOR.jpg", timeout=90)
live = im.status_code == 200
if not live:
    print("SKIP the live checks: NOAA's picture server could not be reached from the test box", im.status_code)
else:
    check("the picture is a real JPEG served from our own copy with NOAA's picture time", im.headers["Content-Type"] == "image/jpeg" and im.content[:3] == b"\xff\xd8\xff" and len(im.content) > 50000 and "Last-Modified" in im.headers)
    check("the page credits the source and the viewer names the satellite for the choice", "NOAA/NESDIS" in t and "GOES-19 (East)" in t)
    n1 = psql("select count(*) from goes_images")
    ft1 = psql("select fetched_at from goes_images where sector = 'smv' and product = 'GEOCOLOR'")
    im2 = anon.get(B + "/goes/smv/GEOCOLOR.jpg", timeout=60)
    check("asking again inside the refresh time is served from the stored copy (NOAA is not asked)", im2.content == im.content and psql("select fetched_at from goes_images where sector = 'smv' and product = 'GEOCOLOR'") == ft1)
    psql("UPDATE goes_images SET fetched_at = now() - interval '1 hour'")
    im3 = anon.get(B + "/goes/smv/GEOCOLOR.jpg", timeout=90)
    check("an old copy is checked with NOAA again (a conditional request) and the time of the check moves on", im3.status_code == 200 and psql("select fetched_at from goes_images where sector = 'smv' and product = 'GEOCOLOR'") != ft1)
    other = anon.get(B + "/goes/conus/13.jpg", timeout=90)
    check("another area and picture works too", other.status_code == 200 and other.content[:3] == b"\xff\xd8\xff")
    check("the page for another choice shows it", "Clean Infrared" in anon.get(B + "/weather/goes?sector=conus&product=13", timeout=90).text)

check("an area or picture that is not in the catalog is a 404", anon.get(B + "/goes/nowhere/GEOCOLOR.jpg").status_code == 404 and anon.get(B + "/goes/smv/bogus.jpg").status_code == 404)
setting("goes_products", "GEOCOLOR")
check("a picture the settings do not offer is a 404 and leaves the page", anon.get(B + "/goes/smv/13.jpg").status_code == 404 and "Clean Infrared" not in anon.get(B + "/weather/goes", timeout=60).text)
setting("goes_sectors", "sp")
check("an area the settings do not offer is a 404 and the page falls back to one that is offered", anon.get(B + "/goes/smv/GEOCOLOR.jpg").status_code == 404 and "Southern Plains" in anon.get(B + "/weather/goes?sector=smv", timeout=60).text)
st = adm.get(B + "/admin/settings").text
check("its settings are in Admin > Settings", all(x in st for x in ("GOES Imagery Page", "GOES: Areas Offered", "GOES: Pictures Offered", "GOES: Area Shown First", "GOES: Ask NOAA for a New Picture Every")))
setting("goes_enabled", "0")
check("switched off, the page and pictures are gone", anon.get(B + "/weather/goes").status_code == 404 and anon.get(B + "/goes/sp/GEOCOLOR.jpg").status_code == 404)
check("NOAA is credited in the terms file", "cdn.star.nesdis.noaa.gov" in open("../DATA_SOURCES_TERMS.md", encoding="utf-8").read())
for k in ("goes_enabled", "goes_sectors", "goes_products"):
    psql(f"DELETE FROM settings WHERE key = '{k}'")
print("ALL PASS" if ok_all else "SOME FAILED")
