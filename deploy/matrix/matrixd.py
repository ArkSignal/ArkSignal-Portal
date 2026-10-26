"""arksignal-matrix: runs the Matrix homeserver (Synapse) and takes its settings from the portal.

The portal sends the settings (server name, public address, the sign-in provider that is the portal itself and its client secret, federation, limits) to POST /apply; this program writes them into Synapse's
configuration folder and (re)starts Synapse. Until the first apply Synapse does not run and /status says so. Nothing here is reachable from outside: the service listens on the stack's internal network only, like
the other ArkSignal services; Synapse's own port (8008) is what the proxy publishes for the Matrix address.

Kept on purpose: password login is switched off (members sign in only through the portal, so its two-factor sign-in, passkeys and disabled accounts apply), nobody can register on the Matrix side, rooms are end-to-end
encrypted by default, federation is off unless the portal says otherwise, and the secrets Synapse needs (macaroon, form, registration) are made once here and kept in /data/secrets.json."""
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA = os.environ.get("MATRIX_DATA", "/data")
PORT = int(os.environ.get("PORT", "8483"))
DB_HOST = os.environ.get("MATRIX_DB_HOST", "matrix-db")
DB_PASS = os.environ.get("MATRIX_DB_PASSWORD", "matrix")
SYNAPSE_UID = int(os.environ.get("SYNAPSE_UID", "991"))
SYNAPSE_GID = int(os.environ.get("SYNAPSE_GID", "991"))
VERSION = "dev"
try:
    t = open("/BUILD_INFO").read()
    VERSION = t.split("version:")[1].split()[0] if "version:" in t else "dev"
except OSError:
    pass

STATE = {"applied": False, "running": False, "restarts": 0, "since": None, "error": "", "server_name": "", "exit": None, "last_sync": None}
_proc = None
_lock = threading.Lock()


def path(*p):
    return os.path.join(DATA, *p)


def secrets_file():
    f = path("secrets.json")
    try:
        return json.load(open(f))
    except (OSError, ValueError):
        s = {"macaroon_secret_key": secrets.token_urlsafe(48), "form_secret": secrets.token_urlsafe(48), "registration_shared_secret": secrets.token_urlsafe(48)}
        os.makedirs(DATA, exist_ok=True)
        with open(f, "w") as fh:
            json.dump(s, fh)
        os.chmod(f, 0o600)
        return s


def q(v):
    return json.dumps(v)   # JSON strings are valid YAML scalars


def render(c):
    """homeserver.yaml text for a settings dictionary from the portal."""
    s = secrets_file()
    name = c["server_name"]
    lines = [
        f"server_name: {q(name)}",
        f"public_baseurl: {q(c['public_baseurl'])}",
        "pid_file: /tmp/homeserver.pid",
        "report_stats: false",
        f"signing_key_path: {q(path(name + '.signing.key'))}",
        f"media_store_path: {q(path('media_store'))}",
        f"max_upload_size: {q(c.get('max_upload_size', '50M'))}",
        f"macaroon_secret_key: {q(s['macaroon_secret_key'])}",
        f"form_secret: {q(s['form_secret'])}",
        f"registration_shared_secret: {q(s['registration_shared_secret'])}",
        "listeners:",
        "  - port: 8008",
        "    tls: false",
        "    type: http",
        "    x_forwarded: true",
        "    bind_addresses: ['0.0.0.0']",
        "    resources:",
        "      - names: [client, federation]" if c.get("federation") else "      - names: [client]",
        "        compress: false",
        "database:",
        "  name: psycopg2",
        "  args:",
        "    user: matrix",
        f"    password: {q(DB_PASS)}",
        "    database: matrix",
        f"    host: {q(DB_HOST)}",
        "    cp_min: 5",
        "    cp_max: 10",
        "log_config: " + q(path("log.config")),
        "enable_registration: false",
        "password_config:",
        "  enabled: false",
        "presence:",
        f"  enabled: {'true' if c.get('presence', True) else 'false'}",
        f"url_preview_enabled: {'true' if c.get('url_previews', True) else 'false'}",
        "url_preview_ip_range_blacklist: ['127.0.0.0/8','10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','100.64.0.0/10','169.254.0.0/16','::1/128','fe80::/64','fc00::/7']",
        "encryption_enabled_by_default_for_room_type: invite",
        "user_directory:",
        "  enabled: true",
        "  search_all_users: true",   # members find each other by name or user name (everyone here is a member of the site)
        "limit_profile_requests_to_users_who_share_rooms: false",
        "autocreate_auto_join_rooms: false",
        *(["auto_join_rooms:"] + [f"  - {q('#' + a + ':' + name)}" for a in c.get("auto_join", [])] if c.get("auto_join") else []),
        *(["app_service_config_files:", f"  - {q(path('appservice-arksignal.yaml'))}"] if c.get("as_token") and c.get("hs_token") else []),
        "ip_range_whitelist: ['172.16.0.0/12','10.0.0.0/8','192.168.0.0/16']",   # the site and the bridge are on the hub's private network: push and application service calls go there
        "modules:",
        "  - module: arksignal_policy.Policy",
        "    config:",
        f"      policy_file: {q(path('policy.json'))}",
        "allow_public_rooms_over_federation: false",
        "allow_public_rooms_without_auth: false",
        "serve_server_wellknown: true",
        "suppress_key_server_warning: true",
        "trusted_key_servers: []" if not c.get("federation") else "trusted_key_servers:\n  - server_name: matrix.org",
        "federation_domain_whitelist: []" if not c.get("federation") else "",
        f"media_retention:\n  remote_media_lifetime: {q(c.get('remote_media_lifetime', '30d'))}" if c.get("federation") else "",
        *(["sso:", "  client_whitelist:", f"    - {q(c['element_url'].rstrip('/') + '/')}"] if c.get("element_url") else []),   # Element is sent straight back with the login token (no 'continue' page, which could not be shown inside the site's frame)
        "oidc_providers:",
        "  - idp_id: arksignal",
        f"    idp_name: {q(c.get('idp_name', 'ArkSignal'))}",
        "    discover: " + ("false" if c.get("oidc_internal") else "true"),
        f"    issuer: {q(c['issuer'])}",
        f"    client_id: {q(c['client_id'])}",
        f"    client_secret_path: {q(path('client_secret'))}",
        "    client_auth_method: client_secret_post",
        "    pkce_method: always",
        "    scopes: ['openid', 'profile', 'email']",
        "    allow_existing_users: true",
    ]
    if c.get("oidc_internal"):
        b = c["oidc_internal"].rstrip("/")
        # Synapse insists on https for the endpoints it calls; on the hub's private network they are plain http, so it is told to trust the direct (server to server) answer instead of re-checking its signature
        lines += ["    skip_verification: true"]
        lines += [f"    authorization_endpoint: {q(c['issuer'].rstrip('/') + '/oidc/authorize')}", f"    token_endpoint: {q(b + '/oidc/token')}", f"    userinfo_endpoint: {q(b + '/oidc/userinfo')}", f"    jwks_uri: {q(b + '/oidc/jwks')}"]
    lines += [
        "    user_mapping_provider:",
        "      config:",
        "        localpart_template: \"{{ user.preferred_username | lower }}\"",
        "        display_name_template: \"{{ user.name }}\"",
        "        email_template: \"{{ user.email }}\"",
    ]
    return "\n".join(x for x in lines if x != "") + "\n"


LOG_CONFIG = """version: 1
formatters:
  plain: {format: '%(asctime)s - %(name)s - %(levelname)s - %(message)s'}
handlers:
  console: {class: logging.StreamHandler, formatter: plain}
loggers:
  synapse.storage.SQL: {level: WARNING}
root: {level: INFO, handlers: [console]}
disable_existing_loggers: false
"""


def prepare(c):
    os.makedirs(path("media_store"), exist_ok=True)
    write_appservice(c)
    with open(path("homeserver.yaml"), "w") as f:
        f.write(render(c))
    os.chmod(path("homeserver.yaml"), 0o600)
    with open(path("log.config"), "w") as f:
        f.write(LOG_CONFIG)
    with open(path("applied.json"), "w") as f:
        json.dump({k: v for k, v in c.items() if k not in ("client_secret", "as_token", "hs_token")}, f)
    subprocess.run(["chown", "-R", f"{SYNAPSE_UID}:{SYNAPSE_GID}", DATA], check=False)   # the data folder is a host folder that may belong to root: Synapse must be able to write it every start


def drop():
    os.setgid(SYNAPSE_GID)
    os.setuid(SYNAPSE_UID)


def start(c):
    global _proc
    with _lock:
        stop_locked()
        prepare(c)
        gen = subprocess.run([sys.executable, "-m", "synapse.app.homeserver", "-c", path("homeserver.yaml"), "--generate-keys"], preexec_fn=drop, capture_output=True, text=True)
        if gen.returncode != 0:
            STATE.update(running=False, error=("Synapse could not make its signing key: " + gen.stderr[-300:]))
            return False
        _proc = subprocess.Popen([sys.executable, "-m", "synapse.app.homeserver", "-c", path("homeserver.yaml")], preexec_fn=drop)
        STATE.update(applied=True, running=True, since=time.time(), error="", server_name=c["server_name"], exit=None)
        STATE["restarts"] += 1
        return True


def stop_locked():
    global _proc
    if _proc is not None and _proc.poll() is None:
        _proc.terminate()
        try:
            _proc.wait(25)
        except subprocess.TimeoutExpired:
            _proc.kill()
    _proc = None
    STATE["running"] = False


def watch():
    """Start Synapse again if it stops by itself (after a crash), with the last settings that were applied."""
    while True:
        time.sleep(5)
        again = None
        with _lock:
            if _proc is not None and _proc.poll() is not None and STATE["running"]:
                STATE.update(running=False, exit=_proc.returncode, error=f"Synapse stopped (exit {_proc.returncode}) and was started again.")
                again = _applied()
        if again:
            again["client_secret"] = _secret()
            err = STATE["error"]
            start(again)
            STATE["error"] = err


def _applied():
    try:
        c = json.load(open(path("applied.json")))
    except (OSError, ValueError):
        return None
    try:
        c.update(json.load(open(path("appservice.json"))))   # the application service's tokens are kept apart (private), and rejoin the settings on a restart
    except (OSError, ValueError):
        pass
    return c


def _secret():
    try:
        return open(path("client_secret")).read().strip()
    except OSError:
        return ""


def synapse_ok():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8008/health", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def synapse_version():
    try:
        import synapse
        return synapse.__version__
    except Exception:
        return ""



# ---------------------------------------------------------------- the portal's housekeeping on Synapse (through its admin API)

ADMIN_NAME = "arksignal-admin"
_admin = {"token": "", "user": ""}


class ApiError(Exception):
    def __init__(self, status, body=""):
        super().__init__(f"HTTP {status} {str(body)[:200]}")
        self.status = status


def api(method, p, body=None, token=None, timeout=20):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:8008" + p, data=data, method=method, headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise ApiError(e.code, e.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError) as e:
        raise ApiError(0, str(e))


def uid(lp, server=None):
    return "@" + lp.lower() + ":" + (server or STATE["server_name"])


def admin_token():
    """The access token of the server's own administrator account (made once with the registration secret, so no password is ever needed or kept)."""
    import hashlib
    import hmac
    if _admin["token"]:
        try:
            api("GET", "/_matrix/client/v3/account/whoami", token=_admin["token"])
            return _admin["token"]
        except ApiError:
            _admin["token"] = ""
    try:
        saved = json.load(open(path("admin.json")))
        api("GET", "/_matrix/client/v3/account/whoami", token=saved["token"])
        _admin.update(saved)
        return saved["token"]
    except (OSError, ValueError, KeyError, ApiError):
        pass
    name = ADMIN_NAME + ("" if not os.path.exists(path("admin.json")) else "-" + secrets.token_hex(3))   # a lost token: a new administrator account (nothing can log in with a password)
    secret = secrets_file()["registration_shared_secret"]
    nonce = api("GET", "/_synapse/admin/v1/register")["nonce"]
    password = secrets.token_urlsafe(32)
    mac = hmac.new(secret.encode(), digestmod=hashlib.sha1)
    mac.update(nonce.encode() + b"\x00" + name.encode() + b"\x00" + password.encode() + b"\x00" + b"admin")
    r = api("POST", "/_synapse/admin/v1/register", {"nonce": nonce, "username": name, "password": password, "admin": True, "displayname": "ArkSignal Admin", "mac": mac.hexdigest()})
    _admin.update(token=r["access_token"], user=r["user_id"])
    with open(path("admin.json"), "w") as f:
        json.dump({"token": r["access_token"], "user": r["user_id"]}, f)
    os.chmod(path("admin.json"), 0o600)
    return r["access_token"]


def A(method, p, body=None):
    return api(method, p, body, token=admin_token())


def user_exists(lp):
    try:
        A("GET", "/_synapse/admin/v2/users/" + urllib.parse.quote(uid(lp)))
        return True
    except ApiError as e:
        if e.status == 404:
            return False
        raise


def ensure_member(lp, name):
    """A chat account for this member with this display name, made without a password (they sign in through the site, which links to it). Returns 'created', 'renamed' or 'same'."""
    lp = lp.lower()
    url = "/_synapse/admin/v2/users/" + urllib.parse.quote(uid(lp))
    try:
        cur = A("GET", url)
    except ApiError as e:
        if e.status != 404:
            raise
        A("PUT", url, {"displayname": name, "admin": False})
        return "created"
    if name and cur.get("displayname") != name:
        A("PUT", url, {"displayname": name})
        return "renamed"
    return "same"


def act_as(lp, method, p, body=None):
    """One call made as a member (a one-minute token minted by the administrator): how a member's own settings are changed for them."""
    t = A("POST", "/_synapse/admin/v1/users/" + urllib.parse.quote(uid(lp)) + "/login", {"valid_until_ms": int((time.time() + 60) * 1000)})["access_token"]
    return api(method, p, body, token=t)


def ensure_room(r):
    alias = "#" + r["alias"].lower() + ":" + STATE["server_name"]
    try:
        return A("GET", "/_matrix/client/v3/directory/room/" + urllib.parse.quote(alias))["room_id"]
    except ApiError as e:
        if e.status != 404:
            raise
    made = A("POST", "/_matrix/client/v3/createRoom", {"name": r["name"], "topic": r.get("topic", ""), "room_alias_name": r["alias"].lower(), "preset": "public_chat", "visibility": "public",
                                                       "initial_state": [{"type": "m.room.guest_access", "state_key": "", "content": {"guest_access": "forbidden"}}]})
    return made["room_id"]


def set_moderators(room_id, mods):
    q_ = urllib.parse.quote(room_id)
    pl = A("GET", f"/_matrix/client/v3/rooms/{q_}/state/m.room.power_levels")
    users = {u: lvl for u, lvl in pl.get("users", {}).items() if lvl >= 100 or u == _admin.get("user")}
    for lp in mods:
        users.setdefault(uid(lp), 50)
    if users != pl.get("users", {}):
        pl["users"] = users
        A("PUT", f"/_matrix/client/v3/rooms/{q_}/state/m.room.power_levels", pl)


def sync(c):
    """Bring Synapse in step with what the portal says: locked accounts, ignore lists (blocks), the standing rooms and their moderators, and the policy the module reads. Safe to repeat."""
    out = {"locked": 0, "unlocked": 0, "ignored": 0, "rooms": 0, "errors": []}
    state = {}
    try:
        state = json.load(open(path("sync_state.json")))
    except (OSError, ValueError):
        pass
    prev_locked, prev_ign = set(state.get("locked", [])), set(state.get("ignoring", []))
    profiles = dict(state.get("profiles", {}))
    out["provisioned"] = 0
    for m in c.get("members", [])[:2000]:   # an account for every member, so a search finds them before they open chat; only changes are sent
        lp, nm = str(m.get("lp", "")).lower(), str(m.get("name", ""))[:80]
        if not lp or profiles.get(lp) == nm:
            continue
        try:
            if ensure_member(lp, nm) != "same":
                out["provisioned"] += 1
            profiles[lp] = nm
        except ApiError as e:
            out["errors"].append(f"account {lp}: {e}")
        if out["provisioned"] >= 300:
            break   # the rest follows on the next pass
    with open(path("policy.json"), "w") as f:
        json.dump(c.get("policy", {}), f)
    now_locked = set(x.lower() for x in c.get("locked", []))
    new_locked, ignoring = set(), set()
    for lp in sorted(now_locked | prev_locked):
        want = lp in now_locked
        try:
            if not user_exists(lp):
                if want:
                    new_locked.add(lp)   # no account yet: nothing to lock, and the portal refuses their sign-in anyway
                continue
            A("PUT", "/_synapse/admin/v2/users/" + urllib.parse.quote(uid(lp)), {"locked": want})
            out["locked" if want else "unlocked"] += 1
            if want:
                new_locked.add(lp)
        except ApiError as e:
            out["errors"].append(f"lock {lp}: {e}")
            if want:
                new_locked.add(lp)
    ign = {k.lower(): [x.lower() for x in v] for k, v in c.get("ignores", {}).items()}
    for lp in sorted(set(ign) | prev_ign):
        try:
            if not user_exists(lp):
                continue
            act_as(lp, "PUT", "/_matrix/client/v3/user/" + urllib.parse.quote(uid(lp)) + "/account_data/m.ignored_user_list", {"ignored_users": {uid(x): {} for x in ign.get(lp, [])}})
            out["ignored"] += 1
            if ign.get(lp):
                ignoring.add(lp)
        except ApiError as e:
            out["errors"].append(f"ignore list {lp}: {e}")
            if ign.get(lp):
                ignoring.add(lp)
    for r in c.get("rooms", []):
        try:
            rid = ensure_room(r)
            set_moderators(rid, c.get("moderators", []))
            out["rooms"] += 1
        except ApiError as e:
            out["errors"].append(f"room {r.get('alias')}: {e}")
    with open(path("sync_state.json"), "w") as f:
        json.dump({"locked": sorted(new_locked), "ignoring": sorted(ignoring), "profiles": profiles, "at": time.time()}, f)
    STATE["last_sync"] = {"at": time.time(), **{k: v for k, v in out.items() if k != "errors"}, "errors": out["errors"][:5]}
    return out


def reports(limit=100):
    r = A("GET", f"/_synapse/admin/v1/event_reports?limit={int(limit)}&dir=b")
    out = []
    for e in r.get("event_reports", []):
        out.append({"id": e["id"], "ts": e.get("received_ts"), "room_id": e.get("room_id"), "room_name": e.get("name") or "", "event_id": e.get("event_id"), "reporter": e.get("user_id"), "sender": e.get("sender"),
                    "reason": e.get("reason") or "", "score": e.get("score")})
    return {"reports": out, "total": r.get("total", len(out))}


def redact(room_id, event_id, reason):
    q_ = urllib.parse.quote(room_id)
    try:
        A("POST", f"/_synapse/admin/v1/join/{q_}", {"user_id": _admin.get("user") or json.load(open(path("admin.json")))["user"]})
    except ApiError:
        pass   # already in the room, or the room is gone
    return A("PUT", f"/_matrix/client/v3/rooms/{q_}/redact/{urllib.parse.quote(event_id)}/{secrets.token_hex(8)}", {"reason": reason[:200]})


def stats():
    out = {}
    try:
        out["users"] = A("GET", "/_synapse/admin/v2/users?limit=1&guests=false&deactivated=false").get("total", 0)
        out["rooms"] = A("GET", "/_synapse/admin/v1/rooms?limit=1").get("total_rooms", 0)
    except ApiError as e:
        out["error"] = str(e)
    return out



# ---------------------------------------------------------------- the bridge bot (an application service: the portal gets the events of rooms the bot is in, and posts as the bot)

BOT_NAME = "arksignal-bridge"


def bot_id():
    return uid(BOT_NAME)


def as_api(method, p, body=None, as_user=None):
    """A call to Synapse as the application service (its own token), acting as the bridge bot."""
    tok = _applied_cfg().get("as_token", "")
    sep = "&" if "?" in p else "?"
    return api(method, p + (f"{sep}user_id={urllib.parse.quote(as_user or bot_id())}" if as_user != "" else ""), body, token=tok)


def _applied_cfg():
    try:
        return json.load(open(path("appservice.json")))
    except (OSError, ValueError):
        return {}


def ensure_bot():
    try:
        api("POST", "/_matrix/client/v3/register", {"type": "m.login.application_service", "username": BOT_NAME}, token=_applied_cfg().get("as_token", ""))
    except ApiError as e:
        if e.status != 400 or "M_USER_IN_USE" not in str(e):
            raise


def bridge_join(room_id, member):
    """Put the bridge bot in a room: the member who asked invites it (they need the right to), else a public room is joined through the administrator."""
    ensure_bot()
    q_ = urllib.parse.quote(room_id)
    try:
        as_api("POST", f"/_matrix/client/v3/join/{q_}", {})
        return {"joined": True}
    except ApiError:
        pass
    try:
        act_as(member, "POST", f"/_matrix/client/v3/rooms/{q_}/invite", {"user_id": bot_id()})
    except ApiError as e:
        try:
            A("POST", f"/_synapse/admin/v1/join/{q_}", {"user_id": bot_id()})
            return {"joined": True}
        except ApiError:
            raise ApiError(e.status, "You cannot invite the bridge into that room (you need the right to invite people).")
    as_api("POST", f"/_matrix/client/v3/join/{q_}", {})
    return {"joined": True}


def bridge_leave(room_id):
    try:
        as_api("POST", f"/_matrix/client/v3/rooms/{urllib.parse.quote(room_id)}/leave", {})
    except ApiError:
        pass
    return {"left": True}


def bot_post(room_id, text, html=None):
    body = {"msgtype": "m.notice", "body": text}
    if html:
        body.update(format="org.matrix.custom.html", formatted_body=html)
    r = as_api("PUT", f"/_matrix/client/v3/rooms/{urllib.parse.quote(room_id)}/send/m.room.message/{secrets.token_hex(10)}", body)
    return {"event_id": r.get("event_id", "")}


def rooms_of(lp):
    """The rooms a member is in, each marked encrypted or not and whether the member made it: only an unencrypted room can be bridged (the bot could not read an encrypted one, and would not be let to), and only by the person who made it (a standing room, made by the site, or somebody else's room is never theirs to bridge)."""
    out = []
    me = uid(lp)
    for rid in act_as(lp, "GET", "/_matrix/client/v3/joined_rooms").get("joined_rooms", []):
        name, enc, maker = "", False, ""
        try:
            for ev in act_as(lp, "GET", f"/_matrix/client/v3/rooms/{urllib.parse.quote(rid)}/state"):
                t = ev.get("type")
                if t == "m.room.name":
                    name = (ev.get("content") or {}).get("name", "")
                elif t == "m.room.encryption":
                    enc = True
                elif t == "m.room.create":
                    maker = ev.get("sender", "")
                    if not maker:
                        maker = (ev.get("content") or {}).get("creator", "")
        except ApiError:
            enc = True   # not readable: not offered
        out.append({"room_id": rid, "name": name or rid, "encrypted": enc, "mine": bool(maker) and maker.lower() == me.lower()})
    return {"rooms": sorted(out, key=lambda r: r["name"].lower())}


def write_appservice(c):
    """The registration file Synapse reads: the portal is the application service, with one bot user and nothing else claimed."""
    if not c.get("as_token") or not c.get("hs_token"):
        return None
    server = c["server_name"]
    rx = "@" + BOT_NAME + ":" + server.replace(".", "\\.")
    text = "\n".join(["id: arksignal", f"url: {q(c.get('appservice_url', 'http://adsb-portal:8430'))}", f"as_token: {q(c['as_token'])}", f"hs_token: {q(c['hs_token'])}", f"sender_localpart: {q(BOT_NAME)}",
                      "rate_limited: false", "namespaces:", "  users:", "    - exclusive: true", f"      regex: {q(rx)}", "  aliases: []", "  rooms: []"]) + "\n"
    with open(path("appservice-arksignal.yaml"), "w") as f:
        f.write(text)
    os.chmod(path("appservice-arksignal.yaml"), 0o600)
    with open(path("appservice.json"), "w") as f:
        json.dump({"as_token": c["as_token"], "hs_token": c["hs_token"]}, f)
    os.chmod(path("appservice.json"), 0o600)
    return path("appservice-arksignal.yaml")


def pusher(c):
    """Register, remove or clear a member's HTTP pushers (Web Push through the site): done as the member."""
    lp, app = c["member"], c.get("app_id", "net.arksignal.web")
    base = "/_matrix/client/v3/pushers"
    if c.get("clear"):
        for p in act_as(lp, "GET", base).get("pushers", []):
            if p.get("app_id") == app:
                act_as(lp, "POST", base + "/set", {"kind": None, "app_id": app, "pushkey": p["pushkey"]})
        return {}
    if c.get("delete"):
        act_as(lp, "POST", base + "/set", {"kind": None, "app_id": app, "pushkey": c["pushkey"]})
        return {}
    act_as(lp, "POST", base + "/set", {"kind": "http", "app_id": app, "app_display_name": "ArkSignal", "device_display_name": c.get("device") or "Browser", "pushkey": c["pushkey"], "lang": "en",
                                       "data": {"url": c["url"]}, "append": True})
    return {}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _allowed(self):
        """With a SERVICE_TOKEN set, every request but /health must carry it (the portal and this stack's other services do)."""
        want = os.environ.get("SERVICE_TOKEN", "")
        if not want or urllib.parse.urlparse(self.path).path.rstrip("/") in ("", "/health"):
            return True
        import hmac
        return hmac.compare_digest(self.headers.get("X-Service-Token", ""), want)

    def do_GET(self):
        if not self._allowed():
            return self.send({"error": "unauthorized"}, 401)
        if self.path == "/health":
            return self.send({"ok": True, "version": VERSION})
        if self.path == "/status":
            return self.send({**STATE, "synapse_answering": synapse_ok() if STATE["running"] else False, "synapse_version": synapse_version(), "service_version": VERSION,
                              "uptime": (time.time() - STATE["since"]) if STATE["since"] else 0})
        if self.path.startswith("/reports"):
            try:
                return self.send({"ok": True, **reports()})
            except ApiError as e:
                return self.send({"ok": False, "error": str(e)}, 502)
        if self.path == "/stats":
            return self.send({"ok": True, **stats()})
        self.send({"error": "not found"}, 404)

    def do_POST(self):
        if not self._allowed():
            return self.send({"error": "unauthorized"}, 401)
        n = int(self.headers.get("Content-Length") or 0)
        try:
            c = json.loads(self.rfile.read(min(n, 200000)) or b"{}")
        except ValueError:
            return self.send({"error": "bad request"}, 400)
        if self.path == "/apply":
            need = ("server_name", "public_baseurl", "issuer", "client_id", "client_secret")
            if any(not c.get(k) for k in need):
                return self.send({"ok": False, "error": "Missing: " + ", ".join(k for k in need if not c.get(k))}, 400)
            os.makedirs(DATA, exist_ok=True)
            fd = os.open(path("client_secret"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # private from its first byte
            with os.fdopen(fd, "w") as f:
                f.write(c["client_secret"])
            os.chmod(path("client_secret"), 0o600)
            ok = start(c)
            return self.send({"ok": ok, "error": STATE["error"]}, 200 if ok else 500)
        if self.path == "/sync":
            if not STATE["running"]:
                return self.send({"ok": False, "error": "Synapse is not running."}, 503)
            try:
                return self.send({"ok": True, **sync(c)})
            except ApiError as e:
                return self.send({"ok": False, "error": str(e)}, 502)
        if self.path in ("/bridge/join", "/bridge/leave", "/bridge/post", "/bridge/rooms"):
            try:
                if self.path == "/bridge/join":
                    return self.send({"ok": True, **bridge_join(c["room_id"], c["member"])})
                if self.path == "/bridge/leave":
                    return self.send({"ok": True, **bridge_leave(c["room_id"])})
                if self.path == "/bridge/post":
                    return self.send({"ok": True, **bot_post(c["room_id"], c["text"], c.get("html"))})
                return self.send({"ok": True, **rooms_of(c["member"])})
            except (ApiError, KeyError) as e:
                return self.send({"ok": False, "error": str(e)}, 502)
        if self.path == "/ensure":
            try:
                return self.send({"ok": True, "result": ensure_member(c["lp"], str(c.get("name", ""))[:80])})
            except (ApiError, KeyError) as e:
                return self.send({"ok": False, "error": str(e)}, 502)
        if self.path == "/pusher":
            try:
                if not user_exists(c["member"]):
                    return self.send({"ok": False, "error": "That member has not used chat yet; notifications start after their first chat sign-in."}, 404)
                return self.send({"ok": True, **pusher(c)})
            except (ApiError, KeyError) as e:
                return self.send({"ok": False, "error": str(e)}, 502)
        if self.path == "/room-id":
            try:
                return self.send({"ok": True, "room_id": A("GET", "/_matrix/client/v3/directory/room/" + urllib.parse.quote("#" + c["alias"].lower() + ":" + STATE["server_name"]))["room_id"]})
            except (ApiError, KeyError) as e:
                return self.send({"ok": False, "error": str(e)}, 404)
        if self.path == "/media-prune":
            try:
                days = max(1, int(c["days"]))
                r = A("POST", f"/_synapse/admin/v1/media/delete?before_ts={int((time.time() - days * 86400) * 1000)}&keep_profiles=true")
                return self.send({"ok": True, "total": int(r.get("total", 0))})
            except (ApiError, KeyError, ValueError) as e:
                return self.send({"ok": False, "error": str(e)}, 502)
        if self.path == "/redact":
            try:
                return self.send({"ok": True, **redact(c["room_id"], c["event_id"], c.get("reason", "Removed by a moderator"))})
            except (ApiError, KeyError) as e:
                return self.send({"ok": False, "error": str(e)}, 502)
        self.send({"error": "not found"}, 404)


def main():
    os.makedirs(DATA, exist_ok=True)
    c = _applied()
    if c and _secret():
        c["client_secret"] = _secret()
        start(c)   # a restart of the container brings Synapse back with the last settings by itself
    threading.Thread(target=watch, daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: (stop_locked(), os._exit(0)))
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()


if __name__ == "__main__":
    main()
