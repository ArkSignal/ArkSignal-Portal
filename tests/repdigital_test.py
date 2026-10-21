"""Digital repeater details through the web: DMR ID, the settings of the other digital modes and talkgroups on the repeater's page and in its edit form, the radio code plug import, the call sign lookup, and the in-container unit checks (repdigital_unit.py)."""
import io, re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
def ssh(cmd):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=300).stdout

out = ssh("docker cp ~/adsb-portal/tests/repdigital_unit.py adsb-portal:/tmp/repdigital_unit.py; docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/repdigital_unit.py")
check("repdigital_unit.py passes inside the portal container", "ALL PASS" in out and "FAIL" not in out, out[-600:])

adm = sess("marty", PW)
psql("DELETE FROM repeaters WHERE callsign = 'W5DGT'")
tok = re.search(r'name="csrf_token" value="([^"]+)"', adm.get(B + "/admin/repeaters").text).group(1)
form = {"csrf_token": tok, "freq": "443.2", "offset": "+5", "callsign": "W5DGT", "city": "Conway", "lat": "35.09", "lon": "-92.44", "modes": ["FM", "DMR"], "color_code": "3", "network": "BrandMeister",
        "dmr_id": "310123401", "a_nac": "293", "a_website": "dgt.example.org", "a_ipsc": "BrandMeister 3101", "talkgroups": "1, 3105, Arkansas, static\n2, 10501, ARWX, dynamic"}
r = adm.post(B + "/admin/repeaters/add", data=form, allow_redirects=False)
rid = psql("select id from repeaters where callsign='W5DGT'")
check("an editor adds a DMR repeater with a DMR ID, mode settings and talkgroups", r.status_code in (302, 303) and rid.isdigit(), (r.status_code, rid))
if rid.isdigit():
    pg = adm.get(B + "/repeaters/" + rid).text
    check("the repeater's page shows the DMR ID, the settings and the talkgroups by slot", all(x in pg for x in ("310123401", "P25 NAC", "293", "BrandMeister 3101", "Talkgroups", "3105", "Arkansas", "ARWX", "https://dgt.example.org")), "page")
    ed = adm.get(B + "/my/repeaters/" + rid).text
    check("its edit form carries them", all(x in ed for x in ('name="dmr_id"', 'value="310123401"', 'name="a_nac"', 'name="talkgroups"', "3105, Arkansas")), "edit")
    tok2 = re.search(r'name="csrf_token" value="([^"]+)"', ed).group(1)
    form2 = dict(form, csrf_token=tok2, talkgroups="1, 3105, Arkansas, static", dmr_id="310123402")
    adm.post(B + f"/my/repeaters/{rid}", data=form2, allow_redirects=False)
    pg2 = adm.get(B + "/repeaters/" + rid).text
    check("editing changes the DMR ID and the talkgroups (the removed one is gone)", "310123402" in pg2 and "ARWX" not in pg2 and "Arkansas" in pg2)
    bad = adm.post(B + f"/my/repeaters/{rid}", data=dict(form2, dmr_id="12"), allow_redirects=False)
    check("a DMR ID that is not three to nine digits is refused", psql("select dmr_id from repeaters where id=" + rid) == "310123402")
psql("DELETE FROM repeaters WHERE callsign = 'W5DGT'")

pg = adm.get(B + "/admin/repeaters").text
check("Admin > Repeaters has the radio code plug import", "Import a Radio Code Plug" in pg and 'action="/admin/repeaters/import-plug"' in pg)
plug = "Channel Name,Channel Type,RX Frequency[MHz],TX Frequency[MHz],Time Slot,TX Contact,Color Code,CTC/DCS Encode\nW5UPL Test 1Local,Digital,445.5,445.5,Slot 1,Local,4,00.0\nFRS 1,Analog,462.5625,462.5625,Slot 1,None,0,00.0\n"
tok3 = re.search(r'name="csrf_token" value="([^"]+)"', pg).group(1)
r = adm.post(B + "/admin/repeaters/import-plug", data={"csrf_token": tok3, "label": "web test plug"}, files={"file": ("plug.csv", io.BytesIO(plug.encode()), "text/csv")}, headers={"X-Requested-With": "XMLHttpRequest"})
check("the import takes a channel list and says what it did", r.status_code == 200 and "1 repeaters added" in r.text and "1 channels skipped" in r.text, r.text[:200])
check("an imported repeater is approved at once, without a position", psql("select approved::text || ',' || coalesce(lat::text,'none') from repeaters where callsign='W5UPL'") == "true,none")
_rid = psql("select id from repeaters where callsign='W5UPL'")
check("its page, and its edit form, open without a position (no error)", adm.get(B + "/repeaters/" + _rid).status_code == 200 and adm.get(B + "/my/repeaters/" + _rid).status_code == 200 and "No position yet" in adm.get(B + "/repeaters/" + _rid).text)
check("and the admin page that lists repeaters opens", adm.get(B + "/admin/repeaters").status_code == 200)
psql("DELETE FROM repeaters WHERE callsign = 'W5UPL'")
check("an ordinary member cannot use the import", sess("plain1", "plainpassword1").post(B + "/admin/repeaters/import-plug", data={"label": "x"}, allow_redirects=False).status_code in (302, 400, 401, 403, 404))
j = requests.get(B + "/api/callsign/KD5VCF.json", timeout=30).json()
check("the call sign lookup answers with the licensee data, or nothing, and never a street address", "callsign" in j and "line1" not in str(j) and "address" not in str(j), j)
check("a call sign that is not one gives nothing", requests.get(B + "/api/callsign/not-a-call.json").json() == {"callsign": None})
sp = adm.get(B + "/admin/settings").text
check("the lookup is switchable in Settings > Repeaters", all(f'name="{k}"' in sp for k in ("callsign_lookup_enabled", "callsign_lookup_url", "callsign_cache_days")))
print("ALL PASS" if ok_all else "SOME FAILED")
