"""Mesh Channels: make a private Meshtastic channel, share it with chosen members, and give each of them the same QR code and address to put into the Meshtastic app.

A channel is a name, a random key (16 or 32 random bytes made here), and options (uplink and downlink to MQTT, how exactly positions are shared). The Meshtastic app adds a channel from a web address of the form
https://meshtastic.org/e/?add=true#<the channel settings as a protobuf, in url-safe base64 without padding>: `build_settings` writes exactly the fields the app reads (psk, name, uplink, downlink, position precision).
The address with `?add=true` adds the channel and keeps the person's own; the one without it replaces all channels, so it is offered only as a second choice, with a warning. The key is stored sealed (app/vault.py,
the same protection as passwords) and is shown only to the owner and the members the owner shared it with: the QR code is an image served after that check, never part of a public page.
Sharing follows the chat rules: a member who blocked the owner, or whom the owner blocked, is skipped. Limits (channels per member, people per channel) are settings. Everything (make, change, rotate the key,
share, unshare, leave, delete, an administrator's removal, showing the key) is in the audit log; administrators see a channel's name, owner and people but never its key."""
import base64
import re
import secrets

from flask import Blueprint, Response, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from . import mesh as ms
from . import social
from .action_response import action_response
from .admin import permission_required
from .audit import audit
from .db import get_db
from .settings import cfg, cfg_int
from .vault import seal, unseal

bp = Blueprint("mesh_channels", __name__)

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,11}$")
PRECISIONS = (0, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 32)   # bits of position shared on the channel; 0 shares none (the app's own choices)
BASE = "https://meshtastic.org/e/"
MAX_CODE = 8   # a Meshtastic radio holds at most eight channels
# About how far a position is blurred at each setting (the Meshtastic documentation's figures), in kilometers: shown in the person's own units
PRECISION_KM = {10: 23.3, 11: 11.7, 12: 5.8, 13: 2.9, 14: 1.5, 15: 0.73, 16: 0.364, 17: 0.182, 18: 0.091, 19: 0.045}


def on():
    return ms.enabled() and cfg("mesh_channels_enabled") == "1"


def _on():
    if not on():
        abort(404)


# ------------------------------------------------------------------ the app's channel address

def varint(n):
    out = b""
    n &= (1 << 64) - 1
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def _field(num, wire, payload):
    return varint(num << 3 | wire) + payload


def _bytes_field(num, data):
    return _field(num, 2, varint(len(data)) + data)


def build_settings(psk, name, uplink=False, downlink=False, precision=0):
    """One ChannelSettings message: psk (2), name (3), uplink_enabled (5), downlink_enabled (6), module_settings (7) with position_precision (1)."""
    out = _bytes_field(2, psk) + (_bytes_field(3, name.encode()) if name else b"")
    if uplink:
        out += _field(5, 0, varint(1))
    if downlink:
        out += _field(6, 0, varint(1))
    if precision:
        out += _bytes_field(7, _field(1, 0, varint(precision)))
    return out


def address_of(settings, add=True):
    """The https://meshtastic.org/e/ address for a ChannelSet holding these channels (each one a ChannelSettings message), the first becoming the primary channel when the app replaces its channels."""
    cs = b"".join(_bytes_field(1, s) for s in settings)
    return BASE + ("?add=true" if add else "") + "#" + base64.urlsafe_b64encode(cs).decode().rstrip("=")


def address(psk, name, uplink=False, downlink=False, precision=0, add=True):
    """The address for one channel."""
    return address_of([build_settings(psk, name, uplink, downlink, precision)], add)


DEFAULT_SETTINGS = build_settings(b"", "")   # the default channel: the one-byte key 1 and no name (the app names it after the radio preset): its address ends CgMSAQE


def qr_svg(text):
    import io
    import segno
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", scale=6, border=2, dark="#000000", light="#ffffff", xmldecl=False, svgns=True, nl=False)
    return buf.getvalue().decode()


# ------------------------------------------------------------------ who may see a channel

def _row(cid):
    """(channel, 'owner' | 'member'), or a 404 for anyone else."""
    d = get_db()
    c = d.execute("SELECT * FROM mesh_channels WHERE id=%s", (cid,)).fetchone()
    if c is None:
        abort(404)
    if c["owner_id"] == current_user.id:
        return c, "owner"
    if d.execute("SELECT 1 FROM mesh_channel_members WHERE channel_id=%s AND user_id=%s", (cid, current_user.id)).fetchone():
        return c, "member"
    abort(404)


def _owned(cid):
    c, role = _row(cid)
    if role != "owner":
        abort(404)
    return c


def _psk(c):
    try:
        return base64.b64decode(unseal(c["psk_sealed"]))
    except ValueError:
        return b""


def _addr(c, add=True):
    return address(_psk(c), c["name"], c["uplink"], c["downlink"], c["precision"], add)


def _back(ok, msg):
    return action_response(ok, msg, redirect_endpoint="mesh_channels.page")


def _options(form):
    try:
        precision = int(form.get("precision", "0") or 0)
    except ValueError:
        precision = -1
    if precision not in PRECISIONS:
        return None, "Choose one of the position choices."
    return {"uplink": bool(form.get("uplink")), "downlink": bool(form.get("downlink")), "precision": precision, "note": (form.get("note") or "").strip()[:200]}, None


# ------------------------------------------------------------------ the member's page

@bp.route("/my/mesh/channels")
@login_required
def page():
    _on()
    d = get_db()
    me = current_user.id
    mine = d.execute("SELECT c.*, (SELECT count(*) FROM mesh_channel_members m WHERE m.channel_id = c.id) AS people FROM mesh_channels c WHERE c.owner_id=%s ORDER BY c.created", (me,)).fetchall()
    people = {}
    for r in d.execute("SELECT m.channel_id, u.username, COALESCE(NULLIF(u.display_name, ''), u.username) AS name FROM mesh_channel_members m JOIN users u ON u.id = m.user_id WHERE m.channel_id IN (SELECT id FROM mesh_channels WHERE owner_id=%s) ORDER BY u.username", (me,)).fetchall():
        people.setdefault(r["channel_id"], []).append(r)
    shared = d.execute("SELECT c.*, COALESCE(NULLIF(u.display_name, ''), u.username) AS owner_name FROM mesh_channel_members m JOIN mesh_channels c ON c.id = m.channel_id JOIN users u ON u.id = c.owner_id WHERE m.user_id=%s ORDER BY c.name", (me,)).fetchall()
    convs = d.execute("SELECT c.id, c.name, c.kind, (SELECT string_agg(COALESCE(NULLIF(u.display_name, ''), u.username), ', ') FROM conv_members m2 JOIN users u ON u.id = m2.user_id WHERE m2.conv_id = c.id AND m2.user_id <> %s) AS others "
                      "FROM conv_members m JOIN convs c ON c.id = m.conv_id WHERE m.user_id=%s AND c.kind='group' ORDER BY c.last_at DESC LIMIT 50", (me, me)).fetchall() if cfg("chat_enabled") == "1" else []
    return render_template("my_mesh_channels.html", mine=mine, people=people, shared=shared, convs=convs, precisions=PRECISIONS, precision_km=PRECISION_KM, default_address=address_of([DEFAULT_SETTINGS]), max_code=MAX_CODE, limit=cfg_int("mesh_channels_per_member"), full=len(mine) >= cfg_int("mesh_channels_per_member"),
                           max_people=cfg_int("mesh_channel_max_members"), friends_on=cfg("friends_enabled") == "1", addr=_addr, active="channels")


@bp.route("/my/mesh/channels/add", methods=["POST"])
@login_required
def add():
    _on()
    d = get_db()
    f = request.form
    name = (f.get("name") or "").strip()
    if not NAME_RE.fullmatch(name):
        return _back(False, "A channel name is 1 to 11 letters, digits, dashes or underscores (the Meshtastic limit).")
    opts, err = _options(f)
    if err:
        return _back(False, err)
    if d.execute("SELECT count(*) AS n FROM mesh_channels WHERE owner_id=%s", (current_user.id,)).fetchone()["n"] >= cfg_int("mesh_channels_per_member"):
        return _back(False, f"You can make {cfg_int('mesh_channels_per_member')} channels.")
    if d.execute("SELECT 1 FROM mesh_channels WHERE owner_id=%s AND lower(name)=lower(%s)", (current_user.id, name)).fetchone():
        return _back(False, "You already have a channel with that name.")
    size = 16 if f.get("key_bits") == "128" else 32
    psk = secrets.token_bytes(size)
    cid = d.execute("INSERT INTO mesh_channels (owner_id, name, psk_sealed, uplink, downlink, precision, note) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (current_user.id, name, seal(base64.b64encode(psk).decode()), opts["uplink"], opts["downlink"], opts["precision"], opts["note"])).fetchone()["id"]
    d.commit()
    audit("meshchannel.create", target=name, detail=f"channel #{cid}, {size * 8}-bit key")
    return _back(True, f"Channel {name} made. Scan its QR code with the Meshtastic app.")


@bp.route("/my/mesh/channels/<int:cid>/save", methods=["POST"])
@login_required
def save(cid):
    _on()
    c = _owned(cid)
    opts, err = _options(request.form)
    if err:
        return _back(False, err)
    d = get_db()
    d.execute("UPDATE mesh_channels SET uplink=%s, downlink=%s, precision=%s, note=%s WHERE id=%s", (opts["uplink"], opts["downlink"], opts["precision"], opts["note"], cid))
    d.commit()
    audit("meshchannel.update", target=c["name"], detail=f"channel #{cid}")
    return _back(True, "Saved. The QR code and address changed with the options: people who already added the channel should scan again.")


@bp.route("/my/mesh/channels/<int:cid>/rotate", methods=["POST"])
@login_required
def rotate(cid):
    _on()
    c = _owned(cid)
    d = get_db()
    d.execute("UPDATE mesh_channels SET psk_sealed=%s, rotated=now() WHERE id=%s", (seal(base64.b64encode(secrets.token_bytes(len(_psk(c)) or 32)).decode()), cid))
    d.commit()
    audit("meshchannel.rotate", target=c["name"], detail=f"channel #{cid}: new key")
    return _back(True, "The key was replaced. Everyone has to scan the new QR code.")


@bp.route("/my/mesh/channels/<int:cid>/delete", methods=["POST"])
@login_required
def delete(cid):
    _on()
    c = _owned(cid)
    d = get_db()
    d.execute("DELETE FROM mesh_channels WHERE id=%s", (cid,))
    d.commit()
    audit("meshchannel.delete", target=c["name"], detail=f"channel #{cid}")
    return _back(True, "Channel deleted.")


@bp.route("/my/mesh/channels/<int:cid>/leave", methods=["POST"])
@login_required
def leave(cid):
    _on()
    c, role = _row(cid)
    if role == "owner":
        abort(404)
    d = get_db()
    d.execute("DELETE FROM mesh_channel_members WHERE channel_id=%s AND user_id=%s", (cid, current_user.id))
    d.commit()
    audit("meshchannel.leave", target=c["name"], detail=f"channel #{cid}")
    return _back(True, "You left the channel. The key you already have still works on your own radio until the owner replaces it.")


@bp.route("/my/mesh/channels/<int:cid>/share", methods=["POST"])
@login_required
def share(cid):
    _on()
    c = _owned(cid)
    d = get_db()
    me = current_user.id
    f = request.form
    want = {}
    for t in re.split(r"[,\n]+", f.get("to", "")):
        if t.strip():
            u = social.find_user(t)
            if u is None:
                return _back(False, f"No member found for {t.strip()[:40]}.")
            want[u["id"]] = u
    if f.get("all_friends"):
        for fid in social.friend_ids(me):
            u = d.execute("SELECT id, username, display_name, contact_policy, show_online, disabled FROM users WHERE id=%s AND NOT disabled", (fid,)).fetchone()
            if u:
                want[u["id"]] = u
    if f.get("conv_id", "").isdigit():
        if not d.execute("SELECT 1 FROM conv_members WHERE conv_id=%s AND user_id=%s", (int(f["conv_id"]), me)).fetchone():
            return _back(False, "Choose one of your groups.")
        for r in d.execute("SELECT u.id, u.username, u.display_name, u.contact_policy, u.show_online, u.disabled FROM conv_members m JOIN users u ON u.id = m.user_id WHERE m.conv_id=%s AND NOT u.disabled", (int(f["conv_id"]),)).fetchall():
            want[r["id"]] = r
    want.pop(me, None)
    if not want:
        return _back(False, "Choose who to share it with.")
    have = {r["user_id"] for r in d.execute("SELECT user_id FROM mesh_channel_members WHERE channel_id=%s", (cid,)).fetchall()}
    added, skipped = [], 0
    room = cfg_int("mesh_channel_max_members") - len(have)
    for uid, u in want.items():
        if uid in have:
            continue
        if social.either_blocked(me, uid):
            skipped += 1
            continue
        if room <= 0:
            return _back(False, f"A channel can be shared with {cfg_int('mesh_channel_max_members')} people at most.")
        d.execute("INSERT INTO mesh_channel_members (channel_id, user_id, added_by) VALUES (%s,%s,%s)", (cid, uid, me))
        room -= 1
        added.append(u["username"])
    d.commit()
    if added:
        audit("meshchannel.share", target=c["name"], detail=f"channel #{cid} with {', '.join(added)[:300]}")
    msg = f"Shared with {len(added)} {'person' if len(added) == 1 else 'people'}." if added else "Everyone you chose already has it."
    if skipped:
        msg += f" {skipped} skipped (blocked either way)."
    return _back(True, msg)


@bp.route("/my/mesh/channels/<int:cid>/unshare", methods=["POST"])
@login_required
def unshare(cid):
    _on()
    c = _owned(cid)
    u = social.find_user(request.form.get("user", ""))
    d = get_db()
    if u is None or not d.execute("DELETE FROM mesh_channel_members WHERE channel_id=%s AND user_id=%s RETURNING 1", (cid, u["id"])).fetchone():
        return _back(False, "That person does not have this channel.")
    d.commit()
    audit("meshchannel.unshare", target=c["name"], detail=f"channel #{cid}, {u['username']} removed (replace the key to lock them out)")
    return _back(True, f"{u['username']} no longer sees this channel. They keep the key they already have until you replace it.")


@bp.route("/my/mesh/channels/<int:cid>/qr.svg")
@login_required
def qr(cid):
    _on()
    c, _role = _row(cid)
    r = Response(qr_svg(_addr(c, request.args.get("replace") != "1")), mimetype="image/svg+xml")
    r.headers["Cache-Control"] = "no-store"
    return r


@bp.route("/my/mesh/channels/<int:cid>/key", methods=["POST"])
@login_required
def key(cid):
    _on()
    c, _role = _row(cid)
    psk = _psk(c)
    audit("meshchannel.key_shown", target=c["name"], detail=f"channel #{cid}")
    r = jsonify({"ok": True, "base64": base64.b64encode(psk).decode(), "hex": psk.hex(), "bits": len(psk) * 8, "address": _addr(c), "replace_address": _addr(c, False)})
    r.headers["Cache-Control"] = "no-store"
    return r


# ------------------------------------------------------------------ one code for several channels

def _wanted(text):
    """([ChannelSettings], [names], error): the channels named in `ids` (the word default and/or channel numbers) that this member may see, in the order given."""
    out, names = [], []
    d = get_db()
    for part in [x for x in (text or "").split(",") if x.strip()]:
        part = part.strip()
        if part == "default":
            out.append(DEFAULT_SETTINGS)
            names.append("Default channel")
            continue
        if not part.isdigit():
            return None, None, "Choose from the list."
        c = d.execute("SELECT * FROM mesh_channels WHERE id=%s", (int(part),)).fetchone()
        if c is None or not (c["owner_id"] == current_user.id or d.execute("SELECT 1 FROM mesh_channel_members WHERE channel_id=%s AND user_id=%s", (c["id"], current_user.id)).fetchone()):
            return None, None, "Choose from the list."
        out.append(build_settings(_psk(c), c["name"], c["uplink"], c["downlink"], c["precision"]))
        names.append(c["name"])
    if not out:
        return None, None, "Choose at least one channel."
    if len(out) > MAX_CODE:
        return None, None, f"A radio holds {MAX_CODE} channels at most: choose {MAX_CODE} or fewer."
    return out, names, None


@bp.route("/my/mesh/channels/combine", methods=["POST"])
@login_required
def combine():
    _on()
    items, names, err = _wanted(request.form.get("ids", ""))
    if err:
        return jsonify({"ok": False, "message": err}), 400
    audit("meshchannel.combine", target=current_user.username, detail=f"one code for {len(items)} channels: {', '.join(names)[:200]}")
    r = jsonify({"ok": True, "count": len(items), "names": names, "address": address_of(items, True), "replace_address": address_of(items, False)})
    r.headers["Cache-Control"] = "no-store"
    return r


@bp.route("/my/mesh/channels/combine.svg")
@login_required
def combine_qr():
    _on()
    items, _names, err = _wanted(request.args.get("ids", ""))
    if err:
        abort(400)
    r = Response(qr_svg(address_of(items, request.args.get("replace") != "1")), mimetype="image/svg+xml")
    r.headers["Cache-Control"] = "no-store"
    return r


# ------------------------------------------------------------------ the administrators' view (never the key)

@bp.route("/admin/mesh/channels/<int:cid>/delete", methods=["POST"])
@permission_required("manage_map_data")
def admin_delete(cid):
    d = get_db()
    c = d.execute("SELECT c.name, u.username FROM mesh_channels c JOIN users u ON u.id = c.owner_id WHERE c.id=%s", (cid,)).fetchone()
    if c is None:
        return action_response(False, "That channel does not exist.", redirect_endpoint="mesh.admin")
    d.execute("DELETE FROM mesh_channels WHERE id=%s", (cid,))
    d.commit()
    audit("meshchannel.admin_delete", target=c["name"], detail=f"channel #{cid} of {c['username']}")
    return action_response(True, "Channel removed.", redirect_endpoint="mesh.admin")


def admin_rows():
    return get_db().execute("SELECT c.id, c.name, c.created, u.username AS owner, (SELECT count(*) FROM mesh_channel_members m WHERE m.channel_id = c.id) AS people, c.uplink, c.downlink FROM mesh_channels c JOIN users u ON u.id = c.owner_id ORDER BY c.created DESC").fetchall()
