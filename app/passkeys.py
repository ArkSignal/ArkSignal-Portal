"""Passkeys: sign in with a fingerprint, face, screen lock or security key instead of a password (WebAuthn).

A passkey is a key pair the person's device makes for this site: the private half never leaves the device (and may sync through the platform's own keychain), this site keeps only the public half. Signing in
asks the device to sign a one-time challenge; a good signature, made after the person unlocked the device (user verification), proves who they are, so a passkey sign-in stands in for the password and the
second step together. Only the checks WebAuthn needs are made here, with the `cryptography` library already in the portal: no attestation is asked for (`none`), and the signature, the challenge, the origin,
the site's address (rp id) and the use counter of every sign-in are verified.

Members add and remove their passkeys on My Account > Passkeys; a passkey is made on the person's own device, so an administrator cannot add one for them, but can see how many a member has and remove them
(for a lost device) on the member's page."""
import base64
import hashlib
import json
import secrets
import struct
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from flask import Blueprint, abort, jsonify, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user

from .action_response import action_response
from .audit import audit
from .clientip import client_ip
from .db import get_db
from .models import User, log_login
from .settings import cfg, cfg_int

bp = Blueprint("passkeys", __name__)

ALGS = [-7, -257, -8]   # ES256, RS256, EdDSA: what a device may use


# ------------------------------------------------------------------ small encodings

def b64e(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64d(text):
    text = str(text or "")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def cbor_decode(buf, i=0):
    """A small CBOR reader (what WebAuthn uses): integers, byte and text strings, arrays, maps, true, false, null. Returns (value, next index)."""
    first = buf[i]
    major, info = first >> 5, first & 31
    i += 1
    if info < 24:
        n = info
    elif info in (24, 25, 26, 27):
        size = {24: 1, 25: 2, 26: 4, 27: 8}[info]
        n = int.from_bytes(buf[i:i + size], "big")
        i += size
    else:
        if major == 7 and info in (20, 21, 22):
            return {20: False, 21: True, 22: None}[info], i
        raise ValueError("unsupported CBOR")
    if major == 0:
        return n, i
    if major == 1:
        return -1 - n, i
    if major == 2:
        return bytes(buf[i:i + n]), i + n
    if major == 3:
        return bytes(buf[i:i + n]).decode("utf-8"), i + n
    if major == 4:
        out = []
        for _ in range(n):
            v, i = cbor_decode(buf, i)
            out.append(v)
        return out, i
    if major == 5:
        out = {}
        for _ in range(n):
            k, i = cbor_decode(buf, i)
            v, i = cbor_decode(buf, i)
            out[k] = v
        return out, i
    raise ValueError("unsupported CBOR")


# ------------------------------------------------------------------ the checks

def rp_id():
    return request.host.split(":")[0]


def origin():
    return request.url_root.rstrip("/")


def public_key_from_cose(cose):
    """A cryptography public key from a COSE key (ES256 on P-256, RS256 or EdDSA)."""
    kty, alg = cose.get(1), cose.get(3)
    if kty == 2 and alg == -7 and cose.get(-1) == 1:
        return ec.EllipticCurvePublicNumbers(int.from_bytes(cose[-2], "big"), int.from_bytes(cose[-3], "big"), ec.SECP256R1()).public_key()
    if kty == 3 and alg == -257:
        return rsa.RSAPublicNumbers(int.from_bytes(cose[-2], "big"), int.from_bytes(cose[-1], "big")).public_key()
    if kty == 1 and alg == -8 and cose.get(-1) == 6:
        return ed25519.Ed25519PublicKey.from_public_bytes(cose[-2])
    raise ValueError("that kind of key is not supported")


def verify_signature(cose, signature, data):
    key = public_key_from_cose(cose)
    try:
        if isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(signature, data, ec.ECDSA(hashes.SHA256()))
        elif isinstance(key, rsa.RSAPublicKey):
            key.verify(signature, data, padding.PKCS1v15(), hashes.SHA256())
        else:
            key.verify(signature, data)
    except InvalidSignature:
        return False
    return True


def _client_data(raw, kind, challenge):
    """The clientDataJSON of a ceremony, checked: right type, the challenge we issued and this site's origin. Returns (data, error)."""
    try:
        cd = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None, "The browser's answer could not be read."
    if cd.get("type") != kind:
        return None, "The browser's answer was for something else."
    if not secrets.compare_digest(str(cd.get("challenge", "")), challenge):
        return None, "That sign-in attempt was not the one this site asked for. Try again."
    if cd.get("origin") != origin():
        return None, "The answer came from a different address than this site's."
    return cd, None


def _take_challenge(kind):
    """The challenge issued to this browser (once only), or None when there is none or it is old."""
    c = session.pop("pk_chal", None)
    if not c or c.get("kind") != kind or time.time() - c.get("at", 0) > cfg_int("passkeys_challenge_seconds"):
        return None
    return c


def parse_auth_data(ad):
    """(rp id hash, flags, sign count, attested credential data or None)."""
    if len(ad) < 37:
        raise ValueError("short")
    rp_hash, flags, count = ad[:32], ad[32], struct.unpack(">I", ad[33:37])[0]
    att = None
    if flags & 0x40:
        aaguid = ad[37:53]
        n = struct.unpack(">H", ad[53:55])[0]
        cred_id = ad[55:55 + n]
        cose, _end = cbor_decode(ad, 55 + n)
        att = {"aaguid": aaguid, "id": cred_id, "cose": cose, "cose_raw": ad[55 + n:_end]}
    return rp_hash, flags, count, att


# ------------------------------------------------------------------ the data

def passkeys_for(user_id):
    return get_db().execute("SELECT id, name, created_at, last_used, transports, backed_up, sign_count FROM passkeys WHERE user_id=%s ORDER BY created_at", (user_id,)).fetchall()


def _handle_for(user_id):
    d = get_db()
    h = d.execute("SELECT passkey_handle FROM users WHERE id=%s", (user_id,)).fetchone()["passkey_handle"]
    if not h:
        h = b64e(secrets.token_bytes(32))
        d.execute("UPDATE users SET passkey_handle=%s WHERE id=%s", (h, user_id))
        d.commit()
    return h


def _on():
    if cfg("passkeys_enabled") != "1":
        abort(404)


def _ctx():
    return {"passkeys": passkeys_for(current_user.id), "per_user": cfg_int("passkeys_per_user")}


def _response(ok, message):
    return action_response(ok, message, redirect_endpoint="passkeys.my_page", fragment_template="partials/_passkeys_list.html", fragment_context=_ctx())


# ------------------------------------------------------------------ My Account > Passkeys

@bp.route("/my/passkeys")
@login_required
def my_page():
    _on()
    return render_template("my_passkeys.html", **_ctx())


@bp.route("/my/passkeys/options", methods=["POST"])
@login_required
def register_options():
    """The options the browser needs to make a new passkey for this person."""
    _on()
    if len(passkeys_for(current_user.id)) >= cfg_int("passkeys_per_user"):
        return jsonify({"ok": False, "message": f"You can have {cfg_int('passkeys_per_user')} passkeys. Remove one to add another."}), 400
    chal = b64e(secrets.token_bytes(32))
    session["pk_chal"] = {"c": chal, "at": time.time(), "kind": "create", "uid": current_user.id}
    have = get_db().execute("SELECT credential_id FROM passkeys WHERE user_id=%s", (current_user.id,)).fetchall()
    return jsonify({"ok": True, "options": {
        "challenge": chal, "rp": {"id": rp_id(), "name": cfg("site_name")},
        "user": {"id": _handle_for(current_user.id), "name": current_user.username, "displayName": current_user.label},
        "pubKeyCredParams": [{"type": "public-key", "alg": a} for a in ALGS], "timeout": cfg_int("passkeys_challenge_seconds") * 1000, "attestation": "none",
        "authenticatorSelection": {"residentKey": "required", "requireResidentKey": True, "userVerification": "required" if cfg("passkeys_require_uv") == "1" else "preferred"},
        "excludeCredentials": [{"type": "public-key", "id": r["credential_id"]} for r in have]}})


@bp.route("/my/passkeys/register", methods=["POST"])
@login_required
def register():
    """The browser made a passkey: check it and keep its public half."""
    _on()
    chal = _take_challenge("create")
    body = request.get_json(silent=True) or {}
    if not chal or chal.get("uid") != current_user.id:
        return _response(False, "That took too long or was not started here. Choose Add a Passkey and try again.")
    try:
        cd, err = _client_data(b64d(body["clientDataJSON"]), "webauthn.create", chal["c"])
        if err:
            return _response(False, err)
        att_obj, _e = cbor_decode(b64d(body["attestationObject"]))
        rp_hash, flags, count, att = parse_auth_data(att_obj["authData"])
        if rp_hash != hashlib.sha256(rp_id().encode()).digest():
            return _response(False, "The passkey was made for a different site address.")
        if not flags & 0x01 or att is None:
            return _response(False, "The device did not confirm that you were there. Try again.")
        if cfg("passkeys_require_uv") == "1" and not flags & 0x04:
            return _response(False, "This site needs the device to check it is you (fingerprint, face or PIN). Try again with a device that does.")
        public_key_from_cose(att["cose"])
    except (KeyError, ValueError, IndexError, TypeError):
        return _response(False, "The passkey could not be read. Try again, or try another device.")
    cred_id = b64e(att["id"])
    d = get_db()
    if d.execute("SELECT 1 FROM passkeys WHERE credential_id=%s", (cred_id,)).fetchone():
        return _response(False, "That passkey is already added.")
    name = " ".join(str(body.get("name") or "").split())[:60] or "My passkey"
    d.execute("INSERT INTO passkeys (user_id, credential_id, public_key, sign_count, name, transports, aaguid, backed_up) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
              (current_user.id, cred_id, att["cose_raw"], count, name, ",".join(str(t) for t in (body.get("transports") or []) if str(t).isalpha())[:80], att["aaguid"].hex(), bool(flags & 0x10)))
    d.commit()
    audit("passkey.add", target=current_user.username, detail=name)
    return _response(True, f"{name} is added. You can now sign in with it.")


@bp.route("/my/passkeys/<int:pid>/rename", methods=["POST"])
@login_required
def rename(pid):
    _on()
    d = get_db()
    row = d.execute("SELECT name FROM passkeys WHERE id=%s AND user_id=%s", (pid, current_user.id)).fetchone()
    if row is None:
        abort(404)
    name = " ".join((request.form.get("name") or "").split())[:60]
    if not name:
        return _response(False, "Give the passkey a name.")
    d.execute("UPDATE passkeys SET name=%s WHERE id=%s", (name, pid))
    d.commit()
    audit("passkey.rename", target=current_user.username, detail=f"{row['name']} to {name}")
    return _response(True, "Renamed.")


@bp.route("/my/passkeys/<int:pid>/remove", methods=["POST"])
@login_required
def remove(pid):
    _on()
    d = get_db()
    row = d.execute("SELECT name FROM passkeys WHERE id=%s AND user_id=%s", (pid, current_user.id)).fetchone()
    if row is None:
        abort(404)
    d.execute("DELETE FROM passkeys WHERE id=%s", (pid,))
    d.commit()
    audit("passkey.remove", target=current_user.username, detail=row["name"])
    return _response(True, f"{row['name']} is removed. It can no longer sign in.")


# ------------------------------------------------------------------ signing in

@bp.route("/login/passkey/options", methods=["POST"])
def login_options():
    """A one-time challenge for a passkey sign-in (the device offers the passkeys it holds for this site, so no user name is needed)."""
    _on()
    chal = b64e(secrets.token_bytes(32))
    session["pk_chal"] = {"c": chal, "at": time.time(), "kind": "get"}
    return jsonify({"ok": True, "options": {"challenge": chal, "rpId": rp_id(), "timeout": cfg_int("passkeys_challenge_seconds") * 1000,
                                            "userVerification": "required" if cfg("passkeys_require_uv") == "1" else "preferred", "allowCredentials": []}})


def _fail(message, username="", detail="passkey failed"):
    from . import security
    log_login(username, client_ip(), False, detail)
    import time as _t
    _t.sleep(0.5)
    return jsonify({"ok": False, "message": message}), 400


@bp.route("/login/passkey/verify", methods=["POST"])
def login_verify():
    """The device signed the challenge: find the passkey, check the signature and sign the person in."""
    _on()
    from . import security
    if security.ip_blocked(client_ip()):
        return jsonify({"ok": False, "message": "Too many failed sign-in attempts from your address. Please wait a few minutes and try again."}), 429
    chal = _take_challenge("get")
    body = request.get_json(silent=True) or {}
    if not chal:
        return _fail("That took too long. Choose Sign In with a Passkey and try again.")
    d = get_db()
    row = d.execute("SELECT * FROM passkeys WHERE credential_id=%s", (str(body.get("id") or ""),)).fetchone()
    if row is None:
        return _fail("This site does not know that passkey. Sign in with your password, then add it under My Account > Passkeys.")
    user = User.get(row["user_id"])
    if user is None or user.disabled:
        return _fail("This account is disabled.", user.username if user else "", "disabled account")
    if user.is_locked():
        return _fail("Too many failed attempts. Try again in a few minutes.", user.username, "locked account")
    try:
        client_raw = b64d(body["clientDataJSON"])
        auth_data = b64d(body["authenticatorData"])
        cd, err = _client_data(client_raw, "webauthn.get", chal["c"])
        if err:
            return _fail(err, user.username)
        rp_hash, flags, count, _att = parse_auth_data(auth_data)
        if rp_hash != hashlib.sha256(rp_id().encode()).digest() or not flags & 0x01:
            return _fail("The passkey did not check out.", user.username)
        if cfg("passkeys_require_uv") == "1" and not flags & 0x04:
            return _fail("This site needs the device to check it is you (fingerprint, face or PIN).", user.username)
        cose, _e = cbor_decode(bytes(row["public_key"]))
        if not verify_signature(cose, b64d(body["signature"]), auth_data + hashlib.sha256(client_raw).digest()):
            user.register_failure()
            return _fail("The passkey did not check out.", user.username)
    except (KeyError, ValueError, IndexError, TypeError):
        return _fail("The passkey could not be read.", user.username)
    if (count or row["sign_count"]) and count <= row["sign_count"]:
        audit("passkey.counter", target=user.username, detail=f"{row['name']}: counter {count} not above {row['sign_count']}", actor_name=user.username)
        return _fail("This passkey looks copied, so it was refused. Remove it and add it again.", user.username, "passkey counter")
    d.execute("UPDATE passkeys SET sign_count=%s, last_used=now(), backed_up=%s WHERE id=%s", (count, bool(flags & 0x10), row["id"]))
    d.commit()
    session.clear()
    user.register_success()
    log_login(user.username, client_ip(), True, "passkey")
    login_user(user, remember=False)
    session.permanent = True
    security.stamp_login()
    security.new_session(user.id)
    from .auth import _safe_next
    nxt = url_for("auth.change_password") if user.must_change_password else _safe_next(body.get("next"))
    return jsonify({"ok": True, "next": nxt})
