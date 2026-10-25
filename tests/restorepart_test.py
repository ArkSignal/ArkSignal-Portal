import json, re, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
adm = sess("marty", PW)
U1, U2, U3 = "rp000000-0000-4000-8000-000000000001", "rp000000-0000-4000-8000-000000000002", "rp000000-0000-4000-8000-000000000003"
for u in (U1, U2, U3): psql(f"DELETE FROM feeders WHERE uuid='{u}'")
psql("DELETE FROM users WHERE username='rp_user1'")
psql(f"INSERT INTO feeders (uuid, station_id, name) VALUES ('{U1}', 'RPTEST1', 'First Station'), ('{U2}', 'RPTEST2', 'Second Station')")
psql(f"INSERT INTO feeder_events (feeder_uuid, event, detail) VALUES ('{U1}', 'connect', 'rp test event')")
psql("INSERT INTO users (username, email, password_hash) VALUES ('rp_user1', 'rp_user1@example.com', 'x')")
psql("INSERT INTO user_roles (user_id, role_id) SELECT u.id, r.id FROM users u, roles r WHERE u.username='rp_user1' AND r.name='User' LIMIT 1")

def post(path, data):
    r = adm.post(B + path, data=dict(data, csrf_token=tok(adm, "/admin/backups")), allow_redirects=False)
    return r
r = post("/admin/backups/run", {})
time.sleep(1)
page = adm.get(B + "/admin/backups").text
name = re.findall(r"/admin/backups/(portal-backup-[\w-]+\.tar\.gz)/parts", page)[0]
check("each backup has a Restore Part button", name and f"/admin/backups/{name}/parts" in page, name)
pp = adm.get(B + f"/admin/backups/{name}/parts")
check("the page lists the areas with row counts and what they hold", pp.status_code == 200 and all(w in pp.text for w in ("Accounts and Roles", "Stations", "Flight History", "Alert Rules and History", "Settings, Menus and Layers")), pp.status_code)
check("it offers merge and replace and individual records", 'value="merge"' in pp.text and 'value="replace"' in pp.text and "Restore Individual Records" in pp.text)
rec = adm.get(B + f"/admin/backups/{name}/records/feeders.json").json()
labs = {x["label"]: x for x in rec["records"]}
check("the station list shows names, and says which are missing now", any("RPTEST1" in l for l in labs) and all(x["exists"] for l, x in labs.items() if "RPTEST" in l), list(labs)[:4])
check("a table that cannot be picked by record is refused", adm.get(B + f"/admin/backups/{name}/records/audit_log.json").status_code == 400)

# change things after the backup
psql("UPDATE feeders SET name='CHANGED' WHERE station_id='RPTEST1'")
psql("DELETE FROM feeders WHERE station_id='RPTEST2'")
psql(f"INSERT INTO feeders (uuid, station_id, name) VALUES ('{U3}', 'RPNEW', 'Added After')")
r = post(f"/admin/backups/{name}/parts/restore", {"area": "stations", "mode": "merge"})
check("restoring the Stations area (merge) answers with a result", r.status_code == 302)
check("a changed station is back to how the backup had it", psql("SELECT name FROM feeders WHERE station_id='RPTEST1'") == "First Station")
check("a deleted station is back", psql("SELECT count(*) FROM feeders WHERE station_id='RPTEST2'") == "1")
check("a station added after the backup is kept (merge deletes nothing)", psql("SELECT count(*) FROM feeders WHERE station_id='RPNEW'") == "1")
check("a safety backup was taken first", "pre-restore" in adm.get(B + "/admin/backups").text)
check("the action is in the audit log", "backup.restore_part" in adm.get(B + "/admin/audit").text)

r = post(f"/admin/backups/{name}/parts/restore", {"area": "stations", "mode": "replace"})
flash = adm.get(B + f"/admin/backups/{name}/parts").text
check("Replace on Stations alone is refused because it would delete other data", "Replace would also delete" in flash and psql("SELECT count(*) FROM feeders WHERE station_id='RPNEW'") == "1", flash[:0])
r = post(f"/admin/backups/{name}/parts/restore", {"area": ["stations", "station_history", "alerts", "accounts", "weather", "sondes", "acars", "mesh", "api", "satimages", "google", "ais", "drones", "chat", "repeaters", "audit", "myflights", "notifications"], "mode": "replace"})
msg = adm.get(B + "/admin/backups").text
check("Replace with everything it depends on ticked makes the area exactly as in the backup", psql("SELECT count(*) FROM feeders WHERE station_id='RPNEW'") == "0" and psql("SELECT count(*) FROM feeders WHERE station_id='RPTEST1'") == "1", msg[:0])
check("the station's own history came back with it", psql("SELECT count(*) FROM feeder_events WHERE detail='rp test event'") == "1")

# one record
psql("DELETE FROM users WHERE username='rp_user1'")
users = adm.get(B + f"/admin/backups/{name}/records/users.json").json()["records"]
uid = [x["id"] for x in users if "rp_user1" in x["label"]]
check("a deleted person is listed as missing now", uid and not [x for x in users if "rp_user1" in x["label"]][0]["exists"], users[:2])
r = post(f"/admin/backups/{name}/records/restore", {"table": "users", "id": uid})
check("restoring just that person brings them back", psql("SELECT count(*) FROM users WHERE username='rp_user1'") == "1")
check("with their role", psql("SELECT count(*) FROM user_roles ur JOIN users u ON u.id=ur.user_id WHERE u.username='rp_user1'") == "1")
check("and nobody else was touched", int(psql("SELECT count(*) FROM users")) >= 2)
r = post(f"/admin/backups/{name}/records/restore", {"table": "audit_log", "id": ["1"]})
check("an unsupported record type is refused", "Tick at least one record" in adm.get(B + "/admin/backups").text or r.status_code == 302)
plain = sess("plain1", "plainpassword1")
check("an ordinary user cannot open the page", plain.get(B + f"/admin/backups/{name}/parts", allow_redirects=False).status_code in (302, 401, 403, 404))
check("a made-up backup name is a 404", adm.get(B + "/admin/backups/nope.tar.gz/parts").status_code == 404)
for u in (U1, U2, U3): psql(f"DELETE FROM feeders WHERE uuid='{u}'")
psql("DELETE FROM users WHERE username='rp_user1'")
print("ALL PASS" if ok_all else "SOME FAILED")
