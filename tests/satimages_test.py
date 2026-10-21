"""Satellite images from members' receivers: a member adds a receiver and gets a private upload address, the receiver posts pictures to it (checked, limited, trimmed), the gallery shows them, private ones stay private,
and administrators manage receivers and pictures. Runs against the test portal."""
import base64, re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
def setting(k, v):
    psql(f"INSERT INTO settings (key, value) VALUES ('{k}', '{v}') ON CONFLICT (key) DO UPDATE SET value = '{v}'")
def token(s, path):
    return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + path).text).group(1)
AJAX = {"X-Requested-With": "XMLHttpRequest"}
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
psql("DELETE FROM sat_receivers")
for k in ("satimg_enabled", "satimg_max_mb", "satimg_keep", "satimg_per_hour"):
    psql(f"DELETE FROM settings WHERE key = '{k}'")
adm = sess("marty", PW)
me = sess("plain1", "plainpassword1")
other = sess("claimer1", "claimpassword1")
anon = requests.Session()

g = anon.get(B + "/radio/satellite-images")
check("the gallery is public and says there are no pictures yet", g.status_code == 200 and "No pictures yet" in g.text and "Filters" in g.text)
check("the Radio menu lists it", 'href="/radio/satellite-images"' in anon.get(B + "/").text)
check("My Satellite Receivers needs a sign-in", anon.get(B + "/my/sat-receivers", allow_redirects=False).status_code in (302, 401, 403, 404))
check("My Account has the tab", 'href="/my/sat-receivers"' in me.get(B + "/my/").text)

t = token(me, "/my/sat-receivers")
r = me.post(B + "/my/sat-receivers/add", data={"csrf_token": t, "name": "Backyard Meteor", "kind": "meteor", "place": "Conway", "public": "1", "notes": "QFH"}, headers=AJAX)
j = r.json()
m = re.search(r"/sat/push/([A-Za-z0-9_-]{20,})", j.get("html", ""))
check("a member adds a receiver and sees its private upload address", r.status_code == 200 and j["ok"] and m, j.get("message"))
tok = m.group(1)
URL = B + "/sat/push/" + tok
rid = psql("select id from sat_receivers where name = 'Backyard Meteor'")
check("only a fingerprint of the address is searchable and the address is stored sealed", psql(f"select count(*) from sat_receivers where token_hash = '{tok}' or token_sealed = '{tok}'") == "0")
r = me.post(B + "/my/sat-receivers/add", data={"csrf_token": t, "name": "X", "kind": "meteor"}, headers=AJAX)
check("a name that is too short is refused with a plain message", not r.json()["ok"] and "three letters" in r.json()["message"])

# sending
check("a wrong address is refused", requests.post(B + "/sat/push/notarealaddressnotarealaddress", files={"image": ("a.png", PNG)}).status_code == 404)
r = requests.post(URL, files={"image": ("a.txt", b"hello, this is not a picture")})
check("something that is not a picture is refused", r.status_code == 400 and r.json()["error"] == "not_a_picture")
r = requests.post(URL, files={"image": ("a.png", PNG)}, data={"satellite": "METEOR-M2 3", "product": "MSA false color", "captured": "2026-10-16T05:51:14Z", "note": "good pass"})
check("a PNG is accepted", r.status_code == 200 and r.json()["ok"], r.text[:200])
iid = r.json()["id"]
r = requests.post(URL, data=PNG, headers={"Content-Type": "application/octet-stream"})
check("a picture sent as the whole body is accepted too", r.status_code == 200)
g = anon.get(B + "/radio/satellite-images").text
check("the gallery shows it with the satellite, product, receiver and place", "METEOR-M2 3" in g and "MSA false color" in g and "Backyard Meteor" in g and "Conway" in g and f"/sat/image/{iid}" in g, g[:300])
im = anon.get(B + f"/sat/image/{iid}")
check("the picture is served as the image it is", im.status_code == 200 and im.headers["Content-Type"] == "image/png" and im.content == PNG and im.headers.get("X-Content-Type-Options") == "nosniff")
check("filters work: another satellite shows nothing, this one shows it", "No pictures yet" in anon.get(B + "/radio/satellite-images?sat=NOAA+19").text and "METEOR-M2 3" in anon.get(B + "/radio/satellite-images?sat=METEOR-M2+3&from=2026-10-16&to=2026-10-16").text)
check("the time it was taken is the one sent", psql(f"select to_char(captured_at at time zone 'UTC', 'YYYY-MM-DD HH24:MI') from sat_images where id = {iid}") == "2026-10-16 05:51")

# private stays private
t = token(me, "/my/sat-receivers")
me.post(B + f"/my/sat-receivers/{rid}/save", data={"csrf_token": t, "name": "Backyard Meteor", "kind": "meteor", "place": "Conway", "public": "0", "notes": ""}, headers=AJAX)
check("a private receiver's pictures leave the gallery and cannot be fetched by a visitor", "METEOR-M2 3" not in anon.get(B + "/radio/satellite-images").text and anon.get(B + f"/sat/image/{iid}").status_code == 404 and other.get(B + f"/sat/image/{iid}").status_code == 404)
check("the owner and an administrator still see it", me.get(B + f"/sat/image/{iid}").status_code == 200 and adm.get(B + f"/sat/image/{iid}").status_code == 200)
me.post(B + f"/my/sat-receivers/{rid}/save", data={"csrf_token": t, "name": "Backyard Meteor", "kind": "meteor", "place": "Conway", "public": "1", "notes": ""}, headers=AJAX)
check("nobody else can change or delete a member's receiver", other.post(B + f"/my/sat-receivers/{rid}/delete", data={"csrf_token": token(other, "/my/sat-receivers")}, headers=AJAX).status_code == 404 and psql("select count(*) from sat_receivers") == "1")

# the feed page and the places that must reference it
fp = anon.get(B + "/feed/satellites")
check("a How to Feed page exists for satellite pictures, with the short version, what you need, software setup, troubleshooting and privacy", fp.status_code == 200 and all(x in fp.text for x in ("How to", "The Short Version", "What You Need", "Software Setup", "Troubleshooting", "Your Data and Privacy", "satimage_uploader.py", "/sat/push/YOUR-KEY")))
check("the Feed menu lists it", 'href="/feed/satellites"' in anon.get(B + "/").text and 'href="/feed/satellites"' in anon.get(B + "/feed/").text)
check("every other feeding page lists it too (the list is built from the Feed menu)", all('href="/feed/satellites"' in anon.get(B + x).text for x in ("/feed/acars", "/feed/weather", "/feed/sondes", "/feed/mesh", "/feed/check")))
check("the gallery and My Satellite Receivers point to it", "/feed/satellites" in anon.get(B + "/radio/satellite-images").text and "/feed/satellites" in me.get(B + "/my/sat-receivers").text)
up = anon.get(B + "/static/satimage_uploader.py")
check("the uploader script is served and is valid Python", up.status_code == 200 and compile(up.text, "satimage_uploader.py", "exec") is not None)
chk = me.get(B + "/feed/check?check=1").text
check("Am I Feeding? lists the member's receivers with the time of the last picture", "Your Satellite Receivers" in chk and "Backyard Meteor" in chk and "last picture" in chk)
check("Am I Feeding? tells a visitor to sign in to see theirs", "Your Satellite Receivers" in anon.get(B + "/feed/check?check=1").text)
check("the weather feed page explains sending to the APRS network", "Send Your Weather to the APRS Network" in anon.get(B + "/feed/weather").text)
ro = anon.get(B + "/radio/").text
check("the Radio overview has cards for Satellites (passes, pictures, how to feed), Nets and Winlink Gateways and Space Weather", all(x in ro for x in ("Satellites", 'href="/radio/satellites"', 'href="/radio/satellite-images"', 'href="/feed/satellites"', "Nets and Winlink Gateways", 'href="/radio/winlink"', 'href="/radio/space-weather"')))
ab = anon.get(B + "/about").text
check("the About page has Satellites and Nets", "Satellites" in ab and 'href="/radio/satellite-images"' in ab and "Nets and Winlink Gateways" in ab)
sd = adm.get(B + "/admin/status/data.json").text
check("Service Status has a Satellites check", '"key": "satellites"' in sd.replace('"key":"', '"key": "'))
ov = adm.get(B + "/admin/").text
check("the admin Overview has the satellite tiles and data", "Satellite receivers sending" in ov and "Satellite pictures kept" in ov)
sj = adm.get(B + "/admin/services.json").json()["services"]
check("the service registry lists satellites with a feed page, and nets", any(x["key"] == "satellites" and x["feed"] and x["feed"][1] == "/feed/satellites" for x in sj) and any(x["key"] == "nets" for x in sj))

# limits and trimming
setting("satimg_max_mb", "1")
big = PNG + b"\0" * (1024 * 1024 + 10)
r = requests.post(URL, files={"image": ("big.png", big)})
check("a picture over the size limit is refused", r.status_code == 413 and r.json()["error"] == "too_large")
setting("satimg_max_mb", "8")
setting("satimg_keep", "5")
for _ in range(5):
    requests.post(URL, files={"image": ("a.png", PNG)}, data={"satellite": "METEOR-M2 4"})
check("a receiver keeps only its newest pictures (the oldest are deleted)", psql(f"select count(*) from sat_images where receiver_id = {rid}") == "5" and psql(f"select count(*) from sat_images where id = {iid}") == "0")
setting("satimg_keep", "60")
setting("satimg_per_hour", "7")
r = requests.post(URL, files={"image": ("a.png", PNG)})
r2 = requests.post(URL, files={"image": ("a.png", PNG)})
r3 = requests.post(URL, files={"image": ("a.png", PNG)})
check("a receiver sending too many in an hour is told to wait", r.status_code == 200 and r2.status_code == 200 and r3.status_code == 429 and r3.json()["error"] == "too_many", (r.status_code, r2.status_code, r3.status_code))
setting("satimg_per_hour", "30")

# admin
check("the admin page lists the receiver and its pictures", all(x in adm.get(B + "/admin/sat-images").text for x in ("Backyard Meteor", "Newest Pictures", "Add a Receiver for a Member")))
check("without the permission the admin page is refused", other.get(B + "/admin/sat-images", allow_redirects=False).status_code in (302, 401, 403, 404))
first = psql(f"select id from sat_images where receiver_id = {rid} order by id limit 1")
ta = token(adm, "/admin/sat-images")
r = adm.post(B + f"/admin/sat-images/image/{first}/hide", data={"csrf_token": ta}, headers=AJAX)
check("an administrator can hide a picture: it leaves the gallery and its address", r.json()["ok"] and anon.get(B + f"/sat/image/{first}").status_code == 404)
r = adm.post(B + f"/admin/sat-images/image/{first}/show", data={"csrf_token": ta}, headers=AJAX)
check("and show it again", r.json()["ok"] and anon.get(B + f"/sat/image/{first}").status_code == 200)
r = adm.post(B + "/admin/sat-images/add", data={"csrf_token": ta, "username": "claimer1", "name": "Club GOES dish", "kind": "goes", "place": "Little Rock", "public": "1", "notes": ""}, headers=AJAX)
check("an administrator can add a receiver for a member", r.json()["ok"] and psql("select count(*) from sat_receivers r join users u on u.id = r.owner_id where u.username = 'claimer1' and r.name = 'Club GOES dish'") == "1", r.text[:200])
check("and the member sees it on their own page", "Club GOES dish" in other.get(B + "/my/sat-receivers").text)
r = adm.post(B + "/admin/sat-images/add", data={"csrf_token": ta, "username": "nobodyatall", "name": "Ghost", "kind": "goes"}, headers=AJAX)
check("a member who does not exist is refused", not r.json()["ok"] and "No member" in r.json()["message"])
r = adm.post(B + f"/admin/sat-images/{rid}/enabled", data={"csrf_token": ta, "on": "0"}, headers=AJAX)
check("a receiver switched off cannot send and its pictures are hidden", r.json()["ok"] and requests.post(URL, files={"image": ("a.png", PNG)}).status_code == 404 and "METEOR-M2 4" not in anon.get(B + "/radio/satellite-images").text)
adm.post(B + f"/admin/sat-images/{rid}/enabled", data={"csrf_token": ta, "on": "1"}, headers=AJAX)
r = adm.post(B + f"/admin/sat-images/{rid}/newaddress", data={"csrf_token": ta}, headers=AJAX)
check("a new upload address stops the old one", r.json()["ok"] and requests.post(URL, files={"image": ("a.png", PNG)}).status_code == 404)
newtok = re.search(r"/sat/push/([A-Za-z0-9_-]{20,})", me.get(B + "/my/sat-receivers").text).group(1)
check("and the member's page shows the new one, which works", newtok != tok and requests.post(B + "/sat/push/" + newtok, files={"image": ("a.png", PNG)}).status_code == 200)
i1 = psql(f"select id from sat_images where receiver_id = {rid} order by id limit 1")
r = me.post(B + f"/my/sat-receivers/image/{i1}/delete", data={"csrf_token": token(me, "/my/sat-receivers")}, headers=AJAX)
check("a member can delete one of their pictures", r.json()["ok"] and psql(f"select count(*) from sat_images where id = {i1}") == "0")
r = adm.post(B + f"/admin/sat-images/{rid}/delete", data={"csrf_token": ta}, headers=AJAX)
check("deleting a receiver deletes its pictures", r.json()["ok"] and psql(f"select count(*) from sat_images where receiver_id = {rid}") == "0")

au = adm.get(B + "/admin/audit").text
check("every action is in the audit log", all(x in au for x in ("satrx.add", "satrx.update", "satrx.admin_add", "satimg.admin_hide", "satrx.admin_switch", "satrx.admin_newaddress", "satimg.delete", "satrx.admin_delete")))
st = adm.get(B + "/admin/settings").text
check("its settings are in Admin > Settings", all(x in st for x in ("Satellite Images from Members", "Satellite Images: Receivers per Member", "Satellite Images: Largest Picture", "Satellite Images: Pictures Kept per Receiver", "Satellite Images: Pictures per Hour per Receiver")))
check("Manage Satellite Images is a permission a role can hold and the admin menu lists the page", "Manage Satellite Images" in adm.get(B + "/admin/roles").text and 'href="/admin/sat-images"' in adm.get(B + "/admin/").text)
check("the tables are in a restore area", "Satellite Receivers and Pictures" in adm.get(B + "/admin/backup").text or "sat_receivers" in open("../app/restorepart.py", encoding="utf-8").read())
setting("satimg_enabled", "0")
check("switched off, the gallery is gone and uploads are refused", anon.get(B + "/radio/satellite-images").status_code == 404 and requests.post(B + "/sat/push/" + newtok, files={"image": ("a.png", PNG)}).status_code == 404)
psql("DELETE FROM settings WHERE key = 'satimg_enabled'")
psql("DELETE FROM sat_receivers")
print("ALL PASS" if ok_all else "SOME FAILED")
