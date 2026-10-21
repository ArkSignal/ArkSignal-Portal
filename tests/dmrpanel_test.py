"""A repeater's DMR panel (color code, repeater ID, network, the two time slots with their talkgroups, adding and removing a talkgroup from the standard list or by number) and the combined
Call Signs, Contacts and Talkgroups page."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", "docker exec -i adsb-portal-db psql -U portal -d portal -tA"], input=sql, capture_output=True, text=True, timeout=60).stdout.strip()
adm = sess("marty", PW)
psql("DELETE FROM repeaters WHERE callsign = 'W5PNL'")
tok = tok_of = re.search(r'name="csrf_token" value="([^"]+)"', adm.get(B + "/admin/repeaters").text).group(1)
adm.post(B + "/admin/repeaters/add", data={"csrf_token": tok, "freq": "443.3", "offset": "+5", "callsign": "W5PNL", "city": "Conway", "lat": "35.09", "lon": "-92.44", "modes": ["FM", "DMR"], "color_code": "1", "network": "BrandMeister", "dmr_id": "310123499"}, allow_redirects=False)
rid = psql("select id from repeaters where callsign='W5PNL'")
check("a DMR repeater exists to try", rid.isdigit(), rid)
if rid.isdigit():
    pg = adm.get(B + f"/repeaters/{rid}").text
    check("its page has a DMR panel with both time slots even before any talkgroup is listed", all(x in pg for x in ("DMR: Time Slots and Talkgroups", "Time Slot 1", "Time Slot 2", "310123499", "BrandMeister")) and pg.count("No talkgroups listed for this slot yet") == 2)
    check("an editor sees the form to add one, with the standard talkgroups", 'action="/repeaters/%s/talkgroups/add"' % rid in pg and "Standard talkgroups" in pg and "3100: USA Nationwide" in pg and "9990: Parrot" in pg)
    ajax = {"X-Requested-With": "XMLHttpRequest"}
    t2 = re.search(r'name="csrf_token" value="([^"]+)"', pg).group(1)
    r1 = adm.post(B + f"/repeaters/{rid}/talkgroups/add", data={"csrf_token": t2, "tg": "3105", "slot": "1", "mode": "static", "name": ""}, headers=ajax)
    r2 = adm.post(B + f"/repeaters/{rid}/talkgroups/add", data={"csrf_token": t2, "tg": "31051", "slot": "2", "mode": "dynamic", "name": "ARWX"}, headers=ajax)
    check("a standard talkgroup and one by number and name can be added", r1.status_code == 200 and r2.status_code == 200, (r1.text[:100], r2.text[:100]))
    pg = adm.get(B + f"/repeaters/{rid}").text
    check("they show on their slots with their link mode", "Arkansas" in pg and "31051" in pg and "ARWX" in pg and "static" in pg and "dynamic" in pg and pg.count("No talkgroups listed for this slot yet") == 0, "page")
    r3 = adm.post(B + f"/repeaters/{rid}/talkgroups/add", data={"csrf_token": t2, "tg": "", "slot": "3", "mode": "static", "name": ""}, headers=ajax)
    check("a bad slot or an empty choice is refused", r3.status_code == 400)
    check("an ordinary visitor sees the slots but no edit form", "talkgroups/add" not in requests.get(B + f"/repeaters/{rid}").text and "Time Slot 1" in requests.get(B + f"/repeaters/{rid}").text)
    check("an ordinary visitor cannot add one", requests.post(B + f"/repeaters/{rid}/talkgroups/add", data={"tg": "3100"}, allow_redirects=False).status_code in (302, 400, 401, 403))
    row = psql(f"select id from repeater_talkgroups where repeater_id={rid} and tg_id=31051")
    r4 = adm.post(B + f"/repeaters/{rid}/talkgroups/{row}/remove", data={"csrf_token": t2}, headers=ajax)
    check("a talkgroup can be removed", r4.status_code == 200 and ">31051<" not in adm.get(B + f"/repeaters/{rid}").text.replace(" class=\"mono\" style=\"width:70px\"", ""), (row, r4.status_code, r4.text[:150]))
    tgp = requests.get(B + "/callsign", params={"tab": "talkgroups"}).text
    check("the Talkgroups tab lists the standard talkgroups and which repeaters carry them", "USA Nationwide" in tgp and "Parrot" in tgp and "W5PNL" in tgp and f'/repeaters/{rid}' in tgp)
psql("DELETE FROM repeaters WHERE callsign = 'W5PNL'")
check("the Talkgroups tab can be searched", "Parrot" in requests.get(B + "/callsign", params={"tab": "talkgroups", "q": "9990"}).text and "USA Nationwide" not in requests.get(B + "/callsign", params={"tab": "talkgroups", "q": "9990"}).text)
ct = requests.get(B + "/callsign", params={"tab": "contacts", "q": "Local"}).text
check("the contacts tab asks visitors to sign in", "signed-in members" in ct)
psql("DELETE FROM dmr_contacts WHERE id = 9990777; INSERT INTO dmr_contacts (id, name, city, state, country, kind) VALUES (9990777, 'ZZ9TEST Pat', 'Conway', 'Arkansas', 'United States', 'Private Call')")
cm = adm.get(B + "/callsign", params={"tab": "contacts", "q": "ZZ9TEST"}).text
check("a signed-in member finds a contact by name, with its DMR ID", "9990777" in cm and "ZZ9TEST Pat" in cm and "Conway" in cm)
check("or by DMR ID", "ZZ9TEST Pat" in adm.get(B + "/callsign", params={"tab": "contacts", "q": "999077"}).text)
psql("DELETE FROM dmr_contacts WHERE id = 9990777")
check("the three tabs are on every part of the page", all(x in requests.get(B + "/callsign").text for x in ("Call Sign Lookup", "DMR Contacts", "Talkgroups")))
print("ALL PASS" if ok_all else "SOME FAILED")
