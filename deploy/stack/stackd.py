"""adsb-stack: keeps the hub's compose stacks in step with the compose files baked into this image.

On start (and whenever POST /apply is called) it runs `docker compose up -d` for every stack listed in stacks.json, using the hub's own
.env file for each stack (secrets never live in the image). A new release of this image that lists a new stack makes that service appear
on the hub by itself; a changed compose file makes the changed service be recreated. Stacks whose folder or .env is missing and whose
"env" is "required" are skipped, not failed, so an optional stack never blocks the others. Nothing is ever removed or stopped.
"""
import hmac, json, os, re, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MANIFEST = os.environ.get("STACKS_MANIFEST", "/stacks/stacks.json")
TOKEN = os.environ.get("STACK_TOKEN", "")
PORT = int(os.environ.get("STACK_PORT", "8099"))
lock = threading.Lock()
state = {"started": time.time(), "last_apply": None, "results": {}, "applying": False}


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def keep_compose_file_current(d, src):
    """Make <dir>/compose.yaml the file that actually runs, keeping the previous one as compose.yaml.bak-<time> when it differs."""
    dst = os.path.join(d, "compose.yaml")
    new = open(src, "rb").read()
    old = open(dst, "rb").read() if os.path.isfile(dst) else None
    if old == new:
        return
    if old is not None:
        open(dst + ".bak-" + time.strftime("%Y%m%d-%H%M%S"), "wb").write(old)
    open(dst, "wb").write(new)


LEFTOVER = re.compile(r'The container name "/([0-9a-f]{12}_[A-Za-z0-9_.-]+)" is already in use by container "([0-9a-f]{64})"')


def clear_leftovers(output):
    """compose parks a container it is replacing under the name <12 hex>_<name>; an interrupted earlier replace can leave one behind, and every
    later replace of that service then fails with a name conflict. Remove such a leftover, only when it is stopped. True if anything was removed."""
    removed = False
    for name, cid in set(LEFTOVER.findall(output)):
        run = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", cid], capture_output=True, text=True).stdout.strip()
        if run == "false":
            r = subprocess.run(["docker", "rm", cid], capture_output=True, text=True)
            log(f"removed leftover container {name}: " + ("ok" if r.returncode == 0 else r.stderr.strip()[-200:]))
            removed = removed or r.returncode == 0
    return removed


CONFLICT = re.compile(r'The container name "/([A-Za-z0-9][A-Za-z0-9_.-]*)" is already in use by container "([0-9a-f]{64})"')
NEVER_REMOVE = {x.strip() for x in os.environ.get("STACK_NEVER_REMOVE", "adsb-portal,adsb-portal-db,adsb-stack,watchtower").split(",") if x.strip()}


def clear_name_conflicts(output):
    """compose wants to create a container under a name that another container already holds. That happens when the update service (Watchtower) replaced the same container at the same moment, so
    compose no longer recognizes it as its own. The holder is removed when it is stopped, or when it is a container the update service manages (it carries the Watchtower label); the stack's
    data lives in volumes, so nothing is lost, and compose then creates the container itself. The portal, its database, this service and Watchtower are never removed. True if anything was removed."""
    removed = False
    for name, cid in set(CONFLICT.findall(output)):
        if re.match(r"[0-9a-f]{12}_", name) or name in NEVER_REMOVE:
            continue
        info = subprocess.run(["docker", "inspect", "-f", '{{.State.Running}} {{index .Config.Labels "com.centurylinklabs.watchtower.enable"}}', cid], capture_output=True, text=True).stdout.split()
        running, managed = (info[0] if info else "true"), (info[1] if len(info) > 1 else "")
        if running == "false" or managed == "true":
            r = subprocess.run(["docker", "rm", "-f", cid], capture_output=True, text=True)
            log(f"removed container {name} that held the name compose needed: " + ("ok" if r.returncode == 0 else r.stderr.strip()[-200:]))
            removed = removed or r.returncode == 0
    return removed


def apply_stack(base, s):
    d = os.path.join(base, s["dir"])
    env = os.path.join(d, ".env")
    if s.get("env") == "required" and not (os.path.isdir(d) and os.path.isfile(env)):
        return {"ok": True, "skipped": f"{d}/.env not found"}
    if not os.path.isfile(s["file"]):
        return {"ok": False, "error": "compose file missing from image"}
    os.makedirs(d, exist_ok=True)
    keep_compose_file_current(d, s["file"])
    cmd = ["docker", "compose", "-p", s.get("project", s["name"]), "--project-directory", d, "-f", s["file"]]
    if os.path.isfile(env):
        cmd += ["--env-file", env]
    shared = os.path.join(base, s["env_file"]) if s.get("env_file") else None   # e.g. the stack token, shared by services that talk to the portal
    if shared and os.path.isfile(shared):
        cmd += ["--env-file", shared]
    cmd += ["up", "-d", "--pull", "missing", "--wait-timeout", "120"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    out = (p.stdout + p.stderr).strip()[-1500:]
    if p.returncode != 0 and (clear_leftovers(p.stdout + p.stderr) | clear_name_conflicts(p.stdout + p.stderr)):
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)   # once more now the way is clear
        out = (p.stdout + p.stderr).strip()[-1500:]
    # Watchtower updates the same labelled containers when a new image appears, so the two can recreate one container at the same moment: compose then loses the container it was
    # starting ("No such container") and leaves the new one created but not started. Letting the other updater finish and asking again starts it.
    for _wait in (20, 40):
        if p.returncode == 0:
            break
        log(f"stack {s['name']} did not come up ({out[-160:].replace(chr(10), ' | ')}); trying again in {_wait} s")
        time.sleep(_wait)
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
        out = (p.stdout + p.stderr).strip()[-1500:]
        if p.returncode != 0 and (clear_leftovers(p.stdout + p.stderr) | clear_name_conflicts(p.stdout + p.stderr)):
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
            out = (p.stdout + p.stderr).strip()[-1500:]
    return {"ok": p.returncode == 0, "output": out}


def apply_all():
    with lock:
        state["applying"] = True
        try:
            m = json.load(open(MANIFEST))
            res = {}
            for s in m["stacks"]:
                try:
                    res[s["name"]] = apply_stack(m["base"], s)
                except Exception as e:  # keep going with the other stacks
                    res[s["name"]] = {"ok": False, "error": str(e)}
                log(f"stack {s['name']}: " + json.dumps({k: v for k, v in res[s['name']].items() if k != 'output'}))
                if not res[s["name"]].get("ok") and res[s["name"]].get("output"):
                    log(f"stack {s['name']} failed: " + res[s["name"]]["output"][-800:].replace(chr(10), " | "))
            state["results"], state["last_apply"] = res, time.time()
            return res
        finally:
            state["applying"] = False


def find_proxy():
    """The Nginx Proxy Manager container: NPM_CONTAINER if set, else the first running container whose image looks like NPM or nginx-proxy."""
    name = os.environ.get("NPM_CONTAINER", "")
    if name:
        return name
    out = subprocess.run(["docker", "ps", "--format", "{{.Names}} {{.Image}}"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        n, _, img = line.partition(" ")
        if any(k in img.lower() for k in ("nginx-proxy-manager", "nginx-proxy", "npm")):
            return n
    return ""


def watch_restarts():
    """Whenever an updated (labelled) container starts, make the reverse proxy re-resolve container names. Without this nginx keeps the old
    address of a recreated container and answers 502 until it is restarted by hand."""
    fmt = "{{.Actor.Attributes.name}}"
    while True:
        try:
            p = subprocess.Popen(["docker", "events", "--filter", "type=container", "--filter", "event=start",
                                  "--filter", "label=com.centurylinklabs.watchtower.enable=true", "--format", fmt],
                                 stdout=subprocess.PIPE, text=True)
            for line in p.stdout:
                name = line.strip()
                if name == "adsb-stack":
                    continue
                time.sleep(4)  # let the container come up and DNS settle, and fold a burst of restarts into one reload
                npm = find_proxy()
                if npm:
                    r = subprocess.run(["docker", "exec", npm, "nginx", "-s", "reload"], capture_output=True, text=True)
                    log(f"{name} restarted: proxy {npm} reload " + ("ok" if r.returncode == 0 else "failed: " + r.stderr.strip()[-200:]))
                else:
                    log(f"{name} restarted: no proxy container found to reload")
        except Exception as e:
            log(f"event watcher: {e}")
        time.sleep(10)


class H(BaseHTTPRequestHandler):
    def _auth(self):
        got = self.headers.get("Authorization", "")
        if not TOKEN or not hmac.compare_digest(got, "Bearer " + TOKEN):
            self._send(401, {"error": "unauthorized"})
            return False
        return True

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True})
        if self._auth() and self.path == "/status":
            self._send(200, state)
        elif self.path != "/status":
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self._auth():
            if self.path == "/apply":
                self._send(200, {"results": apply_all()})
            else:
                self._send(404, {"error": "not found"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    if not TOKEN:
        log("STACK_TOKEN is not set: the HTTP endpoint will refuse every request, start-up apply still runs")
    threading.Thread(target=apply_all, daemon=True).start()
    threading.Thread(target=watch_restarts, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
