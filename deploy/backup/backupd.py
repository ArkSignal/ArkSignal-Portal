"""adsb-backup: makes the off-site copies the portal asks for.

Every 30 seconds it asks the portal (internal network, bearer token) for its orders: the destination and its secrets, which copies are due, and any
"test" or "make a copy now" request from the Backups page. A copy is one encrypted file streamed straight to the destination (tar | zstd | openssl | rclone),
with no temporary copy on the hub's disk; afterwards copies beyond the number to keep are deleted from the destination. `python3 backupd.py restore` is the
interactive helper that lists, downloads, decrypts and unpacks copies.
"""
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone

PORTAL = os.environ.get("PORTAL_URL", "http://adsb-portal:8430").rstrip("/")
TOKEN = os.environ.get("STACK_TOKEN", "")
BASE = os.environ.get("BACKUP_BASE", "/opt/adsb")
STATE = os.environ.get("BACKUP_STATE", "/state")
POLL = int(os.environ.get("BACKUP_POLL", "30"))


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def api(path, body=None, timeout=120):
    req = urllib.request.Request(PORTAL + path, data=json.dumps(body).encode() if body is not None else None, method="POST" if body is not None else "GET",
                                 headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# ---------------------------------------------------------------- the destination

class Dest:
    """An rclone remote built from the portal's settings. Passwords reach rclone through the environment, never the command line."""

    def __init__(self, conf, secrets):
        self.conf, self.secrets, self.env, self.tmp = conf, secrets, {}, None
        t = conf["type"]
        e = {"RCLONE_CONFIG_DEST_TYPE": t}
        obscured = lambda v: subprocess.run(["rclone", "obscure", v], capture_output=True, text=True).stdout.strip()
        path = conf["path"].strip("/")
        if t == "sftp":
            e["RCLONE_CONFIG_DEST_HOST"] = conf["host"]
            e["RCLONE_CONFIG_DEST_USER"] = conf["user"]
            if conf["port"]:
                e["RCLONE_CONFIG_DEST_PORT"] = conf["port"]
            if secrets["key"]:
                self.tmp = tempfile.NamedTemporaryFile("w", delete=False, dir="/tmp")
                self.tmp.write(secrets["key"].rstrip() + "\n"); self.tmp.close(); os.chmod(self.tmp.name, 0o600)
                e["RCLONE_CONFIG_DEST_KEY_FILE"] = self.tmp.name
            if secrets["secret"]:
                e["RCLONE_CONFIG_DEST_PASS"] = obscured(secrets["secret"])
            kh = self._known_hosts(conf["host"], conf["port"] or "22")
            if kh:
                e["RCLONE_CONFIG_DEST_KNOWN_HOSTS_FILE"] = kh
            self.remote = "DEST:" + path
        elif t == "smb":
            e["RCLONE_CONFIG_DEST_HOST"] = conf["host"]
            e["RCLONE_CONFIG_DEST_USER"] = conf["user"]
            e["RCLONE_CONFIG_DEST_PASS"] = obscured(secrets["secret"])
            if conf["port"]:
                e["RCLONE_CONFIG_DEST_PORT"] = conf["port"]
            self.remote = "DEST:" + path
        elif t == "webdav":
            url = (conf["host"] if "://" in conf["host"] else "https://" + conf["host"]).strip().rstrip("/")
            if not url.lower().startswith("https://"):
                raise ValueError("The WebDAV address must start with https://: the password and the copies are never sent over plain http.")
            e["RCLONE_CONFIG_DEST_URL"] = url
            e["RCLONE_NO_CHECK_CERTIFICATE"] = "false"   # the destination's certificate is always verified
            e["RCLONE_CONFIG_DEST_VENDOR"] = "other"
            e["RCLONE_CONFIG_DEST_USER"] = conf["user"]
            e["RCLONE_CONFIG_DEST_PASS"] = obscured(secrets["secret"])
            self.remote = "DEST:" + path
        elif t == "s3":
            ep = conf["endpoint"] or conf["host"]
            e["RCLONE_CONFIG_DEST_PROVIDER"] = "Other" if ep else "AWS"
            if ep:
                e["RCLONE_CONFIG_DEST_ENDPOINT"] = ep
            if conf["region"]:
                e["RCLONE_CONFIG_DEST_REGION"] = conf["region"]
            e["RCLONE_CONFIG_DEST_ACCESS_KEY_ID"] = conf["access_key"]
            e["RCLONE_CONFIG_DEST_SECRET_ACCESS_KEY"] = secrets["secret"]
            self.remote = "DEST:" + conf["bucket"] + ("/" + path if path else "")
        else:
            raise ValueError("unknown destination type")
        e["RCLONE_LOG_LEVEL"] = "ERROR"   # no "config file not found" notice in the messages
        e["RCLONE_CONTIMEOUT"] = "20s"   # a dead destination fails in seconds, not hours
        self.env = e

    def _known_hosts(self, host, port):
        """Pin the destination's SSH host key the first time we see it (trust on first use); later connections must present the same key."""
        os.makedirs(STATE, exist_ok=True)
        f = os.path.join(STATE, "known_hosts")
        marker = f"[{host}]:{port}" if port != "22" else host
        try:
            if marker in open(f).read():
                return f
        except OSError:
            pass
        r = subprocess.run(["ssh-keyscan", "-T", "10", "-p", port, host], capture_output=True, text=True)
        if r.stdout.strip():
            with open(f, "a") as fh:
                fh.write(r.stdout)
            log(f"pinned the SSH host key of {host}:{port}")
            return f
        return None

    def run(self, *args, stdin=None, timeout=3600):
        env = dict(os.environ, **self.env)
        return subprocess.run(["rclone", *args], env=env, capture_output=True, text=True, input=stdin, timeout=timeout)

    def cleanup(self):
        if self.tmp:
            try:
                os.unlink(self.tmp.name)
            except OSError:
                pass


HINTS = (
    (r"x509|certificate", "The destination's certificate was not accepted (expired, wrong name, or not issued by a trusted authority). Copies are never sent without a valid certificate."),
    (r"(?<!\d)401(?!\d)|Unauthorized", "The destination refused the user name or password (401)."),
    (r"(?<!\d)403(?!\d)|Forbidden", "The user may log in but is not allowed to write in that folder (403): give it read/write permission on the shared folder."),
    (r"(?<!\d)404(?!\d)|Not Found", "The web address or folder was not found (404): check the address, including the WebDAV part of it."),
    (r"(?<!\d)405(?!\d)|Method Not Allowed", "The server does not allow creating folders or files there (405): the address probably points at the wrong place, or the folder is read-only."),
    (r"(?<!\d)409(?!\d)|Conflict", "The destination could not create a folder (409): the folder named in the web address (for example the share) must already exist, and the user must be allowed to write in it. Create it on the NAS first."),
    (r"(?<!\d)412(?!\d)|Precondition Failed", "The destination refused the upload because the file already exists or changed (412)."),
    (r"(?<!\d)413(?!\d)|Request Entity Too Large|Payload Too Large", "The destination, or a reverse proxy in front of it, refused the file as too large (413): raise its upload size limit (nginx: client_max_body_size 0)."),
    (r"(?<!\d)507(?!\d)|Insufficient Storage|no space left|quota", "The destination is out of space or over its quota (507)."),
    (r"(?<!\d)50[234](?!\d)|Bad Gateway|Gateway Time|Service Unavailable", "The destination (or the reverse proxy in front of it) is not answering properly (502/503/504)."),
    (r"no such host|lookup .*server misbehaving", "The destination's name could not be found: check the spelling and the hub's DNS."),
    (r"connection refused|i/o timeout|no route to host|context deadline", "Could not reach the destination: check the address, the port and any firewall."),
)


def _err(proc):
    t = (proc.stderr or proc.stdout or "").strip().splitlines()
    raw = " | ".join(t[-3:])[:500] or f"exit code {proc.returncode}"
    for pat, msg in HINTS:
        if re.search(pat, raw, re.I):
            return msg
    return raw


def test(dest):
    """Write, read back and delete a small file in the destination's backup folder."""
    base = dest.remote
    p = dest.run("mkdir", base + "/small")
    if p.returncode:
        return False, "Could not create the folder: " + _err(p)
    p = dest.run("rcat", base + "/.write-test", stdin="adsb backup test " + str(time.time()))
    if p.returncode:
        return False, "Could not write a file: " + _err(p)
    p = dest.run("cat", base + "/.write-test")
    ok = p.returncode == 0 and p.stdout.startswith("adsb backup test")
    if p.returncode == 0 and not ok and dest.conf["type"] == "webdav":
        dest.run("deletefile", base + "/.write-test")
        return False, ("The destination took the test file but would not show it back. This usually means a reverse proxy in front of the NAS changes the path "
                       "(a prefix such as /webdav that is removed or added): the address must use exactly the path the NAS serves, for example https://name/ShareName.")
    dest.run("deletefile", base + "/.write-test")
    return (True, "Wrote, read back and deleted a test file in " + base.split(":", 1)[1]) if ok else (False, "Could not read the test file back: " + _err(p))


# ---------------------------------------------------------------- what goes in each copy

def small_paths(db_backup):
    paths = []
    if db_backup:
        paths.append(f"portal/backups/{db_backup}")
    for rel in ("portal/.env", "grafana/.env", "stack/.env", "planefence/config"):
        if os.path.exists(os.path.join(BASE, rel)):
            paths.append(rel)
    for d in sorted(os.listdir(BASE)) if os.path.isdir(BASE) else []:
        if os.path.exists(os.path.join(BASE, d, "compose.yaml")):
            paths.append(f"{d}/compose.yaml")
    if os.path.isdir(os.path.join(BASE, "grafana/grafana-data")):
        paths.append("grafana/grafana-data")
    if os.path.isfile(os.path.join(BASE, "matrix/dumps/matrix.dump")):   # the Matrix server: a consistent dump of its database, and the small files that make it who it is (signing key, secrets); the media is in the big copy
        paths.append("matrix/dumps")
    mdata = os.path.join(BASE, "matrix/data")
    if os.path.isdir(mdata):
        paths += [f"matrix/data/{n}" for n in sorted(os.listdir(mdata)) if os.path.isfile(os.path.join(mdata, n))]
    return paths


def big_paths(conf):
    paths = []
    if conf["big"]["prometheus"] and os.path.isdir(os.path.join(BASE, "grafana/prometheus-data")):
        paths.append("grafana/prometheus-data")
    if os.path.isdir(os.path.join(BASE, "matrix/data/media_store")):
        paths.append("matrix/data/media_store")
    if conf["big"]["globe"]:
        for rel in ("hub/globe_history", "hub/graphs1090"):
            if os.path.isdir(os.path.join(BASE, rel)):
                paths.append(rel)
    return paths


def make_copy(kind, conf, secrets, dest, manual=False):
    started = api("/internal/backup/start", {"kind": kind, "manual": manual}, timeout=3600)   # the portal makes a fresh database backup first: minutes on a big hub
    rid = started["run_id"]
    name = f"adsb-{kind}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.tar.zst.enc"
    try:
        paths = small_paths(started.get("db_backup")) if kind == "small" else big_paths(conf)
        if not paths:
            raise RuntimeError("there is nothing to copy: none of the data folders exist on this hub")
        target = f"{dest.remote}/{kind}/{name}"
        dest.run("mkdir", f"{dest.remote}/{kind}")
        tar = "tar --warning=no-file-changed -C " + shlex.quote(BASE) + " -cf - " + " ".join(shlex.quote(p) for p in paths)
        cmd = (f"set -o pipefail; {tar} | zstd -3 -T0 -q | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:BKPASS | "
               f"rclone rcat {shlex.quote(target)}")
        env = dict(os.environ, **dest.env, BKPASS=secrets["passphrase"])
        log(f"{kind} copy: {len(paths)} items to {dest.conf['type']}")
        p = subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, text=True, timeout=6 * 3600)
        # tar exits 1 when a file changed while it was read (live databases): the copy is still good, so only fail on the real errors
        if p.returncode not in (0, 1) and p.returncode != 141:
            raise RuntimeError(_err(p))
        info = dest.run("lsjson", target, "--files-only")
        size = (json.loads(info.stdout)[0]["Size"] if info.returncode == 0 and info.stdout.strip() not in ("", "[]") else 0)
        if size <= 0:
            raise RuntimeError("the file did not arrive at the destination")
        removed = prune(dest, kind, int(conf[kind]["keep"]))
        api("/internal/backup/report", {"run_id": rid, "ok": True, "bytes": size, "remote_name": f"{kind}/{name}", "detail": f"{len(paths)} items" + (f", removed {removed} old" if removed else "")})
        log(f"{kind} copy done: {name} ({size} bytes)")
    except Exception as e:
        log(f"{kind} copy FAILED: {e}")
        try:
            dest.run("deletefile", f"{dest.remote}/{kind}/{name}")   # never leave a half-written copy as the newest one
        except Exception:
            pass
        api("/internal/backup/report", {"run_id": rid, "ok": False, "detail": str(e)[:500], "remote_name": f"{kind}/{name}"})


def prune(dest, kind, keep):
    p = dest.run("lsf", f"{dest.remote}/{kind}", "--files-only")
    names = sorted(n for n in p.stdout.split() if re.fullmatch(rf"adsb-{kind}-\d{{8}}-\d{{6}}\.tar\.zst\.enc", n)) if p.returncode == 0 else []
    old = names[:-keep] if len(names) > keep else []
    for n in old:
        dest.run("deletefile", f"{dest.remote}/{kind}/{n}")
    return len(old)


# ---------------------------------------------------------------- the loop

def tick():
    cfgd = api("/internal/backup/config")
    conf, secrets = cfgd["config"], cfgd["secrets"]
    cmd, due = cfgd.get("command"), cfgd.get("due") or []
    if not (cmd or due) or not cfgd.get("ready"):
        return
    try:
        dest = Dest(conf, secrets)
    except ValueError as e:   # a setting the service refuses (for example a plain http address): say so on the page
        if cmd and cmd.get("action") == "test":
            api("/internal/backup/test-result", {"ok": False, "detail": str(e)})
        log("destination refused: " + str(e))
        return
    try:
        if cmd and cmd.get("action") == "test":
            ok, detail = test(dest)
            api("/internal/backup/test-result", {"ok": ok, "detail": detail})
            log("connection test: " + ("ok" if ok else "FAILED: " + detail))
            return
        jobs = []
        if cmd and cmd.get("action") in ("run-small", "run-big"):
            jobs.append((cmd["action"][4:], True))
        jobs += [(k, False) for k in due if not any(j[0] == k for j in jobs)]
        for kind, manual in jobs:
            make_copy(kind, conf, secrets, dest, manual)
    finally:
        dest.cleanup()


def restore():
    """Interactive: list the copies at a destination, download one, decrypt it and unpack it into a new folder (nothing is overwritten)."""
    import getpass
    print("Restore from an off-site copy. Nothing on this hub is changed: the copy is unpacked into a new folder.")
    conf = {"type": input("Type (sftp, s3, smb, webdav): ").strip(), "host": input("Host, endpoint or (WebDAV) full https address (blank for Amazon S3): ").strip(), "port": input("Port (blank for default): ").strip(),
            "user": input("User (blank for S3): ").strip(), "path": input("Folder (as in the portal): ").strip(), "endpoint": "", "region": "", "bucket": "", "access_key": ""}
    if conf["type"] == "s3":
        conf["bucket"] = input("Bucket: ").strip(); conf["access_key"] = input("Access key: ").strip(); conf["endpoint"] = conf["host"]
    secrets = {"secret": getpass.getpass("Password or secret key: "), "key": "", "passphrase": ""}
    dest = Dest(conf, secrets)
    files = []
    for kind in ("small", "big"):
        p = dest.run("lsf", f"{dest.remote}/{kind}", "--files-only")
        files += [f"{kind}/{n}" for n in sorted(p.stdout.split(), reverse=True)] if p.returncode == 0 else []
    if not files:
        print("No copies found there."); return
    for i, f in enumerate(files):
        print(f"  {i + 1:2}. {f}")
    f = files[int(input("Number to restore: ")) - 1]
    phrase = getpass.getpass("Encryption passphrase: ")
    out = os.path.join(BASE if os.access(BASE, os.W_OK) else "/tmp", "restore-" + re.sub(r"[^A-Za-z0-9]", "", f.split("/")[1][:30]))
    os.makedirs(out, exist_ok=True)
    src = f"rclone cat {shlex.quote(dest.remote + '/' + f)} | openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:BKPASS | zstd -d -q"
    env = dict(os.environ, **dest.env, BKPASS=phrase)
    # first pass: what is inside, so one part can be chosen instead of everything
    p = subprocess.run(["bash", "-c", f"set -o pipefail; {src} | tar -t"], env=env, capture_output=True, text=True)
    if p.returncode not in (0, 141) or not p.stdout.strip():
        print("Could not open the copy: the passphrase may be wrong or the copy damaged."); return
    items = sorted({"/".join(n.strip("/").split("/")[:2]) if n.split("/")[0] in ("portal", "grafana", "hub", "planefence", "stack") and "/" in n.strip("/") else n.strip("/").split("/")[0]
                    for n in p.stdout.splitlines() if n.strip("/")})
    print("This copy holds:")
    for i, it in enumerate(items):
        print(f"  {i + 1:2}. {it}")
    pick = input("Numbers to unpack, separated by commas (blank for everything): ").strip()
    chosen = [items[int(x) - 1] for x in pick.replace(" ", "").split(",") if x] if pick else []
    want = " ".join(shlex.quote(c) for c in chosen)
    p = subprocess.run(["bash", "-c", f"set -o pipefail; {src} | tar -C {shlex.quote(out)} -xf - {want}"], env=env)
    print("Unpacked into", out if p.returncode == 0 else "NOTHING: something went wrong")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "restore":
        restore()
        sys.exit(0)
    if not TOKEN:
        log("STACK_TOKEN is not set: cannot talk to the portal")
    log("adsb-backup started")
    while True:
        try:
            tick()
        except Exception as e:   # the portal restarting, a network blip: try again next round
            log(f"tick: {type(e).__name__}: {e}")
        time.sleep(POLL)
