"""End-to-end check of the off-site backup service on the test box (not in run_all: it needs docker and sshd on the box).
Builds adsb-backup, runs it against the test portal with a fake /opt/adsb tree, and uses the box's own sshd as the SFTP destination."""
import json, re, subprocess, sys, time, requests, os
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def ssh(cmd, timeout=600):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=timeout)
def sd(args, timeout=600): return ssh(f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker {args}", timeout)
def peek(rel, phrase):
    """List a copy's contents by decrypting inside the backup image (the box's own shell has no zstd)."""
    cmd = f"openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass pass:'{phrase}' -in /b/{rel} | zstd -d -q | tar -t 2>&1"
    return sd(f"run --rm --entrypoint sh -v $HOME/sftpdata/backup-e2e:/b:ro adsb-backup:test -c \"{cmd}\"").stdout.split()
HERE = os.path.dirname(os.path.abspath(__file__)); DEP = os.path.join(HERE, "..", "deploy", "backup")
subprocess.run(["scp", "-q", os.path.join(DEP, "Dockerfile.backup"), os.path.join(DEP, "backupd.py"), "nastemp:bkbuild/"] , capture_output=True)
ssh("mkdir -p ~/bkbuild"); subprocess.run(["scp", "-q", os.path.join(DEP, "Dockerfile.backup"), os.path.join(DEP, "backupd.py"), "nastemp:bkbuild/"], check=True, capture_output=True)
b = sd("build -q -t adsb-backup:test -f bkbuild/Dockerfile.backup bkbuild", 900)
check("the backup image builds", b.returncode == 0, b.stderr[-300:])
ssh("rm -rf ~/bkbase ~/sftpdata/backup-e2e; mkdir -p ~/bkbase/portal/backups ~/bkbase/grafana/grafana-data ~/bkbase/grafana/prometheus-data ~/bkbase/hub/globe_history/2026-10-05 ~/bkbase/stack ~/bkbase/backup/state; "
    "echo SECRET_KEY=abc > ~/bkbase/portal/.env; echo GRAFANA_ADMIN_PASSWORD=x > ~/bkbase/grafana/.env; echo STACK_TOKEN=testtoken123 > ~/bkbase/stack/.env; "
    "echo graf > ~/bkbase/grafana/grafana-data/grafana.db; echo prom > ~/bkbase/grafana/prometheus-data/chunk; echo trace > ~/bkbase/hub/globe_history/2026-10-05/trace.json; echo 'services: {}' > ~/bkbase/hub/compose.yaml; mkdir -p ~/bkstate")
sd("rm -f bk-e2e bk-sftp")
ssh("rm -rf ~/sftpdata; mkdir -p ~/sftpdata && chmod 777 ~/sftpdata")
sd("run -d --name bk-sftp -p 2222:2222 -e PUID=1000 -e PGID=1000 -e PASSWORD_ACCESS=true -e USER_PASSWORD=e2epw -e USER_NAME=bk -v $HOME/sftpdata:/config lscr.io/linuxserver/openssh-server:latest", 600)
time.sleep(25)
r = sd("run -d --name bk-e2e -e PORTAL_URL=http://test-box.invalid:18430 -e STACK_TOKEN=testtoken123 -e BACKUP_POLL=4 -v $HOME/bkbase:/opt/adsb:ro -v adsb-portal_portal-backups:/opt/adsb/portal/backups:ro -v $HOME/bkstate:/state adsb-backup:test")
check("the service starts", r.returncode == 0, r.stderr[-200:])

adm = sess("marty", PW)
def post(endpoint, data):
    data = dict(data); data["csrf_token"] = tok(adm, "/admin/backups")
    j = adm.post(B + endpoint, data=data, headers={"X-Requested-With": "XMLHttpRequest"}).json(); return j["ok"], j["message"]
H = {"Authorization": "Bearer testtoken123"}
base = dict(type="sftp", host="test-box.invalid", port="2222", user="bk", path="backup-e2e", secret="e2epw", passphrase="e2e passphrase", small_time="23:59", small_every_days="1", small_keep="3",
            big_time="23:59", big_every_days="7", big_keep="2", big_prometheus="1", big_globe="1")
subprocess.run(["ssh", "-q", "nastemp", "true"])
def sql(q): return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{q}\""], capture_output=True, text=True).stdout.strip()
sql("DELETE FROM offsite_runs"); sql("DELETE FROM settings WHERE key IN ('offsite_command','offsite_test')")
check("settings save", post("/admin/backups/offsite/save", base)[0])
def wait(pred, secs=150):
    t0 = time.time()
    while time.time() - t0 < secs:
        v = pred()
        if v: return v
        time.sleep(4)
    return None
post("/admin/backups/offsite/command", {"action": "test"})
tr = wait(lambda: (json.loads(sql("SELECT value FROM settings WHERE key='offsite_test'") or "null")))
check("the connection test reaches the destination over SFTP and passes", tr and tr["ok"], tr)
post("/admin/backups/offsite/command", {"action": "run-small"})
run = wait(lambda: sql("SELECT ok||'|'||bytes||'|'||remote_name FROM offsite_runs WHERE kind='small' AND ok IS NOT NULL ORDER BY id DESC LIMIT 1"))
check("a small copy runs and reports success with a size", run and run.startswith("true|") and int(run.split("|")[1]) > 100, run)
files = ssh("ls ~/sftpdata/backup-e2e/small/").stdout.split()
check("the encrypted file is at the destination", len(files) == 1 and files[0].startswith("adsb-small-") and files[0].endswith(".tar.zst.enc"), files)
lst = peek("small/" + files[0], "e2e passphrase")
check("decrypted, it holds the database backup, the secret keys, compose files and Grafana data", any(x.startswith("portal/backups/portal-backup-") for x in lst) and "portal/.env" in lst and "stack/.env" in lst and "hub/compose.yaml" in lst and any("grafana.db" in x for x in lst), lst[:12])
check("the big data is NOT in the small copy", not any("globe_history" in x or "prometheus-data" in x for x in lst))
check("a wrong passphrase cannot open it", not any(x.startswith("portal/") for x in peek("small/" + files[0], "wrong")))
post("/admin/backups/offsite/command", {"action": "run-big"})
run = wait(lambda: sql("SELECT ok||'|'||bytes FROM offsite_runs WHERE kind='big' AND ok IS NOT NULL ORDER BY id DESC LIMIT 1"))
check("a big copy runs on its own", run and run.startswith("true|"), run)
bf = ssh("ls ~/sftpdata/backup-e2e/big/").stdout.split()
bl = peek("big/" + bf[0], "e2e passphrase")
check("the big copy holds the statistics and traffic history and nothing else", any("prometheus-data" in x for x in bl) and any("globe_history" in x for x in bl) and "portal/.env" not in bl, bl[:10])
check("the known host key was pinned on first use", "test-box.invalid" in ssh("cat ~/bkstate/known_hosts").stdout)
base2 = dict(base, small_keep="1")
post("/admin/backups/offsite/save", base2)
for i in range(2):
    n0 = int(sql("SELECT count(*) FROM offsite_runs WHERE kind='small' AND ok"))
    time.sleep(1.2); post("/admin/backups/offsite/command", {"action": "run-small"})
    wait(lambda: int(sql("SELECT count(*) FROM offsite_runs WHERE kind='small' AND ok")) > n0)
time.sleep(2)
check("old copies beyond the number to keep are removed", len(ssh("ls ~/sftpdata/backup-e2e/small/").stdout.split()) == 1, ssh("ls ~/sftpdata/backup-e2e/small/").stdout)
post("/admin/backups/offsite/save", dict(base2, secret="not the password"))
sql("DELETE FROM settings WHERE key='offsite_test'")
post("/admin/backups/offsite/command", {"action": "test"})
tr = wait(lambda: (json.loads(sql("SELECT value FROM settings WHERE key='offsite_test'") or "null")))
check("a wrong destination password is reported clearly", tr and not tr["ok"] and "not the password" not in json.dumps(tr), tr)
logs = sd("logs bk-e2e").stdout + sd("logs bk-e2e").stderr
check("the service log never shows the passwords", "e2epw" not in logs and "e2e passphrase" not in logs)
post("/admin/backups/offsite/save", base2)
sd("rm -f bk-e2e bk-sftp"); sql("DELETE FROM offsite_runs"); sql("DELETE FROM settings WHERE key IN ('offsite_config','offsite_secret','offsite_key','offsite_pass','offsite_command','offsite_test')")
print("ALL PASS" if ok_all else "SOME FAILED")
