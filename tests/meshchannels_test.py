"""Mesh Channels: the channel address and QR code (a script run inside the portal container, decoding the address back), and the member pages: making, sharing, who can see the QR and the key, limits, blocking, leaving, replacing the key, deleting, the administrators' list that never shows a key, settings, audit log, restore area. Needs the test box."""
import json, re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
SSH = lambda cmd: subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=300)
DK = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker"
def psql(sql):
    return SSH(f"{DK} exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\"").stdout.strip()
subprocess.run(["scp", "-q", "meshchannels_unit.py", "nastemp:adsb-portal/meshchannels_unit.py"], check=True, capture_output=True)
SSH(f"{DK} cp ~/adsb-portal/meshchannels_unit.py adsb-portal:/tmp/meshchannels_unit.py")
o = SSH(f"{DK} exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/meshchannels_unit.py").stdout.strip().splitlines()
try:
    res = json.loads(o[-1])
except Exception:
    res = {}
    print("unit output:", o[-5:])
for k in ("matches_the_apps_default_channel_address", "round_trip", "add_and_replace_addresses", "no_options_means_no_fields", "qr_is_an_svg", "default_channel_address_is_the_apps_own", "several_channels_in_one_address"):
    check("channel address: " + k.replace("_", " "), res.get(k) is True, res)
psql("DELETE FROM mesh_channels")
psql("DELETE FROM pm_blocks")
for k, v in (("mesh_enabled", "1"), ("mesh_channels_enabled", "1"), ("mesh_channels_per_member", "2"), ("mesh_channel_max_members", "2")):
    psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
adm = sess("marty", PW); me = sess("plain1", "plainpassword1"); other = sess("claimer1", "claimpassword1")
def csrf(s):
    return re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + "/my/mesh/channels").text).group(1)
def post(s, path, **data):
    return s.post(B + path, data=dict(data, csrf_token=csrf(s)), headers={"X-Requested-With": "XMLHttpRequest"})
def audit_tail():
    return psql("select action from audit_log order by id desc limit 4")
check("a bad name is refused with the rule", "11 letters" in post(me, "/my/mesh/channels/add", name="way too long name!", precision="0").json()["message"])
r = post(me, "/my/mesh/channels/add", name="ArkFriends", key_bits="256", uplink="1", precision="14", note="for the club")
check("a channel is made", r.json()["ok"] and psql("select count(*) from mesh_channels") == "1")
cid = psql("select id from mesh_channels")
sealed = psql("select psk_sealed from mesh_channels")
check("its key is stored sealed, never as plain text", len(sealed) > 60 and not re.fullmatch(r"[A-Za-z0-9+/=]{44}", sealed), sealed[:12])
check("the same name again is refused", "already have" in post(me, "/my/mesh/channels/add", name="arkfriends", precision="0").json()["message"])
check("the per-member limit holds", post(me, "/my/mesh/channels/add", name="Second", precision="0").json()["ok"] and "can make 2" in post(me, "/my/mesh/channels/add", name="Third", precision="0").json()["message"])
page = me.get(B + "/my/mesh/channels").text
check("the owner's page shows the channel, its QR image and the sharing form, and the menu lists Mesh Channels", "ArkFriends" in page and f"/my/mesh/channels/{cid}/qr.svg" in page and 'name="to"' in page and 'href="/my/mesh/channels"' in me.get(B + "/my/mesh").text)
q = me.get(B + f"/my/mesh/channels/{cid}/qr.svg")
check("the owner gets the QR code as an image that is never cached", q.status_code == 200 and q.headers["Content-Type"].startswith("image/svg+xml") and "no-store" in q.headers.get("Cache-Control", "") and q.text.lstrip().startswith("<svg"))
check("a stranger gets nothing: no QR, no key, no page entry", other.get(B + f"/my/mesh/channels/{cid}/qr.svg").status_code == 404 and post(other, f"/my/mesh/channels/{cid}/key").status_code == 404 and "ArkFriends" not in other.get(B + "/my/mesh/channels").text)
k = post(me, f"/my/mesh/channels/{cid}/key").json()
check("the owner can show the key and both addresses, and it is audit-logged", k["ok"] and k["bits"] == 256 and len(k["hex"]) == 64 and "?add=true#" in k["address"] and "?add=true" not in k["replace_address"] and "meshchannel.key_shown" in audit_tail())
check("a stranger cannot share, change, replace or delete it", all(post(other, f"/my/mesh/channels/{cid}/{a}", to="plain1").status_code == 404 for a in ("share", "save", "rotate", "delete", "unshare")))
check("sharing with a member works and is logged", post(me, f"/my/mesh/channels/{cid}/share", to="claimer1").json()["ok"] and psql("select count(*) from mesh_channel_members") == "1" and "meshchannel.share" in audit_tail())
check("the member now sees it with the same QR and key, but cannot edit it", "ArkFriends" in other.get(B + "/my/mesh/channels").text and other.get(B + f"/my/mesh/channels/{cid}/qr.svg").text == q.text and post(other, f"/my/mesh/channels/{cid}/key").json()["hex"] == k["hex"] and post(other, f"/my/mesh/channels/{cid}/save", precision="0").status_code == 404)
check("an unknown member is refused", "No member found" in post(me, f"/my/mesh/channels/{cid}/share", to="nobody-here-x").json()["message"])
check("the people limit holds", post(me, f"/my/mesh/channels/{cid}/share", to="marty").json()["ok"] and "2 people at most" in post(me, f"/my/mesh/channels/{cid}/share", to="emailtest1").json()["message"])
post(me, f"/my/mesh/channels/{cid}/unshare", user="marty")
adm_page = adm.get(B + "/admin/mesh").text
check("the administrators' list shows the channel and owner but never a key", "ArkFriends" in adm_page and k["hex"] not in adm_page and k["base64"] not in adm_page)
before = k["hex"]
check("replacing the key changes it for everyone", post(me, f"/my/mesh/channels/{cid}/rotate").json()["ok"] and post(other, f"/my/mesh/channels/{cid}/key").json()["hex"] != before)
check("unsharing removes the person", post(me, f"/my/mesh/channels/{cid}/unshare", user="claimer1").json()["ok"] and other.get(B + f"/my/mesh/channels/{cid}/qr.svg").status_code == 404)
post(me, f"/my/mesh/channels/{cid}/share", to="claimer1")
check("a member can leave a channel shared with them", post(other, f"/my/mesh/channels/{cid}/leave").json()["ok"] and other.get(B + f"/my/mesh/channels/{cid}/qr.svg").status_code == 404)
psql("INSERT INTO pm_blocks (blocker_id, blocked_id) SELECT a.id, b.id FROM users a, users b WHERE a.username='claimer1' AND b.username='plain1'")
check("a member who blocked the owner is skipped, not given the key", "skipped" in post(me, f"/my/mesh/channels/{cid}/share", to="claimer1").json()["message"] and psql("select count(*) from mesh_channel_members m join users u on u.id=m.user_id where u.username='claimer1'") == "0")
psql("DELETE FROM pm_blocks")
old_addr = post(me, f"/my/mesh/channels/{cid}/key").json()["address"]
check("the options can be changed and the address changes with them", post(me, f"/my/mesh/channels/{cid}/save", downlink="1", precision="0", note="x").json()["ok"] and post(me, f"/my/mesh/channels/{cid}/key").json()["address"] != old_addr)
check("an administrator can remove a channel, and it is logged", post(adm, f"/admin/mesh/channels/{cid}/delete").json()["ok"] and psql("select count(*) from mesh_channels where id = " + cid) == "0" and "meshchannel.admin_delete" in audit_tail())
cid2 = psql("select id from mesh_channels limit 1")
check("the owner can delete a channel", post(me, f"/my/mesh/channels/{cid2}/delete").json()["ok"] and psql("select count(*) from mesh_channels") == "0")
pg2 = me.get(B + "/my/mesh/channels").text
check("the position choices say how far a position is blurred, in the viewer's units", "14 bits: blurred to about" in pg2 and re.search(r"10 bits: blurred to about [0-9.]+ (mi|km)", pg2) and "Exact: no blurring" in pg2)
check("the page's forms reload the page (they are not in-page actions), so a made or deleted channel shows at once", "data-async" not in pg2.split('<script')[0])
check("the default channel is on the page with its own QR code and address", "The Default Channel" in pg2 and "combine.svg?ids=default" in pg2 and "#CgMSAQE" in pg2)
r1 = post(me, "/my/mesh/channels/add", name="CodeOne", precision="0").json()
post(me, "/my/mesh/channels/add", name="CodeTwo", precision="16")
ids = psql("select string_agg(id::text, ',' order by id) from mesh_channels")
a, b = ids.split(",")[0], ids.split(",")[1]
cm = post(me, "/my/mesh/channels/combine", ids=f"default,{a},{b}").json()
check("one code for the default channel and two of mine: one address holding all three, and it is audit-logged", cm["ok"] and cm["count"] == 3 and "?add=true#" in cm["address"] and "?add=true" not in cm["replace_address"] and "meshchannel.combine" in audit_tail())
q1 = me.get(B + f"/my/mesh/channels/combine.svg?ids=default,{a},{b}")
check("and its QR code is an image that is never cached", q1.status_code == 200 and q1.text.lstrip().startswith("<svg") and "no-store" in q1.headers.get("Cache-Control", "") and me.get(B + f"/my/mesh/channels/combine.svg?ids=default,{a},{b}&replace=1").text != q1.text)
check("a stranger cannot put someone else's channel in a code", post(other, "/my/mesh/channels/combine", ids=f"default,{a}").status_code == 400 and other.get(B + f"/my/mesh/channels/combine.svg?ids={a}").status_code == 400)
check("nothing chosen, junk, or more than eight channels is refused", post(me, "/my/mesh/channels/combine", ids="").status_code == 400 and post(me, "/my/mesh/channels/combine", ids="x").status_code == 400 and post(me, "/my/mesh/channels/combine", ids=",".join(["default"] * 9)).status_code == 400)
check("the page offers the one-code form with every channel to tick", 'id="oc-form"' in me.get(B + "/my/mesh/channels").text and "CodeOne" in me.get(B + "/my/mesh/channels").text.split('id="oc-form"')[1])
psql("DELETE FROM mesh_channels WHERE name IN ('CodeOne', 'CodeTwo')")
st = adm.get(B + "/admin/settings/radio").text
check("every tunable is in Settings > Mesh Nodes", all(x in st for x in ("Mesh Channels", "Mesh Channels: Channels per Member", "Mesh Channels: People per Channel")))
psql("INSERT INTO settings (key, value) VALUES ('mesh_channels_enabled','0') ON CONFLICT (key) DO UPDATE SET value='0'")
check("switched off, the page is gone", me.get(B + "/my/mesh/channels").status_code == 404)
psql("DELETE FROM settings WHERE key IN ('mesh_channels_per_member','mesh_channel_max_members','mesh_channels_enabled')")
check("the new tables are in the mesh restore area", all(t in open("../app/restorepart.py", encoding="utf-8").read().split('("mesh",')[1].split("\n")[0] for t in ("mesh_channels", "mesh_channel_members")))
print("ALL PASS" if ok_all else "SOME FAILED")
