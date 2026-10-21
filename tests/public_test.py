import re, requests, subprocess, json
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True


def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra)
    ok_all &= bool(cond)


subprocess.run(["ssh", "-q", "nastemp", "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/mkpublic.py"], check=True, capture_output=True)
anon = requests.Session()
h = anon.get(B + "/feeders/").text
check("list shows named, claimed, not-banned stations", all(x in h for x in ("PUB-ALPHA", "PUB-BRAVO", "PUB-CHARLIE")))
check("list hides banned, unnamed and unclaimed", all(x not in h for x in ("PUB-DELTA", "No Station ID Yet", "pubaaaa6")))
order = [m for m in re.findall(r'<a href="/feeders/(PUB-[A-Z]+)">', h)]
check("ranked by positions/day (Charlie 40/s? only 25% uptime)", order[0] in ("PUB-ALPHA", "PUB-CHARLIE") and set(order) == {"PUB-ALPHA", "PUB-BRAVO", "PUB-CHARLIE"}, order)
h2 = anon.get(B + "/feeders/?sort=uptime").text
order2 = re.findall(r'<a href="/feeders/(PUB-[A-Z]+)">', h2)
check("rank by uptime puts Alpha first and Charlie last", order2[0] == "PUB-ALPHA" and order2[-1] == "PUB-CHARLIE", order2)
for per in ("24h", "7d", "30d"):
    check(f"period {per} renders", anon.get(B + f"/feeders/?period={per}").status_code == 200)
check("junk period/sort fall back safely", anon.get(B + "/feeders/?period=x&sort=y").status_code == 200)
# privacy: exact coordinates must not appear anywhere public unless privacy is exact
for label, text in (("list page", h), ("alpha page", anon.get(B + "/feeders/PUB-ALPHA").text), ("api", anon.get(B + "/api/feeders.json").text)):
    check(f"general-privacy exact coordinates not in {label}", "34.315231" not in text and "-92.410357" not in text)
    check(f"hidden station coordinates not in {label}", "35.123456" not in text and "-92.654321" not in text and re.search(r"(?<![\d.])35\.1(?!\d)", text) is None, "")
api = anon.get(B + "/api/feeders.json").json()
by = {f["station_id"]: f for f in api["feeders"]}
check("api lists exactly the three public stations", set(by) == {"PUB-ALPHA", "PUB-BRAVO", "PUB-CHARLIE"}, list(by))
check("general privacy rounded to 0.1 degree", by["PUB-ALPHA"]["lat"] == 34.3 and by["PUB-ALPHA"]["lon"] == -92.4 and by["PUB-ALPHA"]["location_precision"] == "approximate", by["PUB-ALPHA"])
check("exact privacy shows precise point", by["PUB-BRAVO"]["lat"] == 36.06241 and by["PUB-BRAVO"]["location_precision"] == "exact", by["PUB-BRAVO"])
check("hidden privacy has no coordinates", by["PUB-CHARLIE"]["lat"] is None and by["PUB-CHARLIE"]["lon"] is None, by["PUB-CHARLIE"])
check("api never exposes UUIDs, IPs or private notes", "pubaaaa" not in json.dumps(api) and "203.0.113.77" not in json.dumps(api) and "PRIVATE NOTE" not in json.dumps(api))
check("api totals present", api["totals"]["feeders_online"] >= 1 and "messages_per_second" in api["totals"], api["totals"])
a = anon.get(B + "/feeders/PUB-ALPHA").text
check("station page shows public info", "Alpha Station" in a and "Test SDR" in a and "approximate" in a)
check("station page hides IP, UUID and private notes", "203.0.113.77" not in a and "pubaaaa1" not in a and "PRIVATE NOTE" not in a)
b = anon.get(B + "/feeders/PUB-BRAVO").text
check("exact-privacy page shows the exact-location notice", "exact location" in b and "36.06" in b)
c = anon.get(B + "/feeders/PUB-CHARLIE").text
check("hidden station page has no map", 'id="stationmap"' not in c)
check("banned/unnamed/unknown station pages are 404", all(anon.get(B + f"/feeders/{x}").status_code == 404 for x in ("PUB-DELTA", "No-Such", "pubaaaa5-0000-4000-8000-000000000005")))
check("station lookup is case-insensitive", anon.get(B + "/feeders/pub-alpha").status_code == 200)
print("ALL PASS" if ok_all else "SOME FAILED")
