"""Satellite images from members' own receivers: weather satellite pictures (Meteor-M LRPT, GOES HRIT or LRIT, NOAA APT) that a member's SDR station decoded, sent to the site and shown in a gallery.

A member adds a *receiver* on My Account > My Satellite Receivers and gets a private upload address (the key is part of the address, like a weather station's). The decoding software or a small script on the member's
computer posts each finished picture to that address (`curl -F image=@pass.png ...`; SatDump and goestools both write files a script can send). Pictures are kept in the database up to a limit per receiver (the oldest
go first), shown on Radio > Satellite Images with the satellite, the receiver and when it was received, and each links to the Satellite Passes page. A receiver can be public or private; administrators manage every
receiver and picture, can add a receiver for a member, hide or delete a picture, and everything is in the audit log. Only pictures sent by members' own equipment are shown: nothing is fetched from outside here."""
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from . import csrf, vault
from .action_response import action_response
from .admin import permission_required
from .audit import audit
from .db import get_db
from .settings import cfg, cfg_int

bp = Blueprint("satimages", __name__)

KINDS = {"meteor": "Meteor-M weather satellite (LRPT)", "goes": "GOES weather satellite (HRIT or LRIT)", "noaa": "NOAA weather satellite (APT)", "other": "Another satellite"}
KIND_SHORT = {"meteor": "Meteor LRPT", "goes": "GOES HRIT", "noaa": "NOAA APT", "other": "Other"}
TYPES = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"))


def enabled():
    return cfg("satimg_enabled") == "1"


def _need():
    if not enabled():
        abort(404)


def token_hash(token):
    return hashlib.sha256(("sat\0" + token).encode()).hexdigest()


def sniff(data):
    """The content type of a picture from its first bytes (PNG, JPEG or WebP), or None."""
    for magic, ctype in TYPES:
        if data.startswith(magic):
            return ctype
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _clean(f):
    """(values, error) from a receiver form."""
    name = " ".join((f.get("name") or "").split())[:60]
    if len(name) < 3:
        return None, "Give the receiver a name of at least three letters."
    kind = f.get("kind", "meteor")
    if kind not in KINDS:
        return None, "Choose what the receiver listens to."
    return {"name": name, "kind": kind, "place": " ".join((f.get("place") or "").split())[:120], "public": f.get("public") == "1", "notes": (f.get("notes") or "").strip()[:500]}, None


def upload_url(r):
    return request.host_url.rstrip("/") + "/sat/push/" + vault.unseal(r["token_sealed"])


def _new_token():
    t = secrets.token_urlsafe(24)
    return t, token_hash(t), vault.seal(t)


def _create(owner_id, v):
    d = get_db()
    t, h, s = _new_token()
    rid = d.execute("INSERT INTO sat_receivers (owner_id, name, kind, place, public, notes, token_hash, token_sealed) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (owner_id, v["name"], v["kind"], v["place"], v["public"], v["notes"], h, s)).fetchone()["id"]
    d.commit()
    return rid


def _update(rid, v):
    d = get_db()
    d.execute("UPDATE sat_receivers SET name=%s, kind=%s, place=%s, public=%s, notes=%s WHERE id=%s", (v["name"], v["kind"], v["place"], v["public"], v["notes"], rid))
    d.commit()


# ------------------------------------------------------------------ the address the receivers send to

def _when(text):
    """When the picture was taken: an ISO 8601 time or seconds since 1970; now when missing or unreadable (and never in the future)."""
    now = datetime.now(timezone.utc)
    text = (text or "").strip()
    if not text:
        return now
    try:
        t = datetime.fromtimestamp(float(text), timezone.utc) if re.match(r"^\d{9,11}(\.\d+)?$", text) else datetime.fromisoformat(text.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return min(t, now)
    except (ValueError, OverflowError, OSError):
        return now


@bp.route("/sat/push/<token>", methods=["POST"])
@csrf.exempt
def push(token):
    """One picture: the file in a form field called image (or as the whole body), with optional satellite, product, captured (time) and note."""
    if not enabled():
        return jsonify(error="switched_off"), 404
    d = get_db()
    r = d.execute("SELECT * FROM sat_receivers WHERE token_hash=%s", (token_hash(token),)).fetchone()
    if r is None or not r["enabled"]:
        return jsonify(error="unknown_address"), 404
    f = request.files.get("image") or next(iter(request.files.values()), None)
    data = f.read() if f else request.get_data()
    if len(data) > cfg_int("satimg_max_mb") * 1024 * 1024:
        return jsonify(error="too_large", limit_mb=cfg_int("satimg_max_mb")), 413
    ctype = sniff(data)
    if ctype is None:
        return jsonify(error="not_a_picture", detail="send a PNG, JPEG or WebP file"), 400
    if d.execute("SELECT COUNT(*) AS n FROM sat_images WHERE receiver_id=%s AND received_at > now() - interval '1 hour'", (r["id"],)).fetchone()["n"] >= cfg_int("satimg_per_hour"):
        return jsonify(error="too_many", detail=f"at most {cfg_int('satimg_per_hour')} pictures an hour from one receiver"), 429
    v = request.values
    iid = d.execute("INSERT INTO sat_images (receiver_id, satellite, product, captured_at, content_type, size, data, note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (r["id"], " ".join((v.get("satellite") or "").split())[:60], " ".join((v.get("product") or "").split())[:60], _when(v.get("captured")), ctype, len(data), data,
                     (v.get("note") or "").strip()[:300])).fetchone()["id"]
    d.execute("DELETE FROM sat_images WHERE receiver_id=%s AND id NOT IN (SELECT id FROM sat_images WHERE receiver_id=%s ORDER BY captured_at DESC, id DESC LIMIT %s)", (r["id"], r["id"], cfg_int("satimg_keep")))
    d.execute("UPDATE sat_receivers SET last_image_at=now() WHERE id=%s", (r["id"],))
    d.commit()
    return jsonify(ok=True, id=iid)


# ------------------------------------------------------------------ the feed page and the numbers other pages show

def counts():
    d = get_db()
    r = d.execute("SELECT COUNT(*) AS receivers, COUNT(*) FILTER (WHERE last_image_at > now() - interval '48 hours' AND enabled) AS active, COUNT(*) FILTER (WHERE last_image_at IS NULL AND enabled) AS silent FROM sat_receivers").fetchone()
    i = d.execute("SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS bytes, COUNT(*) FILTER (WHERE received_at > now() - interval '24 hours') AS day FROM sat_images").fetchone()
    return {"receivers": r["receivers"], "active": r["active"], "silent": r["silent"], "pictures": i["n"], "bytes": i["bytes"], "day": i["day"]}


@bp.route("/feed/satellites")
def feed_page():
    _need()
    base = "https://" + (cfg("feed_host") or cfg("main_address") or request.host)
    return render_template("feed_satellites.html", fsub="satellites", base=base, uploader=base + "/static/satimage_uploader.py", keep=cfg_int("satimg_keep"), max_mb=cfg_int("satimg_max_mb"), per_hour=cfg_int("satimg_per_hour"))


# ------------------------------------------------------------------ the public pages

def _may_see(img):
    if img["hidden"] and not _is_admin():
        return False
    if img["enabled"] and img["public"]:
        return True
    return current_user.is_authenticated and (img["owner_id"] == current_user.id or _is_admin())


def _is_admin():
    return current_user.is_authenticated and current_user.has_permission("manage_satimages")


@bp.route("/sat/image/<int:iid>")
def image(iid):
    _need()
    img = get_db().execute("SELECT i.id, i.data, i.content_type, i.hidden, r.public, r.enabled, r.owner_id FROM sat_images i JOIN sat_receivers r ON r.id = i.receiver_id WHERE i.id=%s", (iid,)).fetchone()
    if img is None or not _may_see(img):
        abort(404)
    resp = Response(bytes(img["data"]), mimetype=img["content_type"])
    resp.headers["Cache-Control"] = "public, max-age=86400" if img["public"] and img["enabled"] and not img["hidden"] else "private, max-age=0"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@bp.route("/radio/satellite-images")
def gallery():
    _need()
    d = get_db()
    sat, kind = " ".join(request.args.get("sat", "").split())[:60], request.args.get("kind", "")
    rx = int(request.args["rx"]) if request.args.get("rx", "").isdigit() else None
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    where, args = ["NOT i.hidden", "r.public", "r.enabled"], []
    if sat:
        where.append("i.satellite = %s"); args.append(sat)
    if kind in KINDS:
        where.append("r.kind = %s"); args.append(kind)
    if rx:
        where.append("r.id = %s"); args.append(rx)
    for key, op in (("from", ">="), ("to", "<")):
        t = request.args.get(key, "")
        if re.match(r"^\d{4}-\d\d-\d\d$", t):
            where.append(f"i.captured_at {op} %s::date" + (" + 1" if key == "to" else "")); args.append(t)
    w = " AND ".join(where)
    per = max(6, cfg_int("satimg_page"))
    total = d.execute(f"SELECT COUNT(*) AS n FROM sat_images i JOIN sat_receivers r ON r.id = i.receiver_id WHERE {w}", args).fetchone()["n"]
    rows = d.execute(f"SELECT i.id, i.satellite, i.product, i.captured_at, i.size, i.note, r.id AS rid, r.name AS rname, r.kind, r.place FROM sat_images i JOIN sat_receivers r ON r.id = i.receiver_id WHERE {w} "
                     "ORDER BY i.captured_at DESC, i.id DESC LIMIT %s OFFSET %s", args + [per, (page - 1) * per]).fetchall()
    sats = [x["satellite"] for x in d.execute("SELECT DISTINCT i.satellite FROM sat_images i JOIN sat_receivers r ON r.id = i.receiver_id WHERE NOT i.hidden AND r.public AND r.enabled AND i.satellite <> '' ORDER BY 1").fetchall()]
    rxs = d.execute("SELECT DISTINCT r.id, r.name FROM sat_receivers r WHERE r.public AND r.enabled AND EXISTS (SELECT 1 FROM sat_images i WHERE i.receiver_id = r.id AND NOT i.hidden) ORDER BY r.name").fetchall()
    loops = loops_for_gallery(w, args)
    norad = {}
    for s in {x["satellite"] for x in rows if x["satellite"]}:
        t = d.execute("SELECT norad FROM sat_tle WHERE upper(name) = upper(%s) LIMIT 1", (s,)).fetchone()
        if t:
            norad[s] = t["norad"]
    return render_template("satellite_images.html", loops=loops, rows=rows, total=total, page=page, per=per, sat=sat, kind=kind, rx=rx, sats=sats, rxs=rxs, kinds=KINDS, norad=norad, kind_short=KIND_SHORT,
                           frm=request.args.get("from", ""), to=request.args.get("to", ""), passes_on=cfg("satpass_enabled") == "1")


# ------------------------------------------------------------------ animation loops from the pictures members send
# A loop is a run of pictures from one receiver, of the same satellite and product, each no further from the one before than the gap setting (a GOES HRIT station sends one every ten minutes or so; a
# Meteor pass sends a few within minutes). Nothing is stored for a loop: it is found from the pictures when somebody looks, and each member sees only the pictures they may see.

def _runs(rows):
    """Split pictures (id, captured_at; any order) of one series into runs with no gap longer than the setting. Each run is oldest first."""
    gap = timedelta(minutes=max(1, cfg_int("satimg_loop_gap_minutes")))
    out, cur = [], []
    for r in sorted(rows, key=lambda x: (x["captured_at"], x["id"])):
        if cur and r["captured_at"] - cur[-1]["captured_at"] > gap:
            out.append(cur); cur = []
        cur.append(r)
    if cur:
        out.append(cur)
    return out


def loops_for_gallery(where, args):
    """The loops among the newest public pictures that fit the gallery filters (where/args as built by gallery): newest first, each {anchor (newest picture id), count, first, last, satellite, product, receiver}."""
    rows = get_db().execute("SELECT i.id, i.satellite, i.product, i.captured_at, r.id AS rid, r.name AS rname FROM sat_images i JOIN sat_receivers r ON r.id = i.receiver_id WHERE " + where +
                            " ORDER BY i.captured_at DESC, i.id DESC LIMIT 2000", args).fetchall()
    series = {}
    for r in rows:
        series.setdefault((r["rid"], r["satellite"], r["product"]), []).append(r)
    need, most = max(2, cfg_int("satimg_loop_min")), max(3, cfg_int("satimg_loop_max"))
    found = []
    for (rid, sat, prod), items in series.items():
        for run in _runs(items):
            if len(run) >= need:
                run = run[-most:]
                found.append({"anchor": run[-1]["id"], "count": len(run), "first": run[0]["captured_at"], "last": run[-1]["captured_at"], "satellite": sat, "product": prod, "receiver": run[-1]["rname"], "rid": rid})
    found.sort(key=lambda x: x["last"], reverse=True)
    return found[:max(1, cfg_int("satimg_loop_list"))]


def _run_for(iid):
    """(anchor picture row, the run around it that the current viewer may see, oldest first), or aborts with 404."""
    d = get_db()
    a = d.execute("SELECT i.id, i.receiver_id, i.satellite, i.product, i.captured_at, i.hidden, r.public, r.enabled, r.owner_id, r.name AS rname FROM sat_images i JOIN sat_receivers r ON r.id = i.receiver_id WHERE i.id=%s", (iid,)).fetchone()
    if a is None or not _may_see(a):
        abort(404)
    rows = d.execute("SELECT i.id, i.captured_at FROM sat_images i WHERE i.receiver_id=%s AND i.satellite=%s AND i.product=%s" + ("" if _is_admin() else " AND NOT i.hidden") +
                     " ORDER BY i.captured_at DESC, i.id DESC LIMIT 2000", (a["receiver_id"], a["satellite"], a["product"])).fetchall()
    for run in _runs(rows):
        if any(x["id"] == iid for x in run):
            if len(run) < max(2, cfg_int("satimg_loop_min")):
                break
            return a, run[-max(3, cfg_int("satimg_loop_max")):]
    abort(404)


@bp.route("/sat/loop/<int:iid>.json")
def loop_json(iid):
    _need()
    a, run = _run_for(iid)
    resp = jsonify(frames=[{"t": x["captured_at"].isoformat(), "url": f"/sat/image/{x['id']}"} for x in run], more=False, ms=max(100, cfg_int("satimg_loop_ms")))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/radio/satellite-images/loop/<int:iid>")
def loop_page(iid):
    _need()
    a, run = _run_for(iid)
    return render_template("satellite_loop.html", a=a, count=len(run), first=run[0]["captured_at"], last=run[-1]["captured_at"], looper_v=__import__("app.goes", fromlist=["x"]).looper_version(), iid=iid, gap=max(1, cfg_int("satimg_loop_gap_minutes")))


# ------------------------------------------------------------------ My Account

def _my_ctx():
    d = get_db()
    rows = d.execute("SELECT r.*, (SELECT COUNT(*) FROM sat_images i WHERE i.receiver_id = r.id) AS n FROM sat_receivers r WHERE r.owner_id=%s ORDER BY lower(r.name)", (current_user.id,)).fetchall()
    imgs = {r["id"]: d.execute("SELECT id, satellite, product, captured_at, size, hidden FROM sat_images WHERE receiver_id=%s ORDER BY captured_at DESC, id DESC LIMIT 12", (r["id"],)).fetchall() for r in rows}
    return {"receivers": rows, "imgs": imgs, "kinds": KINDS, "limit": cfg_int("satimg_per_user"), "keep": cfg_int("satimg_keep"), "max_mb": cfg_int("satimg_max_mb"), "per_hour": cfg_int("satimg_per_hour"),
            "url_of": upload_url, "admin_view": False}


def _my_resp(ok, message):
    return action_response(ok, message, redirect_endpoint="satimages.my_page", fragment_template="partials/_my_satrx.html", fragment_context=_my_ctx())


@bp.route("/my/sat-receivers")
@login_required
def my_page():
    _need()
    return render_template("my_satrx.html", **_my_ctx())


@bp.route("/my/sat-receivers/add", methods=["POST"])
@login_required
def my_add():
    _need()
    d = get_db()
    if d.execute("SELECT COUNT(*) AS n FROM sat_receivers WHERE owner_id=%s", (current_user.id,)).fetchone()["n"] >= cfg_int("satimg_per_user"):
        return _my_resp(False, f"You can have {cfg_int('satimg_per_user')} receivers. Remove one to add another.")
    v, err = _clean(request.form)
    if err:
        return _my_resp(False, err)
    rid = _create(current_user.id, v)
    audit("satrx.add", target=v["name"], detail=f"#{rid}")
    return _my_resp(True, f"{v['name']} is added. Its upload address is below: set your decoding software or a small script to send each finished picture there.")


def _own(rid):
    r = get_db().execute("SELECT * FROM sat_receivers WHERE id=%s AND owner_id=%s", (rid, current_user.id)).fetchone()
    if r is None:
        abort(404)
    return r


@bp.route("/my/sat-receivers/<int:rid>/save", methods=["POST"])
@login_required
def my_save(rid):
    _need()
    r = _own(rid)
    v, err = _clean(request.form)
    if err:
        return _my_resp(False, err)
    _update(rid, v)
    audit("satrx.update", target=v["name"], detail=f"#{rid}")
    return _my_resp(True, "Saved.")


@bp.route("/my/sat-receivers/<int:rid>/newaddress", methods=["POST"])
@login_required
def my_newaddress(rid):
    _need()
    r = _own(rid)
    _t, h, s = _new_token()
    d = get_db()
    d.execute("UPDATE sat_receivers SET token_hash=%s, token_sealed=%s WHERE id=%s", (h, s, rid))
    d.commit()
    audit("satrx.newaddress", target=r["name"], detail=f"#{rid}")
    return _my_resp(True, "A new upload address is made. The old one no longer works: put the new one in your software.")


@bp.route("/my/sat-receivers/<int:rid>/delete", methods=["POST"])
@login_required
def my_delete(rid):
    _need()
    r = _own(rid)
    d = get_db()
    d.execute("DELETE FROM sat_receivers WHERE id=%s", (rid,))
    d.commit()
    audit("satrx.delete", target=r["name"], detail=f"#{rid}")
    return _my_resp(True, f"{r['name']} and its pictures are removed.")


@bp.route("/my/sat-receivers/image/<int:iid>/delete", methods=["POST"])
@login_required
def my_image_delete(iid):
    _need()
    d = get_db()
    i = d.execute("SELECT i.id, r.name FROM sat_images i JOIN sat_receivers r ON r.id = i.receiver_id WHERE i.id=%s AND r.owner_id=%s", (iid, current_user.id)).fetchone()
    if i is None:
        abort(404)
    d.execute("DELETE FROM sat_images WHERE id=%s", (iid,))
    d.commit()
    audit("satimg.delete", target=i["name"], detail=f"#{iid}")
    return _my_resp(True, "The picture is deleted.")


# ------------------------------------------------------------------ Admin

def _admin_ctx():
    d = get_db()
    rows = d.execute("SELECT r.*, u.username AS owner_name, u.display_name AS owner_display, (SELECT COUNT(*) FROM sat_images i WHERE i.receiver_id = r.id) AS n FROM sat_receivers r LEFT JOIN users u ON u.id = r.owner_id "
                     "ORDER BY lower(r.name)").fetchall()
    imgs = d.execute("SELECT i.id, i.satellite, i.product, i.captured_at, i.size, i.hidden, r.name AS rname FROM sat_images i JOIN sat_receivers r ON r.id = i.receiver_id ORDER BY i.received_at DESC, i.id DESC LIMIT 100").fetchall()
    return {"receivers": rows, "images": imgs, "kinds": KINDS, "url_of": upload_url, "admin_view": True, "active": "satimages"}


def _admin_resp(ok, message):
    return action_response(ok, message, redirect_endpoint="satimages.admin_page", fragment_template="partials/_admin_satrx.html", fragment_context=_admin_ctx())


@bp.route("/admin/sat-images")
@permission_required("manage_satimages")
def admin_page():
    return render_template("admin_satrx.html", **_admin_ctx())


@bp.route("/admin/sat-images/add", methods=["POST"])
@permission_required("manage_satimages")
def admin_add():
    """A receiver for a member (or for the network itself when no member is named)."""
    d = get_db()
    who = (request.form.get("username") or "").strip()
    owner = None
    if who:
        u = d.execute("SELECT id FROM users WHERE lower(username)=lower(%s) OR lower(email)=lower(%s)", (who, who)).fetchone()
        if u is None:
            return _admin_resp(False, "No member has that user name or email address.")
        owner = u["id"]
    v, err = _clean(request.form)
    if err:
        return _admin_resp(False, err)
    rid = _create(owner, v)
    audit("satrx.admin_add", target=v["name"], detail=f"#{rid}" + (f" for {who}" if who else ""))
    return _admin_resp(True, f"{v['name']} is added. Its upload address is in the list below.")


def _rx(rid):
    r = get_db().execute("SELECT * FROM sat_receivers WHERE id=%s", (rid,)).fetchone()
    if r is None:
        abort(404)
    return r


@bp.route("/admin/sat-images/<int:rid>/save", methods=["POST"])
@permission_required("manage_satimages")
def admin_save(rid):
    r = _rx(rid)
    v, err = _clean(request.form)
    if err:
        return _admin_resp(False, err)
    _update(rid, v)
    audit("satrx.admin_update", target=v["name"], detail=f"#{rid}")
    return _admin_resp(True, "Saved.")


@bp.route("/admin/sat-images/<int:rid>/enabled", methods=["POST"])
@permission_required("manage_satimages")
def admin_enabled(rid):
    r = _rx(rid)
    on = request.form.get("on") == "1"
    d = get_db()
    d.execute("UPDATE sat_receivers SET enabled=%s WHERE id=%s", (on, rid))
    d.commit()
    audit("satrx.admin_switch", target=r["name"], detail=f"#{rid}: {'on' if on else 'off'}")
    return _admin_resp(True, f"{r['name']} is switched {'on' if on else 'off'}: " + ("it can send and its pictures show." if on else "it cannot send and its pictures are hidden."))


@bp.route("/admin/sat-images/<int:rid>/newaddress", methods=["POST"])
@permission_required("manage_satimages")
def admin_newaddress(rid):
    r = _rx(rid)
    _t, h, s = _new_token()
    d = get_db()
    d.execute("UPDATE sat_receivers SET token_hash=%s, token_sealed=%s WHERE id=%s", (h, s, rid))
    d.commit()
    audit("satrx.admin_newaddress", target=r["name"], detail=f"#{rid}")
    return _admin_resp(True, "A new upload address is made; the old one no longer works.")


@bp.route("/admin/sat-images/<int:rid>/delete", methods=["POST"])
@permission_required("manage_satimages")
def admin_delete(rid):
    r = _rx(rid)
    d = get_db()
    d.execute("DELETE FROM sat_receivers WHERE id=%s", (rid,))
    d.commit()
    audit("satrx.admin_delete", target=r["name"], detail=f"#{rid}")
    return _admin_resp(True, f"{r['name']} and its pictures are deleted.")


@bp.route("/admin/sat-images/image/<int:iid>/<act>", methods=["POST"])
@permission_required("manage_satimages")
def admin_image(iid, act):
    if act not in ("hide", "show", "delete"):
        abort(400)
    d = get_db()
    i = d.execute("SELECT i.id, r.name FROM sat_images i JOIN sat_receivers r ON r.id = i.receiver_id WHERE i.id=%s", (iid,)).fetchone()
    if i is None:
        abort(404)
    if act == "delete":
        d.execute("DELETE FROM sat_images WHERE id=%s", (iid,))
    else:
        d.execute("UPDATE sat_images SET hidden=%s WHERE id=%s", (act == "hide", iid))
    d.commit()
    audit("satimg.admin_" + act, target=i["name"], detail=f"#{iid}")
    return _admin_resp(True, {"hide": "The picture is hidden from the gallery.", "show": "The picture shows again.", "delete": "The picture is deleted."}[act])
