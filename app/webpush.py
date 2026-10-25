"""Web Push for the site: a notification on a member's phone or computer when the chat is closed.

How it works. A member turns notifications on from this site (My Account > Community Settings). Their browser makes a push subscription (an address at the browser maker's push service and two keys) and gives it to
the site; the site registers an HTTP pusher for the member on the Matrix server that points at /_matrix/push/v1/notify here. When the member is mentioned or messaged, the Matrix server (which decides what is worth a
notification by the member's own notification settings in Element, room by room) posts to that address; the site encrypts a short message for the subscription (RFC 8291, aes128gcm) and sends it to the push service
with its own VAPID identity (RFC 8292); the browser's service worker (/sw.js) shows it, and a click opens the chat.

Kept on purpose: nothing is built on a push library (the encryption is the standard one, written with the cryptography package already in the site, and checked against an independent implementation in the tests); a
subscription's keys are sealed in the database and the pusher's secret key is stored only as a keyed hash; the site only talks to push services on a list (Settings > Matrix Chat > Push Services), because a
subscription's address is typed by a browser and must never make the server reach an address of someone's choosing; the text of a message is included only when the room is not end-to-end encrypted (the Matrix
server does not have it otherwise), and an encrypted room only says that there is a new message; a push service that says the subscription is gone (404 or 410) has it removed here and the Matrix pusher with it."""
import base64
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import Blueprint, Response, abort, jsonify, request
from flask_login import current_user, login_required

from . import csrf, vault
from .audit import audit
from .db import get_db
from .settings import cfg, cfg_int, get_secret, set_secret

bp = Blueprint("webpush", __name__)

APP_ID = "net.arksignal.web"


def on():
    return cfg("push_enabled") == "1"


def b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def b64ud(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ------------------------------------------------------------------ the site's own push identity (VAPID)

def _vapid_private():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    pem = get_secret("webpush_vapid_private")
    if not pem:
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        set_secret("webpush_vapid_private", pem)
        get_db().commit()
        return key
    return serialization.load_pem_private_key(pem.encode(), password=None)


def public_key():
    """The VAPID public key as the browser wants it: the uncompressed point, base64url."""
    from cryptography.hazmat.primitives import serialization
    return b64u(_vapid_private().public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))


def vapid_authorization(endpoint):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    u = urllib.parse.urlparse(endpoint)
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": cfg("push_contact")}, separators=(",", ":")).encode())
    r, s = decode_dss_signature(_vapid_private().sign((head + "." + body).encode(), ec.ECDSA(hashes.SHA256())))
    return f"vapid t={head}.{body}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}, k={public_key()}"


# ------------------------------------------------------------------ the encryption (RFC 8291, one aes128gcm record of RFC 8188)

def _hkdf(salt, ikm, info, n):
    import hashlib
    import hmac
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:n]


def encrypt(plaintext, p256dh, auth):
    """The body to post to the push service for a subscription (keys base64url)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    ua_pub = b64ud(p256dh)
    auth_secret = b64ud(auth)
    eph = ec.generate_private_key(ec.SECP256R1())
    as_pub = eph.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = eph.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub))
    ikm = _hkdf(auth_secret, shared, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    salt = os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    record = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    return salt + (4096).to_bytes(4, "big") + bytes([len(as_pub)]) + as_pub + record


# ------------------------------------------------------------------ subscriptions

def endpoint_allowed(url):
    u = urllib.parse.urlparse(url or "")
    if u.scheme not in ("https", "http") or not u.hostname or len(url) > 1000:
        return False
    host = u.hostname.lower()
    for entry in [x.strip().lower() for x in cfg("push_endpoint_hosts").split(",") if x.strip()]:
        if host == entry or host.endswith("." + entry.lstrip(".")):
            return u.scheme == "https" or host in ("127.0.0.1", "localhost")
    return False


def send(sub, payload, ttl=None, urgency="normal"):
    """(ok, status): one encrypted notification to one subscription row. Never raises."""
    try:
        body = encrypt(json.dumps(payload, separators=(",", ":")).encode()[:3000], vault.unseal(sub["p256dh_sealed"]), vault.unseal(sub["auth_sealed"]))
        req = urllib.request.Request(sub["endpoint"], data=body, method="POST", headers={"Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(ttl or cfg_int("push_ttl_seconds")),
                                                                                         "Urgency": urgency, "Authorization": vapid_authorization(sub["endpoint"])})
        with urllib.request.urlopen(req, timeout=cfg_int("push_timeout_seconds")) as r:
            return 200 <= r.status < 300, r.status
    except urllib.error.HTTPError as e:
        return False, e.code
    except Exception:
        return False, 0


def notify_member(user_id, title, body, url, tag, urgency="normal"):
    """Send one notification to every device a member has turned notifications on for (a device its push service says is gone is removed). Returns the number of devices reached. Never raises."""
    try:
        if not on():
            return 0
        d = get_db()
        reached = 0
        for s in d.execute("SELECT * FROM push_subscriptions WHERE user_id=%s", (user_id,)).fetchall():
            ok, status = send(s, {"title": title, "body": body, "url": url, "tag": tag}, urgency=urgency)
            if ok:
                reached += 1
                d.execute("UPDATE push_subscriptions SET last_ok=now() WHERE id=%s", (s["id"],))
            elif status in (404, 410):
                d.execute("DELETE FROM push_subscriptions WHERE id=%s", (s["id"],))
        d.commit()
        return reached
    except Exception:
        return 0


def _pusher(member, pushkey, delete=False, device=""):
    from . import matrix
    if not matrix.replaces_builtin():
        return True
    ok, d = matrix.call("/pusher", "POST", {"member": member.lower(), "pushkey": pushkey, "url": cfg("matrix_push_url"), "device": device[:60], "app_id": APP_ID, "delete": delete}, timeout=30)
    return bool(ok and d.get("ok"))


def _json(ok, message, status=None, **extra):
    return jsonify({"ok": ok, "message": message, **extra}), (200 if ok else (status or 400))


@bp.route("/sw.js")
def service_worker():
    """The site's service worker: shows pushed notifications and opens the chat when one is clicked."""
    js = open(os.path.join(os.path.dirname(__file__), "static", "sw.js"), encoding="utf-8").read()
    r = Response(js, mimetype="text/javascript")
    r.headers["Cache-Control"] = "no-cache"
    r.headers["Service-Worker-Allowed"] = "/"
    return r


@bp.route("/push/key")
def key():
    if not on():
        abort(404)
    return jsonify({"key": public_key()})


@bp.route("/my/push/state")
@login_required
def state():
    if not on():
        abort(404)
    rows = get_db().execute("SELECT id, endpoint, device, created, last_ok FROM push_subscriptions WHERE user_id=%s ORDER BY created", (current_user.id,)).fetchall()
    return jsonify({"on": True, "devices": [{"id": r["id"], "endpoint": r["endpoint"], "device": r["device"], "created": r["created"].isoformat(), "last_ok": r["last_ok"].isoformat() if r["last_ok"] else None} for r in rows]})


@bp.route("/my/push/subscribe", methods=["POST"])
@login_required
def subscribe():
    if not on():
        abort(404)
    j = request.get_json(silent=True) or {}
    sub = j.get("subscription") or {}
    endpoint, keys = (sub.get("endpoint") or "").strip(), sub.get("keys") or {}
    if not endpoint_allowed(endpoint):
        return _json(False, "This browser's notification service is not one the site is set up to use.")
    try:
        if len(b64ud(keys.get("p256dh", ""))) != 65 or len(b64ud(keys.get("auth", ""))) < 12:
            raise ValueError
    except Exception:
        return _json(False, "The browser's subscription is not complete.")
    d = get_db()
    if d.execute("SELECT COUNT(*) AS n FROM push_subscriptions WHERE user_id=%s", (current_user.id,)).fetchone()["n"] >= cfg_int("push_devices_per_member") and \
            not d.execute("SELECT 1 FROM push_subscriptions WHERE endpoint=%s AND user_id=%s", (endpoint, current_user.id)).fetchone():
        return _json(False, f"You can have notifications on {cfg_int('push_devices_per_member')} devices at most. Turn one off first.")
    device = " ".join((j.get("device") or request.headers.get("User-Agent", "")).split())[:120]
    pushkey = secrets.token_urlsafe(32)
    old = d.execute("SELECT id FROM push_subscriptions WHERE endpoint=%s", (endpoint,)).fetchone()
    if old:   # the same browser again (or a browser that changed hands): the old entry is replaced
        d.execute("DELETE FROM push_subscriptions WHERE id=%s", (old["id"],))
    d.execute("INSERT INTO push_subscriptions (user_id, endpoint, p256dh_sealed, auth_sealed, pushkey_hash, device) VALUES (%s,%s,%s,%s,%s,%s)",
              (current_user.id, endpoint, vault.seal(keys["p256dh"]), vault.seal(keys["auth"]), vault.keyed_hash(pushkey, "pushkey"), device))
    d.commit()
    pusher = _pusher(current_user.username, pushkey, device=device)
    audit("push.subscribe", target=current_user.username, detail=device[:80])
    return _json(True, "Notifications are on for this device." + ("" if pusher else " (The chat server could not be told yet; chat notifications start once it can.)"))


@bp.route("/my/push/unsubscribe", methods=["POST"])
@login_required
def unsubscribe():
    if not on():
        abort(404)
    j = request.get_json(silent=True) or {}
    d = get_db()
    rows = d.execute("DELETE FROM push_subscriptions WHERE user_id=%s AND (endpoint=%s OR %s = '') RETURNING id", (current_user.id, j.get("endpoint", ""), j.get("endpoint", ""))).fetchall()
    d.commit()
    # the Matrix pusher is keyed by a secret the site no longer holds in the clear: pushers of this site are cleared for the member and made again for the devices that remain
    resync_pushers(current_user.id, current_user.username)
    audit("push.unsubscribe", target=current_user.username, detail=f"{len(rows)} device(s)")
    return _json(True, "Notifications are off for this device." if rows else "Nothing was on.")


def resync_pushers(user_id, username):
    from . import matrix
    if not matrix.replaces_builtin():
        return
    ok, d = matrix.call("/pusher", "POST", {"member": username.lower(), "clear": True, "app_id": APP_ID}, timeout=30)
    d2 = get_db()
    for r in d2.execute("SELECT id, device FROM push_subscriptions WHERE user_id=%s", (user_id,)).fetchall():
        pushkey = secrets.token_urlsafe(32)   # a fresh key for each remaining device (the old one is only kept as a hash)
        d2.execute("UPDATE push_subscriptions SET pushkey_hash=%s WHERE id=%s", (vault.keyed_hash(pushkey, "pushkey"), r["id"]))
        d2.commit()
        _pusher(username, pushkey, device=r["device"])


@bp.route("/my/push/test", methods=["POST"])
@login_required
def test():
    if not on():
        abort(404)
    rows = get_db().execute("SELECT * FROM push_subscriptions WHERE user_id=%s", (current_user.id,)).fetchall()
    if not rows:
        return _json(False, "Turn notifications on for a device first.")
    sent = sum(1 for r in rows if send(r, {"title": cfg("brand_name") + cfg("brand_accent"), "body": "Notifications work on this device.", "url": "/my/community", "tag": "test"})[0])
    return _json(bool(sent), f"Sent to {sent} of {len(rows)} device{'s' if len(rows) != 1 else ''}." if sent else "The notification service did not take it. Try turning notifications off and on again.")


# ------------------------------------------------------------------ the push gateway the Matrix server calls

def describe(n):
    """(title, body, url) for one notification the Matrix server sent."""
    sender = n.get("sender_display_name") or (n.get("sender") or "").lstrip("@").split(":")[0] or "Someone"
    room = n.get("room_name") or n.get("room_alias") or ""
    c = n.get("content") or {}
    if n.get("type") == "m.room.member":
        return "Chat invitation", f"{sender} invited you to {room or 'a chat'}.", "/chat/"
    text = (c.get("body") or "").strip() if c.get("msgtype") in ("m.text", "m.notice", "m.emote") else ""
    if not text and c.get("msgtype") in ("m.image", "m.video", "m.audio", "m.file"):
        text = {"m.image": "Sent a picture.", "m.video": "Sent a video.", "m.audio": "Sent a voice message or audio.", "m.file": "Sent a file."}[c["msgtype"]]
    title = f"{sender} in {room}" if room else sender
    url = "/chat/?room=" + urllib.parse.quote(n.get("room_id") or "", safe="") if n.get("room_id") else "/chat/"
    return title, (text[:140] or "New message."), url


@bp.route("/_matrix/push/v1/notify", methods=["POST"])
@csrf.exempt
def gateway():
    """The Matrix push gateway interface: the server posts a notification and the devices (our own pusher keys) it is for; the answer lists the keys that no longer work."""
    if not on():
        abort(404)
    n = (request.get_json(silent=True) or {}).get("notification") or {}
    rejected = []
    d = get_db()
    title, body, url = describe(n)
    for dev in n.get("devices") or []:
        if dev.get("app_id") != APP_ID:
            continue
        pushkey = dev.get("pushkey") or ""
        sub = d.execute("SELECT * FROM push_subscriptions WHERE pushkey_hash=%s", (vault.keyed_hash(pushkey, "pushkey"),)).fetchone() if pushkey else None
        if sub is None:
            rejected.append(pushkey)
            continue
        from . import pushkinds
        if not pushkinds.wants(sub["user_id"], "chat"):   # the member turned chat notifications off here (or the site does not offer them)
            continue
        unread = (n.get("counts") or {}).get("unread")
        ok, status = send(sub, {"title": title, "body": body, "url": url, "tag": n.get("room_id") or "chat", "badge": unread}, urgency="high" if n.get("prio") == "high" else "normal")
        if ok:
            d.execute("UPDATE push_subscriptions SET last_ok=now(), failures=0 WHERE id=%s", (sub["id"],))
        elif status in (404, 410):   # the push service says the subscription is gone
            d.execute("DELETE FROM push_subscriptions WHERE id=%s", (sub["id"],))
            rejected.append(pushkey)
        else:
            d.execute("UPDATE push_subscriptions SET failures=failures+1 WHERE id=%s", (sub["id"],))
        d.commit()
    return jsonify({"rejected": rejected})
