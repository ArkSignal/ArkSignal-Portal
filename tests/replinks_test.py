"""Linked repeaters: groups of repeaters that are linked together as one system: the list shows a badge and filters by system, a repeater's page lists the others in its system, administrators and the repeater's
owner can make and change systems, the map data carries the lines (the shortest set joining the members), and the Repeater Map has the layer."""
import json, re, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
tag = str(int(time.time()) % 100000)
psql("DELETE FROM repeater_groups")
ids = []
for i, (call, la, lo) in enumerate((("ZZ1" + tag, 34.7, -92.3), ("ZZ2" + tag, 35.2, -92.9), ("ZZ3" + tag, 36.1, -94.1), ("ZZ4" + tag, 34.0, -91.9))):
    r = psql(f"INSERT INTO repeaters (callsign, city, state, freq_hz, offset_hz, modes, kind, status, source, approved, lat, lon) VALUES ('{call}', 'Zz{i}', 'AR', {146700000 + i * 60000}, 600000, 'FM', 'repeater', 'active', 'test', TRUE, {la}, {lo}) RETURNING id")
    ids.append(int(r.split()[0]))
adm = sess("marty", PW)
ta = tok(adm, "/admin/repeaters")
adm.post(B + "/admin/repeaters/groups/add", data={"csrf_token": ta, "name": "ZZ Test Link " + tag, "notes": "a test system", "members": ", ".join(str(i) for i in ids[:3])}, allow_redirects=False)
gid = psql(f"SELECT id FROM repeater_groups WHERE name='ZZ Test Link {tag}'")
check("an administrator makes a linked system with its repeaters", gid.isdigit() and psql(f"SELECT count(*) FROM repeater_group_members WHERE group_id={gid}") == "3")
check("the admin page lists it", "ZZ Test Link " + tag in adm.get(B + "/admin/repeaters").text)
lst = requests.get(B + "/repeaters/?q=ZZ").text
check("the repeater list shows a badge with the system on its three members and not on the fourth", lst.count("&#128279; ZZ Test Link " + tag) == 3 and f'/repeaters/?group={gid}' in lst)
only = requests.get(B + f"/repeaters/?group={gid}").text
check("the list can be filtered to one system", "ZZ1" + tag in only and "ZZ3" + tag in only and "ZZ4" + tag not in only)
check("the system is in the filter list", f'<option value="{gid}"' in lst)
pg = requests.get(B + f"/repeaters/{ids[0]}").text
check("a repeater's page lists the others in its system", "Linked Repeaters" in pg and "ZZ2" + tag in pg and "ZZ3" + tag in pg and "ZZ4" + tag not in pg)
g = None
for _ in range(40):
    g = next((x for x in requests.get(B + "/api/repeaters/links.json").json()["groups"] if x["name"] == "ZZ Test Link " + tag), None)
    if g: break
    time.sleep(1)
check("the map data carries the system with its members and the shortest set of lines joining them (two lines for three repeaters)", g and len(g["members"]) == 3 and len(g["lines"]) == 2, g)
if g:
    pts = {(round(l[0], 1), round(l[1], 1)) for l in g["lines"]} | {(round(l[2], 1), round(l[3], 1)) for l in g["lines"]}
    check("and no line joins the two repeaters that are farthest apart", len(g["lines"]) == 2 and {(34.7, -92.3), (35.2, -92.9), (36.1, -94.1)} <= pts)
def fresh_map():   # each web worker keeps the map data for up to 30 seconds
    for _ in range(40):
        m = requests.get(B + "/api/repeaters/map.json").json()["repeaters"]
        if any(p["id"] == ids[0] and p["groups"] for p in m):
            return m
        time.sleep(1)
    return m
mp = fresh_map()
check("the map points say which systems each repeater is in", any(p["id"] == ids[0] and any(x["name"] == "ZZ Test Link " + tag for x in p["groups"]) for p in mp) and all(p.get("groups") == [] for p in mp if p["id"] == ids[3]))
check("the Repeater Map has the links layer", 'id="rm-links"' in requests.get(B + "/repeaters/map").text)
tr = tok(adm, f"/repeaters/{ids[3]}")
adm.post(B + f"/repeaters/{ids[3]}/link", data={"csrf_token": tr, "group": gid}, allow_redirects=False)
check("a repeater can be added to a system from its own page", psql(f"SELECT count(*) FROM repeater_group_members WHERE group_id={gid}") == "4")
adm.post(B + f"/repeaters/{ids[3]}/link", data={"csrf_token": tr, "name": "ZZ Second " + tag}, allow_redirects=False)
check("or to a new one by name", psql(f"SELECT count(*) FROM repeater_groups WHERE name='ZZ Second {tag}'") == "1")
adm.post(B + f"/repeaters/{ids[3]}/link/{psql(f'''SELECT id FROM repeater_groups WHERE name='ZZ Second {tag}' ''')}/remove", data={"csrf_token": tr}, allow_redirects=False)
check("taking the last repeater out of a system removes the empty system", psql(f"SELECT count(*) FROM repeater_groups WHERE name='ZZ Second {tag}'") == "0")
plain = sess("plain1", "plainpassword1")
check("a member who is not the owner cannot link a repeater", plain.post(B + f"/repeaters/{ids[0]}/link", data={"csrf_token": tok(plain, "/my/repeaters"), "name": "ZZ Nope"}, allow_redirects=False).status_code in (302, 403, 404))
adm.post(B + f"/admin/repeaters/groups/{gid}/delete", data={"csrf_token": ta}, allow_redirects=False)
check("deleting a system keeps its repeaters", psql(f"SELECT count(*) FROM repeater_groups WHERE id={gid}") == "0" and psql(f"SELECT count(*) FROM repeaters WHERE id={ids[0]}") == "1")
psql("DELETE FROM repeaters WHERE callsign LIKE 'ZZ_" + tag + "'")
print("ALL PASS" if ok_all else "SOME FAILED")
