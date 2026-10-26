"""The portal's side of the Matrix service (deploy/matrix: Synapse with a small program that takes its settings from here, and its own Postgres database).

The chat itself is the Matrix server and the Element app; this module only keeps the service in step with the site: the settings (server name, public address, federation and so on) and the sign-in provider (the
site itself, app/oidcprovider.py, with a client secret the site makes and keeps sealed) go to the service when an administrator presses Apply, and again by themselves when the service has restarted without them. When the service
is away every call returns a plain note and the rest of the site works as before."""
import json
import re
import time
import urllib.error
import urllib.request

from datetime import datetime, timezone

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from . import oidcprovider
from . import csrf
from .action_response import action_response
from .admin import permission_required
from .audit import audit
from .cache import cached
from .db import get_db
from .settings import cfg, cfg_int

bp = Blueprint("matrix", __name__)


def enabled():
    return cfg("matrix_enabled") == "1"


def replaces_builtin():
    """True while Matrix is on for the site (whoever may see it): the built-in chat and messages are closed, their addresses show Element."""
    from .settings import get_setting
    return get_setting("matrix_enabled", "0") == "1"


def call(path, method="GET", body=None, timeout=10):
    """(ok, data or message). Never raises."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(cfg("matrix_url").rstrip("/") + path, data=data, method=method, headers=dict({"User-Agent": cfg("lookup_user_agent"), "Content-Type": "application/json"}, **__import__("app.servicecall", fromlist=["headers"]).headers()))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, json.loads(r.read(1_000_000).decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode()).get("error") or ""
        except Exception:
            msg = ""
        return False, f"The Matrix service answered with an error ({e.code}){': ' + msg if msg else ''}."
    except Exception as e:
        return False, "The Matrix service did not answer (" + type(e).__name__ + ")."


def service_config():
    """What the service needs to run Synapse, sent when an administrator presses Apply."""
    return {"server_name": cfg("matrix_server_name").strip(), "public_baseurl": cfg("matrix_public_url").strip().rstrip("/") + "/", "issuer": oidcprovider.issuer_for_service(),
            "client_id": cfg("matrix_client_id"), "client_secret": oidcprovider.client_secret(create=True), "oidc_internal": cfg("matrix_oidc_internal").strip(), "idp_name": cfg("brand_name") + cfg("brand_accent"),
            "as_token": as_tokens()[0], "hs_token": as_tokens()[1], "appservice_url": cfg("matrix_appservice_url").strip(), "element_url": cfg("matrix_element_url").strip(), "auto_join": [r["alias"] for r in rooms()] if cfg("matrix_auto_join") == "1" else [], "federation": cfg("matrix_federation") == "1", "presence": cfg("matrix_presence") == "1", "url_previews": cfg("matrix_url_previews") == "1",
            "max_upload_size": f"{cfg_int('matrix_max_upload_mb')}M"}


def missing():
    """What has to be filled in before the service can be started, as plain sentences."""
    out = []
    if not cfg("matrix_server_name").strip():
        out.append("the Matrix server name (Settings > Matrix Chat)")
    if not cfg("matrix_public_url").strip():
        out.append("the Matrix address (Settings > Matrix Chat)")
    if not (cfg("main_address") or "").strip() and not cfg("matrix_issuer").strip():
        out.append("the site's Main Address (Settings > General), which Matrix uses to find this site")
    return out


def push_config():
    gaps = missing()
    if gaps:
        return False, "Not ready: fill in " + "; ".join(gaps) + "."
    ok, d = call("/apply", "POST", service_config(), timeout=120)
    cached("matrix_status", lambda: None, 0)
    if not ok:
        return False, d
    return True, "Settings and the sign-in client sent; Synapse is starting."


def status():
    def load():
        ok, d = call("/status", timeout=4)
        return {"ok": ok, "data": d if ok else None, "note": "" if ok else d}
    return cached("matrix_status", load, 10)


# ------------------------------------------------------------------ what Matrix apps and Element ask this site for

def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp


@bp.route("/.well-known/matrix/client")
def well_known_client():
    """How a Matrix app finds the server from an address like @name:arksignal.net (the Element phone app asks here)."""
    if not enabled():
        abort(404)
    return _cors(jsonify({"m.homeserver": {"base_url": cfg("matrix_public_url").strip().rstrip("/")}}))


@bp.route("/.well-known/matrix/server")
def well_known_server():
    if not enabled():
        abort(404)
    host = cfg("matrix_public_url").strip().split("//")[-1].rstrip("/")
    return _cors(jsonify({"m.server": host if ":" in host else host + ":443"}))


def element_theme():
    """The site's own colors as an Element theme (the look of the dark base; Element's own colors are replaced)."""
    return {"name": "ArkSignal", "is_dark": True, "colors": {
        "accent-color": "#3ee6a8", "primary-color": "#3ee6a8", "warning-color": "#ff6b6b", "sidebar-color": "#050a12", "roomlist-background-color": "#0b1424", "roomlist-text-color": "#e8eef7",
        "roomlist-text-secondary-color": "#8a9bb3", "roomlist-highlights-color": "#17253d", "roomlist-separator-color": "#22324c", "timeline-background-color": "#070d17", "timeline-text-color": "#e8eef7",
        "timeline-text-secondary-color": "#8a9bb3", "timeline-highlights-color": "#101b2e", "reaction-row-button-selected-bg-color": "#17253d"}}


@bp.route("/matrix/element-config.json")
def element_config():
    """Element Web's configuration, fetched by the element container (nothing secret in it): our Matrix server only, the site's brand and colors, and what is switched off."""
    if not enabled():
        abort(404)
    theme = element_theme()
    resp = jsonify({
        "default_server_config": {"m.homeserver": {"base_url": cfg("matrix_public_url").strip().rstrip("/"), "server_name": cfg("matrix_server_name").strip()}},
        "brand": cfg("brand_name") + cfg("brand_accent"), "disable_custom_urls": True, "disable_guests": True, "disable_login_language_selector": True, "disable_3pid_login": True,
        "default_federate": cfg("matrix_federation") == "1", "show_labs_settings": False, "mobile_guide_toast": False, "mobile_builds": {"ios": None, "android": None, "fdroid": None},   # Element X cannot sign in here (it needs password or native OIDC login, this server uses single sign-on), so the web app must not send phones to it
         "sso_redirect_options": {"immediate": True},
        "default_theme": "custom-" + theme["name"], "room_directory": {"servers": [cfg("matrix_server_name").strip()]},
        "integrations_ui_url": "", "integrations_rest_url": "", "integrations_widgets_urls": [], "bug_report_endpoint_url": "", "piwik": False, "enable_presence_by_hs_url": {cfg("matrix_public_url").strip().rstrip("/"): cfg("matrix_presence") == "1"},
        "setting_defaults": {"custom_themes": [theme], "theme": "custom-" + theme["name"], "UIFeature.registration": False, "UIFeature.passwordReset": False, "UIFeature.deactivate": False,
                             "UIFeature.thirdPartyId": False, "UIFeature.identityServer": False, "UIFeature.feedback": False, "UIFeature.voip": cfg("matrix_calls") == "1",
                             "notificationsEnabled": True, "notificationBodyEnabled": True, "audioNotificationsEnabled": True, "UIFeature.widgets": False, "UIFeature.shareQrCode": False, "UIFeature.communities": False, "urlPreviewsEnabled": cfg("matrix_url_previews") == "1"}})
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# ------------------------------------------------------------------ the member's Chat page: Element inside the site's own pages

def framed_page(dm=None, room=None):
    """The Chat page while Matrix is on: the site's menu bar and header around Element Web (the old built-in chat and messages are replaced by this)."""
    from . import access
    if not access.allowed("matrix"):
        abort(404)
    base = cfg("matrix_element_url").strip().rstrip("/")
    tail = ""
    if dm:
        ensure_member(dm)   # a member who has never opened chat gets their chat account now, so there is someone to talk to
        tail = "/#/user/@" + dm.lower() + ":" + cfg("matrix_server_name").strip()
    elif room:
        tail = "/#/room/" + room
    st = status()
    d = st["data"] or {}
    ready = bool(st["ok"] and d.get("synapse_answering") and base)
    why = "" if ready else ("The chat server is not answering. It is started again by itself; please try again in a minute." if st["ok"] and d.get("applied") else "The chat server is being set up. Please try again soon.")
    from flask import make_response
    resp = make_response(render_template("matrix_chat.html", element_url=base, frame_src=base + tail, ready=ready, why=why, matrix_server=cfg("matrix_server_name").strip(), push_on=cfg("push_enabled") == "1", active="chat"))
    origin = base if base.startswith("http") else ""
    allow = f'(self "{origin}")' if origin else "(self)"
    calls = cfg("matrix_calls") == "1"
    resp.headers["Permissions-Policy"] = f"camera={allow if calls else '()'}, microphone={allow if calls else '()'}, display-capture={allow if calls else '()'}, geolocation=(self), payment=()"
    return resp


# ------------------------------------------------------------------ keeping Matrix in step with the site (accounts, blocks, moderators, standing rooms, policy)

def rooms():
    """[{alias, name, topic}] from Settings > Matrix Chat > Standing Rooms."""
    out = []
    for part in cfg("matrix_rooms").split(";"):
        bits = [b.strip() for b in part.split("|")]
        alias = re.sub(r"[^a-z0-9._-]", "", bits[0].lower()) if bits and bits[0] else ""
        if alias:
            out.append({"alias": alias, "name": (bits[1] if len(bits) > 1 and bits[1] else alias.title())[:80], "topic": (bits[2] if len(bits) > 2 else "")[:200]})
    return out


def moderators():
    """User names that hold Moderate Chat (or the permissions that imply it, or are super administrators)."""
    rows = get_db().execute("SELECT DISTINCT lower(u.username) AS n FROM users u JOIN user_roles ur ON ur.user_id = u.id JOIN roles r ON r.id = ur.role_id LEFT JOIN role_permissions rp ON rp.role_id = r.id "
                            "WHERE NOT u.disabled AND (r.is_super_admin OR rp.permission IN ('moderate_chat', 'manage_settings'))").fetchall()
    return sorted(r["n"] for r in rows)


def locked_names():
    """Accounts Matrix must not let in: disabled site accounts and the ones a moderator locked (until the end of the lock)."""
    rows = get_db().execute("SELECT lower(username) AS n FROM users WHERE disabled UNION SELECT lower(u.username) FROM matrix_locks l JOIN users u ON u.id = l.user_id "
                            "WHERE NOT l.lifted AND (l.until IS NULL OR l.until > now())").fetchall()
    return sorted(r["n"] for r in rows)


def is_locked(user_id):
    return get_db().execute("SELECT 1 FROM matrix_locks WHERE user_id=%s AND NOT lifted AND (until IS NULL OR until > now()) LIMIT 1", (user_id,)).fetchone() is not None


def ignores():
    """{user name: [user names they blocked]}: a member's blocks become their Matrix ignore list (a blocked person's messages and invitations are hidden from them)."""
    out = {}
    for r in get_db().execute("SELECT lower(a.username) AS who, lower(b.username) AS blocked FROM pm_blocks p JOIN users a ON a.id = p.blocker_id JOIN users b ON b.id = p.blocked_id").fetchall():
        out.setdefault(r["who"], []).append(r["blocked"])
    return out


def friends_map():
    out = {}
    for r in get_db().execute("SELECT lower(a.username) AS a, lower(b.username) AS b FROM friendships f JOIN users a ON a.id = f.user_a JOIN users b ON b.id = f.user_b WHERE f.status = 'accepted'").fetchall():
        out.setdefault(r["a"], []).append(r["b"])
        out.setdefault(r["b"], []).append(r["a"])
    return out


def contact_policies():
    """{user name: 'friends' or 'nobody'} for the members who chose to hear only from friends (My Account > Community Settings > Who Can Contact Me)."""
    return {r["n"]: r["contact_policy"] for r in get_db().execute("SELECT lower(username) AS n, contact_policy FROM users WHERE contact_policy IN ('friends', 'nobody') AND NOT disabled").fetchall()}


def policy():
    return {"create": cfg("matrix_create_rooms"), "invite": cfg("matrix_invite"), "moderators": moderators(), "friends": friends_map(), "contact": contact_policies(), "blocks": ignores()}


def chat_name(display_name, username, callsign, findable):
    """The name a member carries in chat: their display name, with their call sign after it when they allowed being found by call sign (so a search for the call sign finds them)."""
    base = (display_name or username or "").strip() or username
    cs = (callsign or "").strip().upper()
    if findable and cs and cs.lower() not in base.lower():
        base = f"{base} ({cs})"
    return base[:80]


def members_payload():
    """[{lp, name}] for every active member, so the chat server has an account for each one before they ever open chat (a search finds them and a chat with them can start at once)."""
    if cfg("matrix_precreate_members") != "1":
        return []
    rows = get_db().execute("SELECT lower(username) AS lp, username, display_name, callsign, find_by_callsign FROM users WHERE NOT disabled ORDER BY id").fetchall()
    return [{"lp": r["lp"], "name": chat_name(r["display_name"], r["username"], r["callsign"], r["find_by_callsign"])} for r in rows]


def ensure_member(username):
    """Make sure one member has a chat account right now (the Chat button on a friend or member page): (ok, message)."""
    if cfg("matrix_precreate_members") != "1":
        return True, ""
    r = get_db().execute("SELECT lower(username) AS lp, username, display_name, callsign, find_by_callsign FROM users WHERE lower(username)=lower(%s) AND NOT disabled", (username or "",)).fetchone()
    if not r:
        return False, "No such member."
    return call("/ensure", "POST", {"lp": r["lp"], "name": chat_name(r["display_name"], r["username"], r["callsign"], r["find_by_callsign"])}, timeout=20)


def sync_payload():
    return {"locked": locked_names(), "ignores": ignores(), "moderators": moderators(), "rooms": rooms(), "policy": policy(), "members": members_payload()}


def sync_now():
    """(ok, message): tell the Matrix service what the site holds now."""
    ok, d = call("/sync", "POST", sync_payload(), timeout=180)
    cached("matrix_status", lambda: None, 0)
    if not ok:
        return False, d
    try:
        from .bridge import drop_unowned_links
        drop_unowned_links()
    except Exception as e:
        print("matrix: room link check:", type(e).__name__, str(e)[:120], flush=True)
    errs = d.get("errors") or []
    note = f"Synced: {d.get('provisioned', 0)} accounts made or renamed, {d.get('locked', 0)} locked, {d.get('unlocked', 0)} unlocked, {d.get('ignored', 0)} ignore lists, {d.get('rooms', 0)} standing rooms."
    return (not errs), (note + (" Trouble: " + "; ".join(errs[:3]) if errs else ""))


def nudge():
    """Ask for a sync soon (a block, a friend, a disabled account or a lock changed): the background pass picks it up within seconds."""
    from .settings import set_setting
    try:
        set_setting("matrix_sync_wanted", str(time.time()))
        get_db().commit()
    except Exception:
        get_db().rollback()


def nudge_if_on():
    if replaces_builtin():
        nudge()


def _wanted():
    from .settings import get_setting
    try:
        return float(get_setting("matrix_sync_wanted", "0") or 0)
    except ValueError:
        return 0.0


def start_sync(app):
    """The background pass: gives a fresh Matrix service its settings by itself, then keeps it in step every Settings > Keep Matrix in Step seconds and at once after a change."""
    import random
    import threading

    def loop():
        time.sleep(25 + random.random() * 10)
        last = 0.0
        while True:
            try:
                with app.app_context():
                    if enabled() or cfg_get_raw_enabled():
                        due = time.time() - last >= max(30, cfg_int("matrix_sync_seconds")) or _wanted() > last
                        if due:
                            d = get_db()
                            if d.execute("SELECT pg_try_advisory_xact_lock(727031) AS ok").fetchone()["ok"]:   # both web workers run this loop: one pass at a time
                                last = time.time()
                                ok, st = call("/status", timeout=4)
                                if ok and not st.get("applied") and not missing():
                                    push_config()   # a fresh or rebuilt service gets its settings without anyone pressing Apply
                                    time.sleep(40)
                                ok, st = call("/status", timeout=4)
                                if ok and st.get("synapse_answering"):
                                    sync_now()
                            d.commit()
            except Exception as e:
                print("matrix sync:", type(e).__name__, str(e)[:160], flush=True)
            time.sleep(15)
    threading.Thread(target=loop, name="matrix-sync", daemon=True).start()


def cfg_get_raw_enabled():
    return replaces_builtin()


# ------------------------------------------------------------------ the bridge: the site is a Matrix application service, so it hears the rooms its bot is in and posts as the bot

def as_tokens():
    """(as_token, hs_token): made once, kept sealed. The service is given both when settings are applied."""
    from .settings import get_secret, set_secret
    a, h = get_secret("matrix_as_token"), get_secret("matrix_hs_token")
    if not (a and h):
        import secrets as _s
        a, h = _s.token_urlsafe(40), _s.token_urlsafe(40)
        set_secret("matrix_as_token", a)
        set_secret("matrix_hs_token", h)
        get_db().commit()
    return a, h


def _hs_ok():
    import secrets as _s
    got = request.args.get("access_token", "") or (request.headers.get("Authorization", "")[7:] if request.headers.get("Authorization", "").startswith("Bearer ") else "")
    return bool(got) and _s.compare_digest(got.encode(), as_tokens()[1].encode())


def _lp(user_id):
    return (user_id or "").lstrip("@").split(":")[0].lower()


@bp.route("/_matrix/app/v1/transactions/<txn>", methods=["PUT"])
@csrf.exempt
def as_transaction(txn):
    """Synapse hands over the events of the rooms the bridge bot is in."""
    if not enabled():
        abort(404)
    if not _hs_ok():
        return jsonify({"errcode": "M_FORBIDDEN", "error": "Wrong token"}), 403
    for ev in (request.get_json(silent=True) or {}).get("events", []):
        try:
            handle_event(ev)
        except Exception as e:   # one bad event never makes Synapse send the whole batch again
            get_db().rollback()
            print("matrix bridge event:", type(e).__name__, str(e)[:160], flush=True)
    return jsonify({})


@bp.route("/_matrix/app/v1/users/<path:user_id>")
@bp.route("/_matrix/app/v1/rooms/<path:alias>")
def as_nothing(user_id=None, alias=None):
    if not enabled():
        abort(404)
    if not _hs_ok():
        return jsonify({"errcode": "M_FORBIDDEN"}), 403
    return jsonify({"errcode": "M_NOT_FOUND"}), 404


@bp.route("/_matrix/app/v1/ping", methods=["POST"])
@csrf.exempt
def as_ping():
    if not enabled() or not _hs_ok():
        return jsonify({"errcode": "M_FORBIDDEN"}), 403
    return jsonify({})


def handle_event(ev):
    """One event from a bridged room: a text message from the member who made a radio link goes out over that link, and a message in a standing room with a Discord mirror goes to Discord."""
    from . import bridge
    d = get_db()
    server = cfg("matrix_server_name").strip().lower()
    typ, sender, room = ev.get("type"), (ev.get("sender") or "").lower(), ev.get("room_id") or ""
    if not ev.get("event_id") or d.execute("INSERT INTO matrix_seen_events (event_id) VALUES (%s) ON CONFLICT DO NOTHING RETURNING 1", (ev["event_id"],)).fetchone() is None:
        d.commit()
        return
    d.execute("DELETE FROM matrix_seen_events WHERE at < now() - interval '2 days'")
    d.commit()
    bot = "@arksignal-bridge:" + server
    if sender == bot:
        return
    if typ == "m.room.member" and ev.get("state_key", "").lower() == bot and (ev.get("content") or {}).get("membership") == "invite":
        call("/bridge/join", "POST", {"room_id": room, "member": _lp(sender)}, timeout=30)   # anyone who invites the bot is answered (it joins, and says nothing until a link or a mirror exists)
        return
    if typ != "m.room.message":
        return
    c = ev.get("content") or {}
    if c.get("msgtype") not in ("m.text", "m.emote"):
        return
    body = (c.get("body") or "").strip()
    if not body:
        return
    me = d.execute("SELECT id, username, display_name FROM users WHERE lower(username)=%s AND NOT disabled", (_lp(sender),)).fetchone()
    if me is None:
        return
    bridge.forward_room(room, me["id"], body)
    mirror = d.execute("SELECT webhook FROM matrix_mirrors WHERE room_id=%s", (room,)).fetchone()
    if mirror is not None:
        from . import vault, webhooks
        webhooks.send(vault.unseal(mirror["webhook"]), "discord", (me["display_name"] or me["username"]) + ": " + body[:1500], [], cfg("brand_name") + cfg("brand_accent"))
        audit("matrix.mirror", target=room, detail="message sent to Discord", actor_name=me["username"])


def rooms_of(username):
    """(ok, rooms or message): the rooms a member is in, each with its name and whether it is encrypted."""
    ok, d = call("/bridge/rooms", "POST", {"member": username.lower()}, timeout=60)
    if not ok:
        return False, d
    if not d.get("ok"):
        return False, d.get("error", "The Matrix service could not list the rooms.")
    return True, d["rooms"]


def bridge_join(room_id, username):
    ok, d = call("/bridge/join", "POST", {"room_id": room_id, "member": username.lower()}, timeout=60)
    return (ok and d.get("ok", False)), ("" if ok and d.get("ok") else (d if isinstance(d, str) else d.get("error", "")))


def bridge_leave(room_id):
    call("/bridge/leave", "POST", {"room_id": room_id}, timeout=30)


def post_to_room(room_id, text):
    ok, d = call("/bridge/post", "POST", {"room_id": room_id, "text": text}, timeout=30)
    return bool(ok and d.get("ok"))


@bp.route("/admin/matrix/mirror", methods=["POST"])
@login_required
@permission_required("manage_settings")
def admin_mirror():
    from . import vault, webhooks
    alias = re.sub(r"[^a-z0-9._-]", "", (request.form.get("alias") or "").lower())
    url = (request.form.get("webhook") or "").strip()
    if alias not in [r["alias"] for r in rooms()]:
        return _admin_resp(False, "Choose one of the standing rooms.")
    clean, err = webhooks.check_url(url)
    if err or not clean:
        return _admin_resp(False, err or "Paste the Discord webhook address.")
    ok, d = call("/room-id", "POST", {"alias": alias}, timeout=20)
    rid = d.get("room_id", "") if ok else ""
    if not rid:
        return _admin_resp(False, "That room does not exist on the Matrix server yet (press Sync Now first).")
    joined, why = bridge_join(rid, "arksignal-admin")
    if not joined:
        return _admin_resp(False, "The bridge could not join the room: " + why)
    db = get_db()
    db.execute("INSERT INTO matrix_mirrors (alias, room_id, webhook, by_name) VALUES (%s,%s,%s,%s) ON CONFLICT (alias) DO UPDATE SET room_id=EXCLUDED.room_id, webhook=EXCLUDED.webhook, by_name=EXCLUDED.by_name",
               (alias, rid, vault.seal(clean), current_user.username))
    db.commit()
    audit("matrix.mirror_set", target="#" + alias, detail="Discord mirror saved (the address is sealed)")
    return _admin_resp(True, f"What is written in #{alias} is now also sent to that Discord channel (one way). The address is stored sealed.")


@bp.route("/admin/matrix/mirror/<alias>/delete", methods=["POST"])
@login_required
@permission_required("manage_settings")
def admin_mirror_delete(alias):
    db = get_db()
    row = db.execute("DELETE FROM matrix_mirrors WHERE alias=%s RETURNING room_id", (alias,)).fetchone()
    db.commit()
    if row is None:
        return _admin_resp(False, "There is no mirror for that room.")
    if not db.execute("SELECT 1 FROM bridge_links WHERE room_id=%s", (row["room_id"],)).fetchone():
        bridge_leave(row["room_id"])
    audit("matrix.mirror_removed", target="#" + alias)
    return _admin_resp(True, "The Discord mirror is removed.")


# ------------------------------------------------------------------ moderation: reports, locks

def reports():
    """(ok, rows or message): the reports members made in Element, newest first, without the ones a moderator already handled."""
    ok, d = call("/reports", timeout=15)
    if not ok:
        return False, d
    if not d.get("ok"):
        return False, d.get("error", "The Matrix service could not list the reports.")
    done = {r["report_id"] for r in get_db().execute("SELECT report_id FROM matrix_report_state").fetchall()}
    rows = []
    for r in d.get("reports", []):
        if r["id"] in done:
            continue
        r["ts"] = datetime.fromtimestamp((r.get("ts") or 0) / 1000, timezone.utc) if r.get("ts") else None
        r["sender_name"] = (r.get("sender") or "").split(":")[0].lstrip("@")
        r["reporter_name"] = (r.get("reporter") or "").split(":")[0].lstrip("@")
        rows.append(r)
    return True, rows


def _admin_resp(ok, message):
    return action_response(ok, message, redirect_endpoint="matrix.admin_page", fragment_template="partials/_admin_matrix.html", fragment_context=_ctx())


@bp.route("/admin/matrix/sync", methods=["POST"])
@login_required
@permission_required("moderate_chat")
def admin_sync():
    ok, msg = sync_now()
    audit("matrix.sync", target="Matrix service", detail=msg[:200])
    return _admin_resp(ok, msg)


@bp.route("/admin/matrix/lock", methods=["POST"])
@login_required
@permission_required("moderate_chat")
def admin_lock():
    who = (request.form.get("username") or "").strip()
    d = get_db()
    u = d.execute("SELECT id, username FROM users WHERE lower(username)=lower(%s) OR lower(email)=lower(%s)", (who, who)).fetchone()
    if u is None:
        return _admin_resp(False, "No member has that user name or email address.")
    if u["id"] == current_user.id:
        return _admin_resp(False, "You cannot lock your own account.")
    hours = request.form.get("hours", "")
    until = f"{int(hours)} hours" if hours.isdigit() and 0 < int(hours) <= 24 * 365 else None
    reason = " ".join((request.form.get("reason") or "").split())[:200]
    d.execute("INSERT INTO matrix_locks (user_id, reason, by_name, until) VALUES (%s,%s,%s," + ("now() + %s::interval" if until else "NULL") + ")", [u["id"], reason, current_user.username] + ([until] if until else []))
    d.commit()
    audit("matrix.lock", target=u["username"], detail=f"{until or 'until lifted'}: {reason}")
    ok, msg = sync_now()
    return _admin_resp(True, f"{u['username']}'s chat account is locked ({'for ' + until if until else 'until lifted'}); they are signed out of chat." + ("" if ok else " The server could not be told yet: " + msg))


@bp.route("/admin/matrix/unlock/<int:lid>", methods=["POST"])
@login_required
@permission_required("moderate_chat")
def admin_unlock(lid):
    d = get_db()
    row = d.execute("UPDATE matrix_locks SET lifted=TRUE, lifted_by=%s WHERE id=%s AND NOT lifted RETURNING user_id", (current_user.username, lid)).fetchone()
    d.commit()
    if row is None:
        return _admin_resp(False, "That lock is already lifted.")
    u = d.execute("SELECT username FROM users WHERE id=%s", (row["user_id"],)).fetchone()
    audit("matrix.unlock", target=u["username"] if u else str(row["user_id"]))
    sync_now()
    return _admin_resp(True, "The lock is lifted.")


@bp.route("/admin/matrix/reports/<int:rid>/<act>", methods=["POST"])
@login_required
@permission_required("moderate_chat")
def admin_report(rid, act):
    """Handle a report: dismiss it, or remove the reported message from the room and close it."""
    if act not in ("dismiss", "delete"):
        abort(400)
    ok, rows = reports()
    if not ok:
        return _admin_resp(False, rows)
    r = next((x for x in rows if x["id"] == rid), None)
    if r is None:
        return _admin_resp(False, "That report is already handled.")
    if act == "delete":
        ok, d = call("/redact", "POST", {"room_id": r["room_id"], "event_id": r["event_id"], "reason": "Removed by a moderator after a report"}, timeout=30)
        if not ok or not d.get("ok"):
            return _admin_resp(False, "The message could not be removed: " + (d if isinstance(d, str) else d.get("error", "")))
    db = get_db()
    db.execute("INSERT INTO matrix_report_state (report_id, status, by_name) VALUES (%s,%s,%s) ON CONFLICT (report_id) DO UPDATE SET status=EXCLUDED.status, by_name=EXCLUDED.by_name, at=now()",
               (rid, "removed" if act == "delete" else "dismissed", current_user.username))
    db.commit()
    audit("matrix.report_" + act, target=f"report #{rid}", detail=f"{r['sender']} in {r['room_name'] or r['room_id']}")
    return _admin_resp(True, "The message is removed from the room and the report closed." if act == "delete" else "The report is closed.")


# ------------------------------------------------------------------ Admin

def _ctx():
    st = status()
    d = st["data"] or {}
    return {"st": st, "d": d, "gaps": missing(), "on": enabled(), "server_name": cfg("matrix_server_name"), "public_url": cfg("matrix_public_url"), "element_url": cfg("matrix_element_url"),
            "client_id": cfg("matrix_client_id"), "returns": oidcprovider.redirect_uris(), "secret_made": bool(oidcprovider.client_secret()), "issuer": oidcprovider.issuer_for_service(), "active": "matrix",
            "federation": cfg("matrix_federation") == "1", **_moderation_ctx(st, d)}


def _moderation_ctx(st, d):
    out = {"reports": [], "reports_note": "", "locks": [], "rooms": rooms(), "sync": d.get("last_sync"), "sync_iso": datetime.fromtimestamp((d.get("last_sync") or {}).get("at", 0), timezone.utc).isoformat(), "counts": {}, "creating": cfg("matrix_create_rooms"), "inviting": cfg("matrix_invite")}
    db = get_db()
    out["mirrors"] = db.execute("SELECT alias, by_name, created FROM matrix_mirrors ORDER BY alias").fetchall()
    out["locks"] = db.execute("SELECT l.*, u.username FROM matrix_locks l JOIN users u ON u.id = l.user_id WHERE NOT l.lifted AND (l.until IS NULL OR l.until > now()) ORDER BY l.created DESC").fetchall()
    if st["ok"] and d.get("synapse_answering"):
        ok, rows = reports()
        out["reports"], out["reports_note"] = (rows, "") if ok else ([], rows)
        ok, c = call("/stats", timeout=8)
        out["counts"] = c if ok and c.get("ok") else {}
    return out


@bp.route("/admin/matrix")
@login_required
@permission_required("moderate_chat")
def admin_page():
    return render_template("admin_matrix.html", **_ctx())


@bp.route("/admin/matrix/apply", methods=["POST"])
@login_required
@permission_required("manage_settings")
def admin_apply():
    ok, msg = push_config()
    audit("matrix.apply", target="Matrix service", detail=("sent" if ok else msg[:200]))
    return action_response(ok, msg, redirect_endpoint="matrix.admin_page", fragment_template="partials/_admin_matrix.html", fragment_context=_ctx())


@bp.route("/admin/matrix/rotate", methods=["POST"])
@login_required
@permission_required("manage_settings")
def admin_rotate():
    """A new client secret, sent to the service at once (the old one stops working)."""
    import secrets as _s
    from .settings import set_secret
    set_secret("oidc_client_secret", _s.token_urlsafe(40))
    get_db().commit()
    audit("oidc.secret_made", target="Matrix client secret")
    ok, msg = push_config()
    return action_response(ok, "A new sign-in secret is made. " + msg if ok else "A new sign-in secret is made but could not be sent: " + msg, redirect_endpoint="matrix.admin_page",
                           fragment_template="partials/_admin_matrix.html", fragment_context=_ctx())
