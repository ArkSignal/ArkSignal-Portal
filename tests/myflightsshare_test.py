"""Sharing My Flights by link: a flight or the whole diary, unlisted and read only; only a hash of the token is kept and the token is never logged; what the owner leaves off (track, registration, people, notes) is absent from the
page and the data; a removed, expired or switched-off link and another member's flight give a plain 404; the limits, the headers (noindex, no cache, no referrer), and the page at phone width."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
setting("myflights_enabled", "1"); setting("myflights_sharing", "1"); setting("myflights_max_shares", "20"); setting("myflights_share_max_days", "0")
psql("DELETE FROM settings WHERE key = 'access_myflights'")
psql("DELETE FROM users WHERE username LIKE 'mfh%'")
for n in ("mfhone", "mfhtwo"):
    reg = requests.Session()
    reg.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', reg.get(B + "/my/register").text).group(1), "username": n, "email": n + "@example.com", "password": n + "password99", "confirm": n + "password99"})
U1, U2 = sess("mfhone", "mfhonepassword99"), sess("mfhtwo", "mfhtwopassword99")
anon = requests.Session()
def tok(s, url=None):
    return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + (url or "/my/flights/shares")).text).group(1)
def ap(q, code): return next(a for a in U1.get(B + "/my/flights/airports.json", params={"q": q}).json()["airports"] if a["code"] == code)
lit, las, dal = ap("LIT", "LIT"), ap("LAS", "LAS"), ap("DAL", "DAL")
def post(s, **kw):
    kw["csrf_token"] = tok(s, "/my/flights/new")
    return s.post(B + "/my/flights/save", data=kw, allow_redirects=False)
post(U1, from_id=lit["id"], to_id=las["id"], mode="simple", act_out="2024-05-01T12:00", act_in="2024-05-01T14:00", airline="Southwest Airlines", aircraft_type="B738", registration="N123SECRET", flight_no="WN9", notes="private note XYZ", p0_name="Secret Person", p0_class="Economy", p0_reason="Leisure")
post(U1, from_id=lit["id"], to_id=dal["id"], mode="simple", act_out="2024-06-01T12:00", act_in="2024-06-01T13:00", airline="American Airlines", aircraft_type="B738")
post(U2, from_id=dal["id"], to_id=las["id"], mode="simple", act_out="2024-07-01T12:00", act_in="2024-07-01T14:00", registration="N999OTHER", notes="other member note")
f1, f2 = [int(x) for x in psql("SELECT id FROM my_flights WHERE user_id=(SELECT id FROM users WHERE username='mfhone') ORDER BY id").split()]
g1 = int(psql("SELECT id FROM my_flights WHERE user_id=(SELECT id FROM users WHERE username='mfhtwo')"))
psql(f"INSERT INTO my_flight_tracks (flight_id, source, points, n) VALUES ({f1}, 'KML', '[[1714564800,34.73,-92.22,0,0],[1714568400,35.5,-100.0,30000,450],[1714572000,36.08,-115.15,0,0]]', 3)")
def share(s, **kw):
    data = {"csrf_token": tok(s), "days": "30", "label": "", **kw}
    r = s.post(B + "/my/flights/shares/create", data=data, allow_redirects=True)
    m = re.search(r"/share/flights/([A-Za-z0-9_\-]{30,})", r.text)
    return r, (m.group(1) if m else None)
check("signed out: the share page and creating are refused", anon.get(B + "/my/flights/shares", allow_redirects=False).status_code == 302 and anon.post(B + "/my/flights/shares/create", allow_redirects=False).status_code in (302, 400, 401))
check("a flight's page offers Share", f"/my/flights/shares?flight={f1}" in U1.get(B + f"/my/flights/{f1}").text)
check("the share page lists nothing yet and says the link is shown once", "No links yet" in U1.get(B + "/my/flights/shares").text and "shown once" in U1.get(B + "/my/flights/shares").text)
# ---- the whole diary, default choices: track on, registration, people and notes off
r, T = share(U1, show_track="1")
check("a diary link is made and shown once", T is not None and len(T) >= 40, r.text[:300])
pg = anon.get(B + f"/share/flights/{T}")
check("a guest opens it with the route map, statistics and the flights", pg.status_code == 200 and 'id="flmap"' in pg.text and "LIT" in pg.text and "Flights (2)" in pg.text and "Most Visited" in pg.text, pg.status_code)
check("what is left off is not in the page: registration, people, notes (and the other member's data)", not any(x in pg.text for x in ("N123SECRET", "Secret Person", "private note XYZ", "N999OTHER", "other member note", "mfhone", "mfhone@")), [x for x in ("N123SECRET", "Secret Person", "private note XYZ", "N999OTHER", "mfhone") if x in pg.text])
check("no people board when people are off", "People on Board" not in pg.text)
check("the recorded path is in the map data", '"tracks": [{' in pg.text.replace("\n", "") or '"tracks":[{' in pg.text.replace(" ", ""))
check("never indexed, never cached, no referrer", "noindex" in pg.headers.get("X-Robots-Tag", "") and pg.headers.get("Cache-Control") == "no-store" and pg.headers.get("Referrer-Policy") == "no-referrer" and 'name="robots"' in pg.text and "noindex" in pg.text, dict(pg.headers))
check("robots.txt keeps /share/ out", "Disallow: /share/" in anon.get(B + "/robots.txt").text)
fp = anon.get(B + f"/share/flights/{T}/f/{f1}")
check("a flight in the shared diary opens with its track, still without the hidden things", fp.status_code == 200 and "ftmap" in fp.text and "N123SECRET" not in fp.text and "Secret Person" not in fp.text and "private note XYZ" not in fp.text and "Back to the Diary" in fp.text)
check("it has no owner controls (edit, remove track, add track)", "Remove Track" not in fp.text and "Add Track" not in fp.text and "Replace Track" not in fp.text and "/track/upload" not in fp.text)
tj = anon.get(B + f"/share/flights/{T}/track/{f1}.json")
check("the track data comes for a flight of the diary, not for one that has none", tj.status_code == 200 and len(tj.json()["points"]) == 3 and anon.get(B + f"/share/flights/{T}/track/{f2}.json").status_code == 404)
check("another member's flight is not reachable through this link", anon.get(B + f"/share/flights/{T}/f/{g1}").status_code == 404 and anon.get(B + f"/share/flights/{T}/track/{g1}.json").status_code == 404)
check("a wrong token is a plain 404", anon.get(B + "/share/flights/" + "x" * 43).status_code == 404 and anon.get(B + "/share/flights/short").status_code == 404)
# ---- storage and logs
check("only a fingerprint is stored, never the token", psql(f"SELECT COUNT(*) FROM my_flight_shares WHERE token_hash = '{T}' OR token_hash LIKE '%{T[:20]}%'") == "0" and psql("SELECT length(token_hash) FROM my_flight_shares LIMIT 1") == "64")
check("the token is in no audit entry", psql(f"SELECT COUNT(*) FROM audit_log WHERE detail LIKE '%{T[:20]}%' OR target LIKE '%{T[:20]}%'") == "0" and int(psql("SELECT COUNT(*) FROM audit_log WHERE action = 'myflights.share_create'")) >= 1)
check("views are counted", int(psql("SELECT views FROM my_flight_shares ORDER BY id LIMIT 1")) >= 4)
# ---- one flight, with everything on
r, T2 = share(U1, flight_id=str(f1), show_track="1", show_reg="1", show_people="1", show_notes="1", label="Vegas")
pg = anon.get(B + f"/share/flights/{T2}")
check("a flight link opens that flight with the people, the notes and the registration when switched on", pg.status_code == 200 and "N123SECRET" in pg.text and "Secret Person" in pg.text and "private note XYZ" in pg.text and "ftmap" in pg.text and "Back to the Diary" not in pg.text, pg.status_code)
check("a flight link opens no other flight", anon.get(B + f"/share/flights/{T2}/f/{f2}").status_code == 404 and anon.get(B + f"/share/flights/{T2}/f/{f1}").status_code == 404 and anon.get(B + f"/share/flights/{T2}/track/{f2}.json").status_code == 404)
r, T3 = share(U1, flight_id=str(f1))
pg = anon.get(B + f"/share/flights/{T3}")
check("a flight link with everything off still shows route and times, but no track, registration, people or notes", pg.status_code == 200 and "LIT" in pg.text and "N123SECRET" not in pg.text and "Secret Person" not in pg.text and "private note XYZ" not in pg.text, pg.status_code)
r, T4 = share(U1, flight_id=str(f1), show_track="")
pg = anon.get(B + f"/share/flights/{T4}")
check("with the track off there is no map box and the data answers 404", "ftmap" not in pg.text and anon.get(B + f"/share/flights/{T4}/track/{f1}.json").status_code == 404)
r, T5 = share(U1, show_people="1", show_notes="1", show_reg="1")
pg = anon.get(B + f"/share/flights/{T5}")
check("a diary with people on lists them and counts them", "Secret Person" in pg.text and "People on Board" in pg.text and "N123SECRET" in pg.text)
# ---- not for others
check("another member cannot make a link for a flight that is not theirs", U2.post(B + "/my/flights/shares/create", data={"csrf_token": tok(U2), "flight_id": str(f1), "days": "30"}).status_code == 404)
sid = psql("SELECT id FROM my_flight_shares ORDER BY id LIMIT 1")
check("another member cannot remove it", U2.post(B + f"/my/flights/shares/{sid}/revoke", data={"csrf_token": tok(U2)}).status_code == 404 and anon.get(B + f"/share/flights/{T}").status_code == 200)
check("the member's own list shows the links and what each shows", "Vegas" in U1.get(B + "/my/flights/shares").text and "registration, people, notes" in U1.get(B + "/my/flights/shares").text and "Flight LIT to LAS" in U1.get(B + "/my/flights/shares").text)
# ---- removing, expiry, switch
r = U1.post(B + f"/my/flights/shares/{sid}/revoke", data={"csrf_token": tok(U1)}, allow_redirects=True)
check("removing a link makes it a 404 at once", anon.get(B + f"/share/flights/{T}").status_code == 404 and int(psql("SELECT COUNT(*) FROM audit_log WHERE action = 'myflights.share_revoke'")) >= 1)
psql(f"UPDATE my_flight_shares SET expires_at = now() - interval '1 minute' WHERE label = 'Vegas'")
check("an expired link is a 404", anon.get(B + f"/share/flights/{T2}").status_code == 404 and "(ended)" in U1.get(B + "/my/flights/shares").text)
setting("myflights_sharing", "0")
check("with sharing off every link and the page give 404", anon.get(B + f"/share/flights/{T3}").status_code == 404 and U1.get(B + "/my/flights/shares").status_code == 404)
setting("myflights_sharing", "1")
check("and the links work again when it is back on", anon.get(B + f"/share/flights/{T3}").status_code == 200)
# ---- limits
n = int(psql("SELECT COUNT(*) FROM my_flight_shares WHERE user_id=(SELECT id FROM users WHERE username='mfhone')"))
setting("myflights_max_shares", str(n))
r, T6 = share(U1)
check("the limit on links per member is enforced", T6 is None and "can keep" in r.text)
setting("myflights_max_shares", "20")
setting("myflights_share_max_days", "30")
pgs = U1.get(B + "/my/flights/shares").text
check("a longest life removes Never and the longer choices", "1 year" not in pgs and "Never" not in re.sub(r"<td>Never</td>", "", pgs) and "30 days" in pgs, pgs[:0])
bad = U1.post(B + "/my/flights/shares/create", data={"csrf_token": tok(U1), "days": "0"}, allow_redirects=True)
check("a life the site does not allow is refused", "Choose how long" in bad.text)
setting("myflights_share_max_days", "0")
# ---- deleting the flight takes its link; the member's removal takes theirs
r, T7 = share(U1, flight_id=str(f2))
U1.post(B + f"/my/flights/{f2}/delete", data={"csrf_token": tok(U1, "/my/flights")})
check("deleting a flight removes its link", anon.get(B + f"/share/flights/{T7}").status_code == 404)
# ---- phone width
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    o = Options(); o.add_argument("--headless=new"); o.add_argument("--window-size=412,900")
    d = webdriver.Chrome(options=o)
    try:
        d.get(B + f"/share/flights/{T5}")
        w = d.execute_script("return [document.documentElement.scrollWidth, window.innerWidth]")
        check("the shared diary fits a 412 px screen (no sideways scroll of the page)", w[0] <= w[1] + 2, w)
        d.get(B + f"/share/flights/{T3}")
        w = d.execute_script("return [document.documentElement.scrollWidth, window.innerWidth]")
        check("and so does a shared flight", w[0] <= w[1] + 2, w)
    finally:
        d.quit()
except ImportError:
    print("SKIP no selenium")
psql("DELETE FROM users WHERE username LIKE 'mfh%'")
print("ALL PASS" if ok_all else "SOME FAILED")
