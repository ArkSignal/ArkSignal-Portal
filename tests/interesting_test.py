import re, requests, subprocess
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
adm = sess("marty", PW)
H = {"X-Requested-With": "XMLHttpRequest"}
def post(path, **data):
    j = adm.post(B + path, data=dict(data, csrf_token=tok(adm, "/admin/interesting")), headers=H).json(); return j["ok"], j["message"]
psql("delete from interesting_aircraft")
check("the admin page needs sign-in", requests.get(B + "/admin/interesting", allow_redirects=False).status_code in (302, 401, 403, 404))
t = adm.get(B + "/admin/interesting").text
check("it loads for an administrator with its three ways in and a menu entry", all(x in t for x in ("Add an Aircraft", "Import a CSV File", "Plane-Alert Database", 'href="/admin/interesting"')))
check("an ordinary user cannot use it", all(sess("plain1", "plainpassword1").post(B + p, data={}, allow_redirects=False).status_code in (302, 400, 401, 403, 404) for p in ("/admin/interesting/add", "/admin/interesting/import", "/admin/interesting/planealert")))
ok, m = post("/admin/interesting/add", hex="zzz", category="x")
check("a bad hex code is refused", not ok and "six letters or digits" in m, m)
ok, m = post("/admin/interesting/add", hex="AE0123", reg="n1", type="c17", category="Test Category", note="added by the test")
api = requests.get(B + "/api/interesting.json").json()
check("a hand entry is added and reaches the map feed", ok and api["hex"].get("ae0123") == ["Test Category", "added by the test"], (m, api["hex"].get("ae0123")))
csv = "$ICAO,$Registration,$Operator,$Type,$ICAO Type,Category,$#Link\nae0123,OTHER,Somebody,Thing,C17,Csv Category,\nae0456,N456,Owner Co,Thing,B350,Csv Category,https://example.com\nnothex,X,,,,,\n"
ok, m = post("/admin/interesting/import", pasted=csv, mode="preview")
check("a CSV preview says what it read and changes nothing", ok and "Preview" in m and "2 aircraft" in m and "1 lines skipped" in m and psql("select count(*) from interesting_aircraft") == "1", m)
ok, m = post("/admin/interesting/import", pasted=csv, mode="import")
check("a CSV import adds new aircraft and keeps what was added by hand", ok and "1 added" in m and psql("select category from interesting_aircraft where hex='ae0123'") == "Test Category" and psql("select source from interesting_aircraft where hex='ae0456'") == "csv", m)
ok, m = post("/admin/interesting/import", pasted="name,x\n1,2\n", mode="import")
check("a file without a hex column is refused", not ok and "hex" in m, m)
ok, m = post("/admin/interesting/ae0456/exclude")
api = requests.get(B + "/api/interesting.json").json()
check("an excluded aircraft leaves the map feed", ok and "ae0456" not in api["hex"] and "ae0123" in api["hex"], m)
ok, m = post("/admin/interesting/planealert")
n = int(psql("select count(*) from interesting_aircraft where source='planealert'") or 0)
check("the Plane-Alert download brings in thousands of aircraft", ok and n > 5000 and "Plane-Alert" in m, (m, n))
check("it left the hand entry and the excluded one alone", psql("select source from interesting_aircraft where hex='ae0123'") == "manual" and psql("select excluded from interesting_aircraft where hex='ae0456'") == "t")
pa = psql("select hex from interesting_aircraft where source='planealert' order by hex limit 1")
ok, m = post(f"/admin/interesting/{pa}/exclude")
ok, m = post("/admin/interesting/planealert")
check("an excluded Plane-Alert aircraft stays excluded after the next download", psql(f"select excluded from interesting_aircraft where hex='{pa}'") == "t")
ok, m = post(f"/admin/interesting/{pa}/remove")
check("a Plane-Alert entry cannot be removed, only excluded", not ok, m)
t = adm.get(B + "/admin/interesting", params={"q": "ae0123"}).text
check("the list searches by hex", "ae0123" in t and "ae0456" not in t)
cats = adm.get(B + "/admin/interesting").text
check("the page lists the categories found in the download", "Categories in the last download" in cats and "Distinctive" in cats)
check("Data Updates lists Interesting Aircraft", "Interesting Aircraft" in adm.get(B + "/admin/data-updates/").text)
check("the live map loads the list and offers the flag", "/api/interesting.json" in requests.get(B + "/live").text)
pa = requests.get(B + "/api/plane-alert-list.csv")
check("the list is also published in Plane-Alert's own format for the Plane-Alert service to read (header, our entries, categories)", pa.status_code == 200 and pa.text.startswith("$ICAO,$Registration,$Operator,$Type,$ICAO Type,#CMPG") and "AE0123,N1,,added by the test,C17,,,,,Test Category," in pa.text and "AE0456" not in pa.text, pa.text[:200])
ok, m = post("/admin/interesting/ae0123/remove")
check("an entry you added can be removed", ok and psql("select count(*) from interesting_aircraft where hex='ae0123'") == "0", m)
psql("delete from interesting_aircraft")
print("ALL PASS" if ok_all else "SOME FAILED")
