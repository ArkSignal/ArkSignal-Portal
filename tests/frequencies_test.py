"""The Frequencies page: APRS, calling frequencies, FRS, GMRS, MURS, weather radio, marine, mesh; search and service filter; editors add and remove rows."""
import re, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
p = requests.get(B + "/frequencies")
check("the page opens for everyone and is in the menu", p.status_code == 200 and "/frequencies" in requests.get(B + "/").text)
t = p.text
check("it has APRS 144.3900 for Arkansas, the 2 m and 70 cm calling frequencies", "144.3900" in t and "APRS (United States, Arkansas)" in t and "146.5200" in t and "446.0000" in t)
check("FRS 1 to 22, GMRS, MURS, weather radio, marine and Meshtastic are there", all(x in t for x in ("FRS Channel 22", "GMRS Channel 20", "GMRS Repeater Input 15R", "MURS 1", "NOAA Weather Radio 7", "Marine VHF Channel 16", "Meshtastic LongFast")))
check("search by frequency works", "146.5200" in requests.get(B + "/frequencies", params={"q": "146.52"}).text and "444.0000" not in requests.get(B + "/frequencies", params={"q": "146.52"}).text)
f = requests.get(B + "/frequencies", params={"service": "murs"}).text
check("the service filter shows only that service", "MURS 1" in f and "FRS Channel 1<" not in f and "144.3900" not in f)
check("a visitor has no add form", "/frequencies/add" not in t)
adm = sess("marty", PW)
page = adm.get(B + "/frequencies").text
check("an editor has the add form", "/frequencies/add" in page)
tok = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
r = adm.post(B + "/frequencies/add", data={"csrf_token": tok, "service": "mesh", "name": "ZZ Test Mesh", "mhz": "910.525", "mode": "LoRa", "notes": "test"}, headers={"X-Requested-With": "XMLHttpRequest"})
check("an editor adds a frequency", r.status_code == 200 and "ZZ Test Mesh" in requests.get(B + "/frequencies", params={"q": "ZZ Test"}).text, r.text[:100])
bad = adm.post(B + "/frequencies/add", data={"csrf_token": tok, "service": "mesh", "name": "x", "mhz": "abc"}, headers={"X-Requested-With": "XMLHttpRequest"})
check("a bad frequency is refused", bad.status_code == 400)
fid = re.search(r'/frequencies/(\d+)/delete', adm.get(B + "/frequencies", params={"q": "ZZ Test"}).text).group(1)
d = adm.post(B + f"/frequencies/{fid}/delete", data={"csrf_token": tok}, headers={"X-Requested-With": "XMLHttpRequest"})
check("and removes it", d.status_code == 200 and "ZZ Test Mesh" not in requests.get(B + "/frequencies", params={"q": "ZZ Test"}).text)
print("ALL PASS" if ok_all else "SOME FAILED")
