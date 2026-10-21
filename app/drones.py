"""Drones: the aircraft that broadcast Remote ID (the identity and position broadcast the FAA and other regulators require of most drones) and that members' own receivers hear.

A drone sends its ID, kind, position, altitude, speed and heading over Wi-Fi or Bluetooth a few times a second, so anyone nearby with a cheap ESP32 or a phone can read it. A member with such a receiver sends what it
decodes to a private address on this site (`/drones/push/<key>`, normally through the small bridge `static/rid_bridge.py`); the site decodes raw messages itself (`ridodecode.py`) or reads the fields a receiver already
decoded, keeps each drone's newest position and details and a trail, and shows them on a list, a map and a page for each drone.

The operator's own location (which some drones also send) is never stored unless an administrator turns that on, because it can point at a person's home. Administrators can hide an ID on request and everything a
member adds, switches off or deletes is in the audit log."""
import hashlib
import json
import re
import secrets
import time

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from . import csrf, ridodecode, vault
from .action_response import action_response
from .admin import permission_required
from .audit import audit
from .cache import cached
from .db import get_db
from .settings import cfg, cfg_int

bp = Blueprint("drones", __name__)

CREDIT = "Positions and details are what drones broadcast as Remote ID, received by members' own receivers"
KINDS = {"air": ("In the air", "#ffb454"), "ground": ("On the ground", "#9fb0c8"), "other": ("Emergency or unknown", "#ff6b6b")}
ID_RE = re.compile(r"^[A-Za-z0-9._:\- ]{4,32}$")
_src, _trail, _state = {}, {}, {"cleaned": 0.0}


def enabled():
    return cfg("rid_enabled") == "1"


def _need():
    if not enabled():
        abort(404)


def token_hash(token):
    return hashlib.sha256(("rid\0" + token).encode()).hexdigest()


def _area():
    try:
        s, w, n, e = (float(x) for x in cfg("rid_area").split(","))
        return s, w, n, e
    except ValueError:
        return -90.0, -180.0, 90.0, 180.0


def _hidden():
    return {r["drone_id"] for r in get_db().execute("SELECT drone_id FROM rid_hidden").fetchall()}


def kind_of(status):
    return "air" if status == 2 else "ground" if status == 1 else "other"


def counts():
    d = get_db()
    r = d.execute("SELECT COUNT(*) FILTER (WHERE last_seen > now() - make_interval(mins => %s) AND lat IS NOT NULL) AS now, COUNT(*) FILTER (WHERE last_seen > now() - interval '24 hours') AS day, COUNT(*) AS total "
                  "FROM rid_drones WHERE drone_id NOT IN (SELECT drone_id FROM rid_hidden)", (cfg_int("rid_current_minutes"),)).fetchone()
    s = d.execute("SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE enabled AND last_report > now() - interval '24 hours') AS active, COUNT(*) FILTER (WHERE enabled AND last_report IS NULL) AS silent FROM rid_stations").fetchone()
    return {"now": r["now"], "day": r["day"], "total": r["total"], "stations": s["n"], "active": s["active"], "silent": s["silent"]}


# ------------------------------------------------------------------ taking what a receiver sends

FIELDS = (("id", ("id", "uas_id", "drone_id")), ("id_type", ("id_type",)), ("ua_type", ("ua_type",)), ("lat", ("lat", "latitude")), ("lon", ("lon", "lng", "longitude")), ("alt_geo", ("alt_geo", "alt", "altitude")),
          ("alt_baro", ("alt_baro",)), ("height", ("height",)), ("speed", ("speed",)), ("heading", ("heading", "direction")), ("vspeed", ("vspeed",)), ("status", ("status",)), ("op_lat", ("op_lat",)),
          ("op_lon", ("op_lon",)), ("op_id", ("op_id", "operator_id")), ("desc", ("desc", "description", "self_id")))


def _from_item(item):
    """One report from what a receiver sent: raw hex (a message or a pack), fields it already decoded, or both. Returns (src, report)."""
    if isinstance(item, str):
        item = {"raw": item}
    if not isinstance(item, dict):
        return None, {}
    rep = {}
    raws = item.get("raw") or item.get("hex") or item.get("msgs") or []
    for raw in ([raws] if isinstance(raws, str) else raws if isinstance(raws, list) else []):
        if isinstance(raw, str):
            rep.update(ridodecode.decode_hex(raw))
    for key, names in FIELDS:
        for n in names:
            if item.get(n) not in (None, ""):
                rep[key] = item[n]
                break
    src = str(item.get("src") or item.get("mac") or item.get("address") or "")[:40] or None
    return src, rep


def _items(body):
    text = body.decode("utf-8", "replace").strip()
    if not text:
        return []
    if text[:1] in "[{":
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            for key in ("items", "messages", "drones", "reports"):
                if isinstance(data.get(key), list):
                    return data[key]
            return [data]
        if isinstance(data, list):
            return data
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if ln[:1] == "{":
            try:
                out.append(json.loads(ln))
                continue
            except ValueError:
                pass
        if ln:
            out.append(ln)
    return out


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) < 1e9 else None


def _store(d, station_id, reports, now):
    """The newest report of each drone in a batch, written as one position and one set of details per drone."""
    south, west, north, east = _area()
    hidden = _hidden()
    keep_op = cfg("rid_store_operator") == "1"
    by = {}
    for src, rep in reports:
        ident = str(rep.get("id") or "").strip()
        if ident and src:
            _src[src] = (ident, now)
        elif src and src in _src and now - _src[src][1] < 600:
            ident = _src[src][0]
            if rep:
                _src[src] = (ident, now)
        if not ident or not ID_RE.match(ident) or ident in hidden:
            continue
        v = by.setdefault(ident, {"n": 0})
        v["n"] += 1
        lat, lon = _num(rep.get("lat")), _num(rep.get("lon"))
        if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180 and (lat or lon) and south <= lat <= north and west <= lon <= east:
            v.update(lat=lat, lon=lon)
            for k in ("alt_geo", "alt_baro", "height", "speed", "heading", "vspeed"):
                if _num(rep.get(k)) is not None:
                    v[k] = _num(rep[k])
            if rep.get("status") is not None and _num(rep["status"]) is not None:
                v["status"] = int(_num(rep["status"]))
        for k in ("id_type", "ua_type"):
            if _num(rep.get(k)) is not None:
                v[k] = int(_num(rep[k]))
        if isinstance(rep.get("op_id"), str) and rep["op_id"].strip():
            v["op_id"] = " ".join(rep["op_id"].split())[:32]
        if isinstance(rep.get("desc"), str) and rep["desc"].strip():
            v["description"] = " ".join(rep["desc"].split())[:64]
        if keep_op and _num(rep.get("op_lat")) is not None and _num(rep.get("op_lon")) is not None:
            v["op_lat"], v["op_lon"] = _num(rep["op_lat"]), _num(rep["op_lon"])
    stored = 0
    for ident, v in by.items():
        n = v.pop("n")
        cols = [c for c in ("lat", "lon", "alt_geo", "alt_baro", "height", "speed", "heading", "vspeed", "status", "id_type", "ua_type", "op_id", "description", "op_lat", "op_lon") if c in v]
        if not cols:
            continue
        d.execute("INSERT INTO rid_drones (drone_id, first_seen, last_seen, station_id) VALUES (%s, now(), now(), %s) ON CONFLICT (drone_id) DO NOTHING", (ident, station_id))
        sets = ", ".join(f"{c}=%s" for c in cols)
        d.execute(f"UPDATE rid_drones SET {sets}, last_seen=now(), station_id=%s, msgs=msgs+%s WHERE drone_id=%s", [v[c] for c in cols] + [station_id, n, ident])
        if "lat" in v and now - _trail.get(ident, 0) >= cfg_int("rid_trail_seconds"):
            d.execute("INSERT INTO rid_positions (drone_id, lat, lon, alt, speed) VALUES (%s,%s,%s,%s,%s)", (ident, v["lat"], v["lon"], v.get("alt_geo"), v.get("speed")))
            _trail[ident] = now
        stored += 1
    return stored


def _clean(d, now):
    if now - _state["cleaned"] < 600:
        return
    _state["cleaned"] = now
    d.execute("DELETE FROM rid_positions WHERE ts < now() - make_interval(hours => %s)", (cfg_int("rid_keep_hours"),))
    d.execute("DELETE FROM rid_drones WHERE last_seen < now() - make_interval(days => %s)", (cfg_int("rid_drone_days"),))
    for k in [k for k, t in _trail.items() if now - t > 3600]:
        del _trail[k]
    for k in [k for k, v in _src.items() if now - v[1] > 3600]:
        del _src[k]


@bp.route("/drones/push/<token>", methods=["POST"])
@csrf.exempt
def push(token):
    """A batch from a receiver: JSON (a list, or {"items": [...]}) of raw hex messages and/or decoded fields, or text with one hex message or JSON object per line."""
    if not enabled():
        return jsonify(error="switched_off"), 404
    d = get_db()
    st = d.execute("SELECT * FROM rid_stations WHERE token_hash=%s", (token_hash(token),)).fetchone()
    if st is None or not st["enabled"]:
        return jsonify(error="unknown_address"), 404
    if request.content_length and request.content_length > 2 * 1024 * 1024:
        return jsonify(error="too_large"), 413
    now = time.time()
    items = _items(request.get_data())[:cfg_int("rid_max_batch")]
    reports = [_from_item(i) for i in items]
    n = _store(d, st["id"], reports, now)
    d.execute("UPDATE rid_stations SET last_report=now(), msgs=msgs+%s WHERE id=%s", (len(items), st["id"]))
    _clean(d, now)
    d.commit()
    return jsonify(ok=True, items=len(items), drones=n)


# ------------------------------------------------------------------ what the pages read

SELECT = ("SELECT v.*, CASE WHEN s.public THEN s.name ELSE '' END AS station_name FROM rid_drones v LEFT JOIN rid_stations s ON s.id = v.station_id "
          "WHERE v.drone_id NOT IN (SELECT drone_id FROM rid_hidden) ")


def _row(r):
    return {"id": r["drone_id"], "id_type": r["id_type"], "id_type_name": ridodecode.ID_TYPES.get(r["id_type"], ""), "ua_type": r["ua_type"], "ua_name": ridodecode.UA_TYPES.get(r["ua_type"], "Not stated") if r["ua_type"] is not None else "Not stated",
            "status": r["status"], "status_name": ridodecode.STATUS.get(r["status"], ""), "kind": kind_of(r["status"]), "lat": r["lat"], "lon": r["lon"], "alt_geo": r["alt_geo"], "alt_baro": r["alt_baro"], "height": r["height"],
            "speed": r["speed"], "heading": r["heading"], "vspeed": r["vspeed"], "op_id": r["op_id"] or "", "description": r["description"] or "", "op_lat": r["op_lat"], "op_lon": r["op_lon"],
            "first_seen": r["first_seen"].isoformat(), "last_seen": r["last_seen"].isoformat(), "msgs": r["msgs"], "station": r.get("station_name") or ""}


def drones(hours=None, q="", kind=""):
    hours = hours or cfg_int("rid_list_hours")
    rows = get_db().execute(SELECT + "AND v.last_seen > now() - make_interval(hours => %s) ORDER BY v.last_seen DESC", (hours,)).fetchall()
    out = []
    for r in rows:
        x = _row(r)
        if kind and x["kind"] != kind:
            continue
        if q and q.lower() not in (x["id"] + " " + x["ua_name"] + " " + x["op_id"] + " " + x["description"]).lower():
            continue
        out.append(x)
    return out


def _cached_drones():
    raw = cached(("rid_drones_raw",), lambda: [_row(r) for r in get_db().execute(SELECT + "AND v.last_seen > now() - make_interval(hours => %s) ORDER BY v.last_seen DESC", (cfg_int("rid_list_hours"),)).fetchall()], 5)
    hid = _hidden()
    return [v for v in raw if v["id"] not in hid]


@bp.route("/api/drones/drones.json")
def api_drones():
    _need()
    return jsonify({"drones": _cached_drones(), "hours": cfg_int("rid_list_hours"), "credit": CREDIT, "now_minutes": cfg_int("rid_current_minutes")})


@bp.route("/api/drones/<did>.json")
def api_drone(did):
    _need()
    r = get_db().execute(SELECT + "AND v.drone_id=%s", (did,)).fetchone()
    if r is None:
        abort(404)
    trail = get_db().execute("SELECT ts, lat, lon, alt, speed FROM rid_positions WHERE drone_id=%s ORDER BY ts DESC LIMIT 600", (did,)).fetchall()
    return jsonify({"drone": _row(r), "trail": [[t["lat"], t["lon"], t["ts"].isoformat(), t["alt"]] for t in reversed(trail)], "credit": CREDIT})


@bp.route("/api/drones.geojson")
def api_geojson():
    """The Drones layer for every map: a dot for each drone heard lately, with its card."""
    from datetime import datetime, timezone
    from flask import Response
    if not enabled():
        return Response('{"type":"FeatureCollection","features":[]}', mimetype="application/geo+json")
    feats, now = [], datetime.now(timezone.utc)
    for v in _cached_drones():
        if v["lat"] is None or (now - datetime.fromisoformat(v["last_seen"])).total_seconds() / 60 > cfg_int("rid_current_minutes") * 6:
            continue
        rows = [["Kind", v["ua_name"]], ["ID", v["id"]]]
        if v["status_name"]:
            rows.append(["Status", v["status_name"]])
        if v["alt_geo"] is not None:
            rows.append(["Altitude", f"{v['alt_geo'] * 3.28084:,.0f} ft ({v['alt_geo']:,.0f} m)"])
        if v["height"] is not None:
            rows.append(["Above takeoff", f"{v['height'] * 3.28084:,.0f} ft"])
        if v["speed"] is not None:
            rows.append(["Speed", f"{v['speed'] * 2.23694:.0f} mph ({v['speed']:.1f} m/s)"])
        if v["heading"] is not None:
            rows.append(["Heading", f"{v['heading']:.0f}°"])
        if v["description"]:
            rows.append(["Described as", v["description"]])
        rows.append(["Heard", v["last_seen"], "t"])
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(v["lon"], 6), round(v["lat"], 6)]}, "properties": {
            "KIND": v["kind"], "size": 7, "event": "Drone " + v["id"], "headline": v["ua_name"] + (", " + v["status_name"].lower() if v["status_name"] else ""), "rows": rows, "link": "/drones/" + v["id"], "linkText": "Drone Page"}})
    resp = Response(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")), mimetype="application/geo+json")
    resp.headers["Cache-Control"] = "public, max-age=5"
    return resp


# ------------------------------------------------------------------ the public pages

@bp.route("/drones/")
def overview():
    _need()
    q = " ".join(request.args.get("q", "").split())[:60]
    kind = request.args.get("kind", "")
    hours = request.args.get("hours", "")
    hrs = int(hours) if hours.isdigit() and 1 <= int(hours) <= 720 else cfg_int("rid_list_hours")
    rows = drones(hrs, q, kind if kind in KINDS else "")
    return render_template("drones.html", rows=rows[:500], total=len(rows), c=counts(), q=q, kind=kind, hours=hrs, kinds=KINDS, credit=CREDIT, current=cfg_int("rid_current_minutes"))


@bp.route("/drones/map")
def drone_map():
    _need()
    from . import maplayers
    return render_template("drones_map.html", layers=maplayers.for_map(), label_scale=cfg("live_label_scale"), label_bg=cfg("live_label_bg"), map_lat=cfg("map_lat"), map_lon=cfg("map_lon"), map_zoom=cfg("map_zoom"),
                           wx_refresh=cfg_int("weather_refresh_minutes"), credit=CREDIT, kinds=KINDS, current=cfg_int("rid_current_minutes"), hours=cfg_int("rid_list_hours"))


@bp.route("/drones/<did>")
def drone_page(did):
    _need()
    if not ID_RE.match(did):
        abort(404)
    r = get_db().execute(SELECT + "AND v.drone_id=%s", (did,)).fetchone()
    if r is None:
        abort(404)
    return render_template("drone.html", v=_row(r), credit=CREDIT, kinds=KINDS, keep=cfg_int("rid_keep_hours"), map_lat=cfg("map_lat"), map_lon=cfg("map_lon"))


@bp.route("/feed/drones")
def feed_page():
    _need()
    base = "https://" + (cfg("feed_host") or cfg("main_address") or request.host)
    return render_template("feed_drones.html", fsub="drones", base=base, bridge=base + "/static/rid_bridge.py", keep=cfg_int("rid_keep_hours"), area=cfg("rid_area"), operator=cfg("rid_store_operator") == "1")


# ------------------------------------------------------------------ the owner's pages

def _clean_form(f):
    name = " ".join((f.get("name") or "").split())[:60]
    if len(name) < 3:
        return None, "Give the station a name of at least three letters."
    return {"name": name, "place": " ".join((f.get("place") or "").split())[:120], "public": f.get("public") == "1", "notes": (f.get("notes") or "").strip()[:500]}, None


def upload_url(r):
    return request.host_url.rstrip("/") + "/drones/push/" + vault.unseal(r["token_sealed"])


def _create(owner, v):
    d = get_db()
    t = secrets.token_urlsafe(24)
    rid = d.execute("INSERT INTO rid_stations (owner_id, name, place, public, notes, token_hash, token_sealed) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (owner, v["name"], v["place"], v["public"], v["notes"], token_hash(t), vault.seal(t))).fetchone()["id"]
    d.commit()
    return rid


def _my_ctx():
    rows = get_db().execute("SELECT * FROM rid_stations WHERE owner_id=%s ORDER BY lower(name)", (current_user.id,)).fetchall()
    return {"stations": rows, "limit": cfg_int("rid_per_user"), "url_of": upload_url, "admin_view": False}


def _my_resp(ok, message):
    return action_response(ok, message, redirect_endpoint="drones.my_page", fragment_template="partials/_my_drones.html", fragment_context=_my_ctx())


@bp.route("/my/drones")
@login_required
def my_page():
    _need()
    return render_template("my_drones.html", **_my_ctx())


@bp.route("/my/drones/add", methods=["POST"])
@login_required
def my_add():
    _need()
    if get_db().execute("SELECT COUNT(*) AS n FROM rid_stations WHERE owner_id=%s", (current_user.id,)).fetchone()["n"] >= cfg_int("rid_per_user"):
        return _my_resp(False, f"You can have {cfg_int('rid_per_user')} Remote ID stations. Remove one to add another.")
    v, err = _clean_form(request.form)
    if err:
        return _my_resp(False, err)
    rid = _create(current_user.id, v)
    audit("rid.add", target=v["name"], detail=f"#{rid}")
    return _my_resp(True, f"{v['name']} is added. Its upload address is below: give it to the bridge or to your receiver's software.")


def _own(rid):
    r = get_db().execute("SELECT * FROM rid_stations WHERE id=%s AND owner_id=%s", (rid, current_user.id)).fetchone()
    if r is None:
        abort(404)
    return r


def _newaddress(rid):
    t = secrets.token_urlsafe(24)
    d = get_db()
    d.execute("UPDATE rid_stations SET token_hash=%s, token_sealed=%s WHERE id=%s", (token_hash(t), vault.seal(t), rid))
    d.commit()


@bp.route("/my/drones/<int:rid>/save", methods=["POST"])
@login_required
def my_save(rid):
    _need()
    _own(rid)
    v, err = _clean_form(request.form)
    if err:
        return _my_resp(False, err)
    d = get_db()
    d.execute("UPDATE rid_stations SET name=%s, place=%s, public=%s, notes=%s WHERE id=%s", (v["name"], v["place"], v["public"], v["notes"], rid))
    d.commit()
    audit("rid.update", target=v["name"], detail=f"#{rid}")
    return _my_resp(True, "Saved.")


@bp.route("/my/drones/<int:rid>/newaddress", methods=["POST"])
@login_required
def my_newaddress(rid):
    _need()
    r = _own(rid)
    _newaddress(rid)
    audit("rid.newaddress", target=r["name"], detail=f"#{rid}")
    return _my_resp(True, "A new upload address is made. The old one no longer works: put the new one in your bridge.")


@bp.route("/my/drones/<int:rid>/delete", methods=["POST"])
@login_required
def my_delete(rid):
    _need()
    r = _own(rid)
    d = get_db()
    d.execute("DELETE FROM rid_stations WHERE id=%s", (rid,))
    d.commit()
    audit("rid.delete", target=r["name"], detail=f"#{rid}")
    return _my_resp(True, f"{r['name']} is removed. The drones it heard stay listed until they are not heard for a while.")


# ------------------------------------------------------------------ Admin

def _admin_ctx():
    d = get_db()
    rows = d.execute("SELECT s.*, u.username AS owner_name, u.display_name AS owner_display FROM rid_stations s LEFT JOIN users u ON u.id = s.owner_id ORDER BY lower(s.name)").fetchall()
    hid = d.execute("SELECT * FROM rid_hidden ORDER BY hidden_at DESC").fetchall()
    return {"stations": rows, "hidden": hid, "url_of": upload_url, "admin_view": True, "c": counts(), "active": "drones"}


def _admin_resp(ok, message):
    return action_response(ok, message, redirect_endpoint="drones.admin_page", fragment_template="partials/_admin_drones.html", fragment_context=_admin_ctx())


@bp.route("/admin/drones")
@permission_required("manage_feeders")
def admin_page():
    return render_template("admin_drones.html", **_admin_ctx())


def _st(rid):
    r = get_db().execute("SELECT * FROM rid_stations WHERE id=%s", (rid,)).fetchone()
    if r is None:
        abort(404)
    return r


@bp.route("/admin/drones/add", methods=["POST"])
@permission_required("manage_feeders")
def admin_add():
    d = get_db()
    who = (request.form.get("username") or "").strip()
    owner = None
    if who:
        u = d.execute("SELECT id FROM users WHERE lower(username)=lower(%s) OR lower(email)=lower(%s)", (who, who)).fetchone()
        if u is None:
            return _admin_resp(False, "No member has that user name or email address.")
        owner = u["id"]
    v, err = _clean_form(request.form)
    if err:
        return _admin_resp(False, err)
    rid = _create(owner, v)
    audit("rid.admin_add", target=v["name"], detail=f"#{rid}" + (f" for {who}" if who else ""))
    return _admin_resp(True, f"{v['name']} is added. Its upload address is in the list below.")


@bp.route("/admin/drones/<int:rid>/save", methods=["POST"])
@permission_required("manage_feeders")
def admin_save(rid):
    _st(rid)
    v, err = _clean_form(request.form)
    if err:
        return _admin_resp(False, err)
    d = get_db()
    d.execute("UPDATE rid_stations SET name=%s, place=%s, public=%s, notes=%s WHERE id=%s", (v["name"], v["place"], v["public"], v["notes"], rid))
    d.commit()
    audit("rid.admin_update", target=v["name"], detail=f"#{rid}")
    return _admin_resp(True, "Saved.")


@bp.route("/admin/drones/<int:rid>/enabled", methods=["POST"])
@permission_required("manage_feeders")
def admin_enabled(rid):
    r = _st(rid)
    on = request.form.get("on") == "1"
    d = get_db()
    d.execute("UPDATE rid_stations SET enabled=%s WHERE id=%s", (on, rid))
    d.commit()
    audit("rid.admin_switch", target=r["name"], detail=f"#{rid}: {'on' if on else 'off'}")
    return _admin_resp(True, f"{r['name']} is switched {'on' if on else 'off'}.")


@bp.route("/admin/drones/<int:rid>/newaddress", methods=["POST"])
@permission_required("manage_feeders")
def admin_newaddress(rid):
    r = _st(rid)
    _newaddress(rid)
    audit("rid.admin_newaddress", target=r["name"], detail=f"#{rid}")
    return _admin_resp(True, "A new upload address is made; the old one no longer works.")


@bp.route("/admin/drones/<int:rid>/delete", methods=["POST"])
@permission_required("manage_feeders")
def admin_delete(rid):
    r = _st(rid)
    d = get_db()
    d.execute("DELETE FROM rid_stations WHERE id=%s", (rid,))
    d.commit()
    audit("rid.admin_delete", target=r["name"], detail=f"#{rid}")
    return _admin_resp(True, f"{r['name']} is deleted.")


@bp.route("/admin/drones/hide", methods=["POST"])
@permission_required("manage_feeders")
def admin_hide():
    """The takedown list: a drone ID whose pilot asked not to be shown. It is removed from every page and its broadcasts are no longer stored."""
    ident = " ".join((request.form.get("drone_id") or "").split())
    if not ID_RE.match(ident):
        return _admin_resp(False, "A drone ID is 4 to 32 letters, numbers and dashes.")
    reason = " ".join((request.form.get("reason") or "").split())[:200]
    d = get_db()
    d.execute("INSERT INTO rid_hidden (drone_id, reason, hidden_by) VALUES (%s,%s,%s) ON CONFLICT (drone_id) DO UPDATE SET reason=EXCLUDED.reason, hidden_by=EXCLUDED.hidden_by, hidden_at=now()", (ident, reason, current_user.username))
    d.execute("DELETE FROM rid_positions WHERE drone_id=%s", (ident,))
    d.commit()
    audit("rid.hide", target=ident, detail=reason)
    return _admin_resp(True, f"Drone {ident} is hidden and will not be stored.")


@bp.route("/admin/drones/unhide", methods=["POST"])
@permission_required("manage_feeders")
def admin_unhide():
    ident = " ".join((request.form.get("drone_id") or "").split())
    d = get_db()
    d.execute("DELETE FROM rid_hidden WHERE drone_id=%s", (ident,))
    d.commit()
    audit("rid.unhide", target=ident)
    return _admin_resp(True, f"Drone {ident} shows again when it is heard.")
