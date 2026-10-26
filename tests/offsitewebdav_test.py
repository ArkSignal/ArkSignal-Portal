"""End to end: the off-site copy to a WebDAV server over HTTPS, the way it will run against a NAS behind a reverse proxy.
Needs docker on the test box (ssh alias nastemp). Builds the backup image, makes a private certificate authority, starts `rclone serve webdav` with basic auth and a
certificate for the box's address, and drives everything through the admin pages. Proves: a certificate that is not trusted is refused with a plain message, a trusted
one works, a Host written as a full URL with a sub-folder and trailing slash works, the files arrive encrypted (no plaintext), a big file streams without being held
in memory, old copies are pruned to the keep count, the restore helper lists, downloads, decrypts and unpacks, wrong passwords and read-only folders are explained,
plain http is refused, and no secret reaches the service log."""
import json, os, re, subprocess, sys, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def ssh(cmd, timeout=600, inp=None):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=timeout, input=inp)
def sd(args, timeout=600, inp=None): return ssh(f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker {args}", timeout, inp)
def root(cmd, timeout=120): return ssh(f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' sh -c \"{cmd}\"", timeout)
HERE = os.path.dirname(os.path.abspath(__file__)); DEP = os.path.join(HERE, "..", "deploy", "backup")
BOX = "test-box.invalid"
URL = f"https://{BOX}:8443/webdav/nested/"   # a full URL with a sub-folder and a trailing slash, as typed on the page
DAV = "$HOME/wddata"

# ---- the image and a private certificate authority
ssh("mkdir -p ~/bkbuild"); subprocess.run(["scp", "-q", os.path.join(DEP, "Dockerfile.backup"), os.path.join(DEP, "backupd.py"), "nastemp:bkbuild/"], check=True, capture_output=True)
b = sd("build -q -t adsb-backup:test -f bkbuild/Dockerfile.backup bkbuild", 900)
check("the backup image builds", b.returncode == 0, b.stderr[-300:])
sd("rm -f bkw-svc bkw-dav bkw-ro"); root("rm -rf $HOME/wddata $HOME/wdcerts $HOME/wdro $HOME/bkwbase $HOME/bkwstate $HOME/bkwrestore")
ssh("mkdir -p ~/wdcerts ~/wddata ~/wdro/webdav ~/bkwstate ~/bkwrestore")
script = f"""set -e
cd /c
openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.pem -subj /CN=e2e-ca -days 2 -addext basicConstraints=critical,CA:TRUE -addext keyUsage=keyCertSign 2>/dev/null
openssl req -newkey rsa:2048 -nodes -keyout s.key -out s.csr -subj /CN={BOX} 2>/dev/null
printf 'subjectAltName=IP:{BOX}\\nbasicConstraints=CA:FALSE\\n' > ext
openssl x509 -req -in s.csr -CA ca.pem -CAkey ca.key -CAcreateserial -out s.pem -days 2 -extfile ext 2>/dev/null
chmod 644 *
"""
ssh("tr -d '" + chr(92) + "015' > ~/wd.sh", inp=script)
g = sd("run --rm --entrypoint sh -v $HOME/wdcerts:/c -v $HOME/wd.sh:/wd.sh adsb-backup:test /wd.sh")
check("a private certificate authority and a server certificate are made", g.returncode == 0 and "s.pem" in ssh("ls ~/wdcerts").stdout, g.stderr[-300:])
serve = ("run -d --name {n} -p {p}:{p} -v $HOME/wdcerts:/c:ro -v {d}:/data --entrypoint rclone adsb-backup:test serve webdav /data --addr :{p} --baseurl /webdav "
         "--dir-cache-time 1s --user dav --pass davpw --cert /c/s.pem --key /c/s.key {x}")
r = sd(serve.format(n="bkw-dav", p=8443, d=DAV, x=""))
check("the WebDAV server starts (https, basic auth)", r.returncode == 0, r.stderr[-200:])
r = sd(serve.format(n="bkw-ro", p=8444, d="$HOME/wdro", x="--read-only"))
time.sleep(4)
check("the WebDAV server answers over https and asks for a password", ssh(f"curl -s -o /dev/null -w '%{{http_code}}' --cacert ~/wdcerts/ca.pem https://{BOX}:8443/webdav/").stdout.strip() == "401")

# ---- a fake hub data tree, including a big incompressible file
ssh("rm -rf ~/bkwbase; mkdir -p ~/bkwbase/portal/backups ~/bkwbase/grafana/grafana-data ~/bkwbase/grafana/prometheus-data ~/bkwbase/hub/globe_history/2026-10-05 ~/bkwbase/stack; "
    "echo SECRET_KEY=plaintextmarker > ~/bkwbase/portal/.env; echo STACK_TOKEN=testtoken123 > ~/bkwbase/stack/.env; echo graf > ~/bkwbase/grafana/grafana-data/grafana.db; "
    "echo prom > ~/bkwbase/grafana/prometheus-data/chunk; echo trace > ~/bkwbase/hub/globe_history/2026-10-05/trace.json; echo 'services: {}' > ~/bkwbase/hub/compose.yaml; "
    "dd if=/dev/urandom of=~/bkwbase/hub/globe_history/big.bin bs=1M count=300 2>/dev/null")

def start_service(trust):
    sd("rm -f bkw-svc")
    ca = "-e SSL_CERT_FILE=/c/ca.pem -v $HOME/wdcerts:/c:ro " if trust else ""
    return sd(f"run -d --name bkw-svc {ca}-e PORTAL_URL=http://{BOX}:18430 -e STACK_TOKEN=testtoken123 -e BACKUP_POLL=4 -v $HOME/bkwbase:/opt/adsb:ro "
              f"-v adsb-portal_portal-backups:/opt/adsb/portal/backups:ro -v $HOME/bkwstate:/state adsb-backup:test")
r = start_service(False)
check("the backup service starts without trusting the private authority", r.returncode == 0, r.stderr[-200:])

adm = sess("marty", PW)
def post(endpoint, data):
    data = dict(data); data["csrf_token"] = tok(adm, "/admin/backups")
    j = adm.post(B + endpoint, data=data, headers={"X-Requested-With": "XMLHttpRequest"}).json(); return j["ok"], j["message"]
def sql(q): return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{q}\""], capture_output=True, text=True).stdout.strip()
def wait(pred, secs=240):
    t0 = time.time()
    while time.time() - t0 < secs:
        v = pred()
        if v: return v
        time.sleep(4)
    return None
def test_conn():
    sql("DELETE FROM settings WHERE key='offsite_test'")
    post("/admin/backups/offsite/command", {"action": "test"})
    return wait(lambda: json.loads(sql("SELECT value FROM settings WHERE key='offsite_test'") or "null"))
base = dict(type="webdav", host=URL, user="dav", path="adsb-backup-e2e", secret="davpw", passphrase="e2e passphrase", small_time="23:59", small_every_days="1", small_keep="3",
            big_time="23:59", big_every_days="7", big_keep="2", big_prometheus="1", big_globe="1")
sql("DELETE FROM offsite_runs"); sql("DELETE FROM settings WHERE key IN ('offsite_command','offsite_test')")

ok, msg = post("/admin/backups/offsite/save", dict(base, host="http://" + BOX + ":8443/webdav/"))
check("a plain http address is refused by the page", not ok and "https" in msg, msg)
ok, msg = post("/admin/backups/offsite/save", base)
check("the settings save with a full https address, sub-folder and trailing slash", ok, msg)
page = adm.get(B + "/admin/backups").text
check("the page never shows the passwords", "davpw" not in page and "e2e passphrase" not in page)
check("the page says no copy has succeeded yet", "Last successful small copy: <b>never</b>" in page, "")

tr = test_conn()
check("a certificate that is not trusted is refused with a plain explanation", tr and not tr["ok"] and "certificate" in tr["detail"].lower() and "davpw" not in json.dumps(tr), tr)

start_service(True)
tr = test_conn()
check("a web address whose own folder does not exist is explained (409), not a raw error", tr and not tr["ok"] and "409" in tr["detail"] and "must already exist" in tr["detail"], tr)
root("mkdir -p $HOME/wddata/nested"); time.sleep(3)
tr = test_conn()
check("with a trusted certificate the connection test passes (creates the folders, writes, reads back, deletes)", tr and tr["ok"], tr)
check("the folders were made level by level under the sub-folder", "adsb-backup-e2e" in root("ls $HOME/wddata/nested").stdout and not root("ls $HOME/wddata/nested/adsb-backup-e2e/.write-test").stdout.strip(), root("ls -R $HOME/wddata").stdout[:300])

# ---- a small copy: arrives encrypted, opens with the passphrase only
post("/admin/backups/offsite/command", {"action": "run-small"})
run = wait(lambda: sql("SELECT ok||'|'||bytes||'|'||remote_name FROM offsite_runs WHERE kind='small' AND ok IS NOT NULL ORDER BY id DESC LIMIT 1"))
check("a small copy runs and reports success", run and run.startswith("true|") and int(run.split("|")[1]) > 100, run)
sdir = "$HOME/wddata/nested/adsb-backup-e2e/small"
files = root(f"ls {sdir}").stdout.split()
check("the encrypted file is at the destination", len(files) == 1 and files[0].startswith("adsb-small-") and files[0].endswith(".tar.zst.enc"), files)
head = root(f"head -c 8 {sdir}/{files[0]}").stdout if files else ""
check("the stored file is an OpenSSL encrypted file", head.startswith("Salted__"), repr(head))
check("no plaintext is stored at the destination", root(f"grep -rl plaintextmarker $HOME/wddata | wc -l").stdout.strip() == "0")
def peek(rel, phrase):
    cmd = f"openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass pass:'{phrase}' -in /b/{rel} | zstd -d -q | tar -t 2>&1"
    return sd(f"run --rm --entrypoint sh -v $HOME/wddata/nested/adsb-backup-e2e:/b:ro adsb-backup:test -c \"{cmd}\"").stdout.split()
lst = peek("small/" + files[0], "e2e passphrase")
check("decrypted, it holds the secret keys, database backup folder and compose files", "portal/.env" in lst and "stack/.env" in lst and "hub/compose.yaml" in lst, lst[:10])
check("a wrong passphrase cannot open it", not any(x.startswith("portal/") for x in peek("small/" + files[0], "wrong")))
page = adm.get(B + "/admin/backups").text
check("the page now shows the time of the last successful small copy", "Last successful small copy: <b>never</b>" not in page and "last successful big copy: <b>never</b>" in page)

# ---- the big copy: a large file streams through without being held in memory
post("/admin/backups/offsite/command", {"action": "run-big"})
peak = 0; t0 = time.time(); run = None
while time.time() - t0 < 600 and not run:
    m = re.match(r"([\d.]+)(\w+)", sd("stats --no-stream --format '{{.MemUsage}}' bkw-svc").stdout.strip().strip("'"))
    if m:
        peak = max(peak, float(m.group(1)) * {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024, "B": 1 / 1048576}.get(m.group(2), 1))
    run = sql("SELECT ok||'|'||bytes FROM offsite_runs WHERE kind='big' AND ok IS NOT NULL ORDER BY id DESC LIMIT 1")
    time.sleep(2)
check("a big copy of a 300 MB file runs and reports its size", run and run.startswith("true|") and int(run.split("|")[1]) > 290_000_000, run)
check("it was streamed: the service never held the file in memory (peak under 150 MB)", 0 < peak < 150, f"peak {peak:.0f} MB")
bf = root("ls $HOME/wddata/nested/adsb-backup-e2e/big").stdout.split()
bl = peek("big/" + bf[0], "e2e passphrase") if bf else []
check("the big copy holds the statistics and traffic history and nothing else", any("prometheus-data" in x for x in bl) and any("big.bin" in x for x in bl) and "portal/.env" not in bl, bl[:8])

# ---- pruning to the keep count
post("/admin/backups/offsite/save", dict(base, small_keep="1"))
for i in range(2):
    n0 = int(sql("SELECT count(*) FROM offsite_runs WHERE kind='small' AND ok"))
    time.sleep(1.2); post("/admin/backups/offsite/command", {"action": "run-small"})
    wait(lambda: int(sql("SELECT count(*) FROM offsite_runs WHERE kind='small' AND ok")) > n0)
time.sleep(2)
left = root(f"ls {sdir}").stdout.split()
check("old copies beyond the number to keep are removed from the destination", len(left) == 1 and left[0] > files[0], left)

# ---- the restore helper lists, downloads, decrypts and unpacks
stdin = "\n".join(["webdav", URL, "", "dav", "adsb-backup-e2e", "davpw", "1", "e2e passphrase", ""]) + "\n"
ssh("tr -d '" + chr(92) + "015' > ~/rs.in", inp=stdin)
rs = root("/usr/local/bin/docker run --rm -i -e SSL_CERT_FILE=/c/ca.pem -v $HOME/wdcerts:/c:ro -v $HOME/bkwrestore:/opt/adsb adsb-backup:test restore < $HOME/rs.in", 300)
unp = root("find $HOME/bkwrestore -name .env | head").stdout
check("the restore helper unpacks the newest copy into a new folder", "Unpacked into" in rs.stdout and "portal/.env" in unp, rs.stdout[-300:] + rs.stderr[-200:])
check("the restored file has the original content", "plaintextmarker" in root("cat $HOME/bkwrestore/restore-*/portal/.env").stdout)

# ---- explained failures
post("/admin/backups/offsite/save", dict(base, secret="not the password"))
tr = test_conn()
check("a wrong password is explained and never echoed", tr and not tr["ok"] and "password" in tr["detail"].lower() and "not the password" not in json.dumps(tr), tr)
post("/admin/backups/offsite/save", dict(base, host=f"https://{BOX}:8444/webdav/"))
tr = test_conn()
check("a read-only folder is explained in plain words", tr and not tr["ok"] and re.search(r"folder|allowed|permission|read-only|403|405", tr["detail"]) and "exit code" not in tr["detail"], tr)
logs = sd("logs bkw-svc"); logs = logs.stdout + logs.stderr
check("the service log never shows the passwords", "davpw" not in logs and "e2e passphrase" not in logs and "not the password" not in logs)

# ---- the service itself refuses http, whatever the portal holds
sys.path.insert(0, DEP)
import backupd
try:
    backupd.Dest({"type": "webdav", "host": "http://example.org/dav", "user": "u", "path": "x", "port": ""}, {"secret": "s"}); refused = False
except ValueError:
    refused = True
check("the backup service itself refuses a plain http WebDAV address", refused)

post("/admin/backups/offsite/save", base)
sd("rm -f bkw-svc bkw-dav bkw-ro"); root("rm -rf $HOME/wddata $HOME/wdcerts $HOME/wdro $HOME/bkwbase $HOME/bkwrestore $HOME/wd.sh")
sql("DELETE FROM offsite_runs"); sql("DELETE FROM settings WHERE key IN ('offsite_config','offsite_secret','offsite_key','offsite_pass','offsite_command','offsite_test')")
print("ALL PASS" if ok_all else "SOME FAILED"); sys.exit(0 if ok_all else 1)
