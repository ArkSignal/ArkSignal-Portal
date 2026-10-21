import io, re, subprocess, tarfile, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
D = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker "
def ssh(cmd): return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True).stdout
def psql(sql): return ssh(D + f"exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\"").strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
def csrf(s, path): return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + path).text).group(1)
# the in-container checks of the vault
subprocess.run(["scp", "-q", "vault_unit.py", "nastemp:adsb-portal/vault_unit.py"], check=True, capture_output=True)
ssh(D + "cp ~/adsb-portal/vault_unit.py adsb-portal:/tmp/vault_unit.py")
out = ssh(D + "exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/vault_unit.py")
print(out.strip())
check("the vault (encryption of stored secrets) passes its unit checks", "ALL PASS" in out and "FAIL" not in out, out[-300:])
anon = requests.Session(); adm = sess("marty", PW); other = sess("plain1", "plainpassword1")
# ---- 1. exact station locations never leave the server unless the owner chose Exact
LAT, LON = 34.1234567, -92.7654321
FRAG = ["34.12345", "92.76543", "34.1234", "92.7654"]
UG, UH = "dddddddd-1111-2222-3333-4444444444a1", "dddddddd-1111-2222-3333-4444444444b2"
psql("DELETE FROM feeders WHERE uuid LIKE 'dddddddd-1111-2222-3333-4444444444%'")
for u, sid, priv in ((UG, "PRIV-GEN", "general"), (UH, "PRIV-HID", "hidden")):
    psql(f"INSERT INTO feeders (uuid, station_id, name, owner_id, lat, lon, privacy, online) VALUES ('{u}', '{sid}', 'Privacy {priv}', (SELECT id FROM users WHERE username='marty'), {LAT}, {LON}, '{priv}', TRUE)")
    for sector, nm in ((0, 90.0), (9, 140.0), (18, 60.0), (27, 175.0)):
        psql(f"INSERT INTO feeder_range (feeder_uuid, day, sector, max_nm, max_alt) VALUES ('{u}', (now() AT TIME ZONE 'utc')::date, {sector}, {nm}, 35000)")
URLS = ["/", "/feeders/", "/feeders/?sort=range", "/feeders/PRIV-GEN", "/feeders/PRIV-HID", "/PRIV-GEN", "/PRIV-HID", "/live", "/live?feeder=PRIV-GEN", "/live?feeder=PRIV-HID",
        "/api/feeders.json", "/api/coverage.json", "/api/aircraft.json", "/api/aircraft.json?feeder=PRIV-GEN,PRIV-HID", "/metrics", "/feed/", "/flight/", "/history/", "/airports/",
        "/view/network-feeders?station=PRIV-GEN", "/about", "/contact", "/api/history/flights.json"]
leaks = []
for who, s in (("a visitor", anon), ("another member", other)):
    for u in URLS:
        t = s.get(B + u).text
        for f in FRAG:
            if u.startswith("/api/aircraft.json") and len(f) < 8:   # a real aircraft can fly past a four-decimal coordinate; the long ones cannot be a coincidence
                continue
            if f in t: leaks.append((who, u, f))
        for uid in (UG, UH):
            if uid in t: leaks.append((who, u, "station UUID"))
check("no public page or feed shows a General or Hidden station's exact coordinates or its UUID, to visitors or to other members", not leaks, leaks[:5])
j = anon.get(B + "/api/feeders.json").json()["feeders"]
g = [x for x in j if x["station_id"] == "PRIV-GEN"]; h = [x for x in j if x["station_id"] == "PRIV-HID"]
check("a General station is published rounded, a Hidden one not at all", g and abs(g[0]["lat"] - 34.1) < 0.01 and (not h or h[0]["lat"] is None), (g, h))
cv = anon.get(B + "/api/coverage.json").json()["stations"]
check("coverage is drawn around the rounded place for General and not at all for Hidden", [c for c in cv if c["id"] == "PRIV-GEN" and abs(c["lat"] - 34.1) < 0.01] and not [c for c in cv if c["id"] == "PRIV-HID"])
check("another member cannot open someone else's station management page", other.get(B + f"/my/feeders/{UG}", allow_redirects=False).status_code in (403, 404))
check("another member cannot open the admin station list", other.get(B + "/admin/feeders", allow_redirects=False).status_code in (302, 403, 404))
check("a visitor cannot open the admin station list", anon.get(B + "/admin/feeders", allow_redirects=False).status_code in (302, 401, 403, 404))
al = adm.get(B + "/admin/audit").text
check("the audit log never records coordinates", not any(f in al for f in FRAG))
# ---- 2. no plain-text passwords or secrets anywhere in the database or its backups
setting("backup_encrypt", "0"); setting("log_unknown_usernames", "masked")
UNK = "SuperSecretTypo123xyz"
v = requests.Session(); v.post(B + "/my/login", data={"csrf_token": csrf(v, "/my/login"), "username": UNK, "password": "whatever-123456"})
check("a password typed into the username box is not kept in the sign-in log", UNK not in psql("SELECT string_agg(username, ',') FROM login_events") and "Su" in psql("SELECT string_agg(username, ',') FROM login_events WHERE detail='unknown username'"))
adm.post(B + "/admin/backups/run", data={"csrf_token": csrf(adm, "/admin/backups")}, headers={"X-Requested-With": "XMLHttpRequest"})
names = sorted(set(re.findall(r"portal-backup-\d{8}-\d{6}-manual\.tar\.gz", adm.get(B + "/admin/backups").text)))
raw = adm.get(B + f"/admin/backups/{names[-1]}/download").content
check("with encryption off a backup is a plain archive", raw[:2] == b"\x1f\x8b", raw[:12])
blob = ""
with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
    for m in tar.getmembers():
        if m.isfile() and m.name.endswith(".csv"): blob += tar.extractfile(m).read().decode("utf-8", "replace") + "\n"
for pw in (PW, "plainpassword1", "claimpassword1", "tfpassword123", "alertpassword1", "emailreset-pass1"):
    check(f"a known test password does not appear in any table ({pw[:4]}...)", pw not in blob)
check("password hashes are scrypt hashes, not passwords", bool(re.search(r"scrypt:\d+:\d+:\d+\$", blob)))
rows = psql("SELECT COUNT(*) FROM users WHERE (totp_secret <> '' AND totp_secret NOT LIKE 'enc1:%') OR (webhook_url <> '' AND webhook_url NOT LIKE 'enc1:%')")
check("no user has an unsealed two-factor secret or webhook address", rows == "0", rows)
setting("backup_encrypt", "1")
adm.post(B + "/admin/backups/run", data={"csrf_token": csrf(adm, "/admin/backups")}, headers={"X-Requested-With": "XMLHttpRequest"})
names2 = sorted(set(re.findall(r"portal-backup-\d{8}-\d{6}-manual\.tar\.gz", adm.get(B + "/admin/backups").text)))
enc = adm.get(B + f"/admin/backups/{names2[-1]}/download").content
check("with encryption on (the default) a backup is an encrypted file", enc[:8] == b"ADSBPBK1", enc[:12])
check("an encrypted backup shows no names, hashes or coordinates", all(x not in enc for x in (b"marty", b"scrypt", b"34.1234", b"PRIV-GEN", b"feeders")))
check("the backups page marks it encrypted", "encrypted" in adm.get(B + "/admin/backups").text)
check("a backup setting exists", "Encrypt Backups" in adm.get(B + "/admin/settings").text)
setting("log_unknown_usernames", "exact")
for n_ in set(names) | set(names2):   # leave no extra backups behind for the suites that count them
    adm.post(B + f"/admin/backups/{n_}/delete", data={"csrf_token": csrf(adm, "/admin/backups")}, headers={"X-Requested-With": "XMLHttpRequest"})
psql("DELETE FROM feeder_range WHERE feeder_uuid LIKE 'dddddddd-1111-2222-3333-4444444444%'"); psql("DELETE FROM feeders WHERE uuid LIKE 'dddddddd-1111-2222-3333-4444444444%'")
print("ALL PASS" if ok_all else "SOME FAILED")
