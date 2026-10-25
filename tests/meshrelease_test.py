"""Keeping up with the Meshtastic project: the site reads the newest stable firmware and protobuf releases (here from a stand-in for GitHub), shows the firmware, takes the first protobuf release as the
reviewed baseline, and when the protobufs change tells the administrators (an audit entry and an Overview item) until someone marks the decoders reviewed; nothing is asked when the check is switched off; an
unreachable GitHub keeps the last answer. Needs the mesh service for Mesh Health (sh tests/deploy_mesh.sh) but the checks themselves do not."""
import json, os, re, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
PORT = 18996
def ssh(cmd):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker {cmd}"], capture_output=True, text=True).stdout
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
def releases(firmware="-", protobufs="-"):
    ssh(f"exec adsb-portal python -c \"import urllib.request;urllib.request.urlopen('http://127.0.0.1:{PORT}/set/{firmware}/{protobufs}',timeout=3)\"")
subprocess.run(["scp", "-q", "fake_releases.py", "nastemp:adsb-portal/tests/fake_releases.py"], check=True)
ssh("cp ~/adsb-portal/tests/fake_releases.py adsb-portal:/tmp/fake_releases.py")
ssh(f"exec adsb-portal python -c \"import urllib.request;urllib.request.urlopen('http://127.0.0.1:{PORT}/quit',timeout=3)\"")
ssh(f"exec -d adsb-portal python /tmp/fake_releases.py {PORT}")
time.sleep(2)
releases(firmware="v2.5.9.abc", protobufs="v2.5.9")
psql("DELETE FROM settings WHERE key IN ('mesh_fw_latest','mesh_fw_date','mesh_pb_latest','mesh_pb_reviewed','mesh_release_checked')")
setting("mesh_enabled", "1"); setting("mesh_release_check", "1"); setting("mesh_release_api", f"http://127.0.0.1:{PORT}")
adm = sess("marty", PW)
page = adm.get(B + "/admin/mesh").text
check("Admin > Mesh Nodes has a Meshtastic Releases section that says nothing is read yet", 'id="sec-releases"' in page and "not read yet" in page)
ok, m, _ = act(adm, "/admin/mesh/releases/check", "/admin/mesh")
check("Check Now reads the newest firmware and protobufs", ok and "v2.5.9.abc" in m and "v2.5.9" in m, m)
page = adm.get(B + "/admin/mesh").text
check("they are shown with the date, and the first protobuf release is the reviewed baseline (nothing to review)", "v2.5.9.abc" in page and "2026-10-01" in page and ">reviewed<" in page and "to review" not in page)
check("the Overview has no item yet", "protobufs" not in adm.get(B + "/admin/").text.split("Attention")[-1][:3000] or "Review them and mark" not in adm.get(B + "/admin/").text)
releases(firmware="v2.5.10.def", protobufs="v2.5.10")
ok, m, _ = act(adm, "/admin/mesh/releases/check", "/admin/mesh")
page = adm.get(B + "/admin/mesh").text
check("a new protobuf release is flagged to review, and the new firmware shown", "v2.5.10.def" in page and "to review" in page and "Mark Reviewed" in page and "v2.5.9" in page)
check("the audit log has it", "new Meshtastic protobuf release v2.5.10" in psql("SELECT string_agg(detail, '|') FROM audit_log WHERE action='mesh.release'"))
ov = adm.get(B + "/admin/").text
check("the Admin Overview says to review", "Meshtastic published protobufs v2.5.10" in ov and "decoders were last reviewed against v2.5.9" in ov)
ok, m, _ = act(adm, "/admin/mesh/releases/review", "/admin/mesh")
check("Mark Reviewed clears it", ok and "to review" not in adm.get(B + "/admin/mesh").text and "Meshtastic published protobufs" not in adm.get(B + "/admin/").text and psql("SELECT value FROM settings WHERE key='mesh_pb_reviewed'") == "v2.5.10")
os.system("")
releases()   # the stand-in now knows nothing: it answers 404 for both
ok, m, _ = act(adm, "/admin/mesh/releases/check", "/admin/mesh")
check("when GitHub cannot be read the last answer is kept and it says so", not ok and "did not answer" in m and "v2.5.10.def" in adm.get(B + "/admin/mesh").text, m)
health = requests.Session().get(B + "/mesh/health").text
check("Mesh Health shows the project's newest firmware next to the one seen", "the project's newest: v2.5.10.def" in health or "project&#39;s newest: v2.5.10.def" in health, health[health.find("Newest Meshtastic"):health.find("Newest Meshtastic") + 200])
st = adm.get(B + "/admin/settings/radio").text
check("the choices are settings", all(x in st for x in ("Check for Meshtastic Releases", "Check for Meshtastic Releases Every", "Meshtastic Releases Address")))
check("a member cannot check or review", requests.Session().post(B + "/admin/mesh/releases/check", allow_redirects=False).status_code in (302, 400, 401, 403))
ssh(f"exec adsb-portal python -c \"import urllib.request;urllib.request.urlopen('http://127.0.0.1:{PORT}/quit',timeout=3)\"")
psql("DELETE FROM settings WHERE key IN ('mesh_fw_latest','mesh_fw_date','mesh_pb_latest','mesh_pb_reviewed','mesh_release_checked','mesh_release_api')")
print("ALL PASS" if ok_all else "SOME FAILED")
