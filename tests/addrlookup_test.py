import json, re, requests, subprocess
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
adm = sess("marty", PW)
H = {"X-Requested-With": "XMLHttpRequest"}
t = adm.get(B + "/admin/hospitals").text
fid = re.search(r"/admin/hospitals/([A-Za-z0-9-]+)/lookup", t).group(1)
row = psql(f"select lat||'|'||lon||'|'||pos_source from hospitals where facility_id='{fid}'")
check("every hospital offers an Address Lookup pre-filled with its CMS address", "Address Lookup" in t and 'class="al-form"' in t and f'/admin/hospitals/{fid}/lookup' in t)
check("settings for the finders are on the page", all(f'name="{k}"' in adm.get(B + "/admin/settings").text for k in ("photon_url", "census_oneline_url", "lookup_cache_days")))
check("an ordinary user cannot look up or set a position", all(sess("plain1", "plainpassword1").post(B + p, data={}, allow_redirects=False).status_code in (302, 400, 401, 403, 404) for p in (f"/admin/hospitals/{fid}/lookup", f"/admin/hospitals/{fid}/position")))
psql("delete from geocode_cache where query like 'test lookup%'")
j = adm.post(B + f"/admin/hospitals/{fid}/lookup", data={"q": "", "csrf_token": tok(adm, "/admin/hospitals")}, headers=H).json()
check("an empty address is refused politely", not j["candidates"] and "all" in j["errors"], j)
q = "test lookup 1600 Pennsylvania Avenue NW Washington DC"
j = adm.post(B + f"/admin/hospitals/{fid}/lookup", data={"q": q, "finders": "census", "csrf_token": tok(adm, "/admin/hospitals")}, headers=H).json()
check("the Census finds the White House and is tagged with its finder", j["ok"] and j["candidates"] and j["candidates"][0]["finder"] == "census" and 38.8 < j["candidates"][0]["lat"] < 39 and not j["errors"], j)
check("the answer is kept", psql("select count(*) from geocode_cache where finder='census' and query like 'test lookup%'") == "1")
psql("update geocode_cache set answer='[{\\\"label\\\": \\\"Kept Answer\\\", \\\"lat\\\": 1, \\\"lon\\\": 2}]' where query like 'test lookup%'")
k = adm.post(B + f"/admin/hospitals/{fid}/lookup", data={"q": q, "finders": "census", "csrf_token": tok(adm, "/admin/hospitals")}, headers=H).json()
check("asking again uses the kept answer", k["candidates"] and k["candidates"][0]["label"] == "Kept Answer", k)
qp = "test lookup Baptist Health Medical Center, 9601 Baptist Health Drive, Little Rock, AR"
j = adm.post(B + f"/admin/hospitals/{fid}/lookup", data={"q": qp, "finders": "photon", "csrf_token": tok(adm, "/admin/hospitals")}, headers=H).json()
check("our own Photon finds a Little Rock hospital by address (or is reported as not ready)", j["ok"] and ((j["candidates"] and j["candidates"][0]["finder"] == "photon" and 34.5 < j["candidates"][0]["lat"] < 35) or "photon" in j["errors"]), j)
psql("insert into settings (key, value) values ('photon_url', 'http://no-such-photon-host:2322') on conflict (key) do update set value=excluded.value")
j = adm.post(B + f"/admin/hospitals/{fid}/lookup", data={"q": qp + " again", "finders": "photon", "csrf_token": tok(adm, "/admin/hospitals")}, headers=H).json()
psql("delete from settings where key='photon_url'")
check("a finder that is not there is reported by name and does not break the answer", j["ok"] and "photon" in j["errors"] and j["candidates"] == [], j)
psql(f"update hospitals set pos_check='conflict' where facility_id='{fid}'")
page = adm.get(B + "/admin/hospitals", params={"check": "review"}).text
check("a hospital the checks could not settle is in the review list, and its Address Lookup always has a map to pick a position on", f"/admin/hospitals/{fid}/position" in page and 'class="al-map"' in page and 'class="al-pick"' in page and "Use the Picked Position" in page)
check("a saved position updates the edit form's Latitude and Longitude (the page listens for the finished action)", 'addEventListener("asyncdone"' in page and 'form[action$="/position"]' in page)
ok = adm.post(B + f"/admin/hospitals/{fid}/position", data={"lat": "38.8977", "lon": "-77.0365", "finder": "test", "csrf_token": tok(adm, "/admin/hospitals")}, headers=H).json()
check("Use This Position stores a hand-set position", ok["ok"] and psql(f"select pos_source from hospitals where facility_id='{fid}'") == "manual" and psql(f"select round(lat::numeric,2) from hospitals where facility_id='{fid}'") == "38.90", ok)
check("setting the position by hand (from a lookup or by clicking the map) takes the hospital off the review list", psql(f"select pos_check from hospitals where facility_id='{fid}'") == "manual", psql(f"select pos_check from hospitals where facility_id='{fid}'"))
psql(f"update hospitals set pos_check='single' where facility_id='{fid}'")
sv = adm.post(B + f"/admin/hospitals/{fid}/save", data={"trauma": "", "heliport": "", "lat": "38.8977", "lon": "-77.0365", "notes": "edited", "csrf_token": tok(adm, "/admin/hospitals")}, headers=H).json()
check("saving an edit of a hospital that needed review settles its flag", sv["ok"] and psql(f"select pos_check from hospitals where facility_id='{fid}'") == "manual", sv)
bad = adm.post(B + f"/admin/hospitals/{fid}/position", data={"lat": "123", "lon": "1", "csrf_token": tok(adm, "/admin/hospitals")}, headers=H).json()
check("an impossible position is refused", not bad["ok"], bad)
r = adm.post(B + "/admin/hospitals/import", data={"csrf_token": tok(adm, "/admin/hospitals")}, headers=H)
check("a new import keeps the hand-set position", psql(f"select pos_source from hospitals where facility_id='{fid}'") == "manual")
a, b, src = row.split("|")
psql(f"update hospitals set lat={a}, lon={b}, pos_source='{src}' where facility_id='{fid}'")
psql("delete from geocode_cache where query like 'test lookup%'")
print("ALL PASS" if ok_all else "SOME FAILED")
