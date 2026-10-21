"""River traffic: the barges, towboats, tugs and boats on the Arkansas, White and Mississippi rivers (and the lakes) that members' own AIS receivers hear.

AIS is the radio beacon that every commercial vessel, and many pleasure boats, send a few times a minute: who it is (a nine-digit MMSI number and a name), where it is, how fast and which way, what it is and
where it is going. A member with an AIS-catcher or rtl_ais receiver near water sends the sentences it decodes to a private address on this site (`/ais/push/<key>`, normally through the small bridge
script `static/ais_bridge.py`); the site decodes them itself (`aisdecode.py`), keeps each vessel's newest position and details, a short trail, and shows them on a list, a map and a page for each vessel.

The data is what vessels broadcast openly; nothing else is added. Administrators can hide a vessel on request (the takedown list) and everything a member adds, switches off or deletes is in the audit log."""
import hashlib
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from . import aisdecode, csrf, vault
from .action_response import action_response
from .admin import permission_required
from .audit import audit
from .cache import cached
from .db import get_db
from .settings import cfg, cfg_int, map_area

bp = Blueprint("ais", __name__)

CATEGORIES = {"tow": ("Towboats and tugs", "#ffb454"), "cargo": ("Cargo ships and barges", "#4fb3ff"), "tanker": ("Tankers", "#ff6b6b"), "passenger": ("Passenger vessels", "#c58bff"),
              "pleasure": ("Pleasure craft and sailing", "#3ee6a8"), "fishing": ("Fishing", "#8fe36e"), "service": ("Service, pilot, rescue and law enforcement", "#ffd24f"), "other": ("Other", "#9fb0c8"),
              "unknown": ("Type not stated", "#7b8794")}
CREDIT = "Positions and details are what vessels broadcast over AIS, received by members' own receivers"
_asm, _trail, _state = {}, {}, {"cleaned": 0.0}


def enabled():
    return cfg("ais_enabled") == "1"


def _need():
    if not enabled():
        abort(404)


def token_hash(token):
    return hashlib.sha256(("ais\0" + token).encode()).hexdigest()


def _area():
    try:
        s, w, n, e = (float(x) for x in cfg("ais_area").split(","))
        return s, w, n, e
    except ValueError:
        return -90.0, -180.0, 90.0, 180.0


def _hidden():
    return {r["mmsi"] for r in get_db().execute("SELECT mmsi FROM ais_hidden").fetchall()}


def counts():
    d = get_db()
    r = d.execute("SELECT COUNT(*) FILTER (WHERE last_seen > now() - make_interval(mins => %s) AND lat IS NOT NULL) AS now, COUNT(*) FILTER (WHERE last_seen > now() - interval '24 hours') AS day, COUNT(*) AS total "
                  "FROM ais_vessels WHERE mmsi NOT IN (SELECT mmsi FROM ais_hidden)", (cfg_int("ais_underway_minutes"),)).fetchone()
    s = d.execute("SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE enabled AND last_report > now() - interval '24 hours') AS active, COUNT(*) FILTER (WHERE enabled AND last_report IS NULL) AS silent FROM ais_stations").fetchone()
    return {"now": r["now"], "day": r["day"], "total": r["total"], "stations": s["n"], "active": s["active"], "silent": s["silent"]}


# ------------------------------------------------------------------ taking what a receiver sends

def _ship_json(s):
    """One vessel from AIS-catcher's JSON output, in the same fields the decoder gives."""
    try:
        m = int(s.get("mmsi"))
    except (TypeError, ValueError):
        return None
    out = {"mmsi": m, "type": 0}
    lat, lon = s.get("lat"), s.get("lon")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and lat != 91 and lon != 181 and -90 <= lat <= 90 and -180 <= lon <= 180:
        out.update(lat=float(lat), lon=float(lon))
        for k_in, k_out in (("speed", "speed"), ("course", "course"), ("heading", "heading"), ("status", "status")):
            v = s.get(k_in)
            if isinstance(v, (int, float)) and not (k_out == "heading" and v == 511) and not (k_out == "speed" and v >= 102.3) and not (k_out == "course" and v >= 360):
                out[k_out] = v
    for k_in, k_out in (("shipname", "name"), ("callsign", "callsign"), ("destination", "destination")):
        if isinstance(s.get(k_in), str) and s[k_in].strip():
            out[k_out] = " ".join(s[k_in].replace("@", " ").split())[:30]
    for k in ("imo", "to_bow", "to_stern", "to_port", "to_starboard"):
        if isinstance(s.get(k), int) and s[k] > 0:
            out[k] = s[k]
    if isinstance(s.get("shiptype"), int) and s["shiptype"] > 0:
        out["ship_type"] = s["shiptype"]
    if isinstance(s.get("draught"), (int, float)) and s["draught"] > 0:
        out["draught"] = float(s["draught"])
    return out


def _messages(body, ctype, asm, now):
    """The decoded messages in a posted body: NMEA lines (text), a JSON list of NMEA strings, {"msgs": [...]} or AIS-catcher's {"ships": [...]}."""
    msgs, lines = [], []
    text = body.decode("utf-8", "replace")
    data = None
    if text.lstrip()[:1] in "[{":
        try:
            data = json.loads(text)
        except ValueError:
            data = None
    if data is None:
        lines = text.splitlines()
    elif isinstance(data, list):
        lines = [x for x in data if isinstance(x, str)]
        msgs += [m for m in (_ship_json(x) for x in data if isinstance(x, dict)) if m]
    elif isinstance(data, dict):
        for key in ("msgs", "nmea", "sentences", "lines"):
            if isinstance(data.get(key), list):
                lines += [x for x in data[key] if isinstance(x, str)]
        if isinstance(data.get("ships"), list):
            msgs += [m for m in (_ship_json(x) for x in data["ships"] if isinstance(x, dict)) if m]
    for ln in lines[:cfg_int("ais_max_batch")]:
        m = asm.feed(ln, now)
        if m:
            msgs.append(m)
    return msgs, len(lines)


def _store(d, station_id, msgs, now):
    """Fold the messages of one batch into one position and one set of details per vessel, then write them."""
    south, west, north, east = _area()
    hidden = _hidden()
    by = {}
    for m in msgs:
        mmsi = m["mmsi"]
        if not 100000000 <= mmsi <= 799999999 or mmsi in hidden:
            continue
        v = by.setdefault(mmsi, {"n": 0})
        v["n"] += 1
        pos_keys = ("lat", "lon", "speed", "course", "heading", "status")
        if m.get("type") in (1, 2, 3, 18, 19) or "lat" in m:   # a position report: kept only when it has a place inside the area
            lat, lon = m.get("lat"), m.get("lon")
            if lat is None or lon is None or not (south <= lat <= north and west <= lon <= east):
                m = {k: val for k, val in m.items() if k not in pos_keys}
        for k, val in m.items():
            if k in ("type", "mmsi", "cls") or val in (None, ""):
                continue
            v[k] = val
    stored = 0
    for mmsi, v in by.items():
        n = v.pop("n")
        if "lat" in v and "lon" not in v:
            v.pop("lat")
        if "lon" in v and "lat" not in v:
            v.pop("lon")
        cols = [c for c in ("lat", "lon", "speed", "course", "heading", "status", "name", "callsign", "imo", "ship_type", "to_bow", "to_stern", "to_port", "to_starboard", "draught", "destination") if c in v]
        if not cols:
            continue
        d.execute("INSERT INTO ais_vessels (mmsi, first_seen, last_seen, station_id) VALUES (%s, now(), now(), %s) ON CONFLICT (mmsi) DO NOTHING", (mmsi, station_id))
        sets = ", ".join(f"{c}=%s" for c in cols)
        d.execute(f"UPDATE ais_vessels SET {sets}, last_seen=now(), station_id=%s, msgs=msgs+%s WHERE mmsi=%s", [v[c] for c in cols] + [station_id, n, mmsi])
        if "lat" in v and "lon" in v:
            if now - _trail.get(mmsi, 0) >= cfg_int("ais_trail_seconds"):
                d.execute("INSERT INTO ais_positions (mmsi, lat, lon, speed, course) VALUES (%s,%s,%s,%s,%s)", (mmsi, v["lat"], v["lon"], v.get("speed"), v.get("course")))
                _trail[mmsi] = now
        stored += 1
    return stored


def _clean(d, now):
    if now - _state["cleaned"] < 600:
        return
    _state["cleaned"] = now
    d.execute("DELETE FROM ais_positions WHERE ts < now() - make_interval(hours => %s)", (cfg_int("ais_keep_hours"),))
    d.execute("DELETE FROM ais_vessels WHERE last_seen < now() - make_interval(days => %s)", (cfg_int("ais_vessel_days"),))
    for k in [k for k, t in _trail.items() if now - t > 3600]:
        del _trail[k]


@bp.route("/ais/push/<token>", methods=["POST"])
@csrf.exempt
def push(token):
    """A batch from a receiver: NMEA sentences, one per line, or JSON (see `_messages`). The answer says how many sentences came in and how many vessels were updated."""
    if not enabled():
        return jsonify(error="switched_off"), 404
    d = get_db()
    st = d.execute("SELECT * FROM ais_stations WHERE token_hash=%s", (token_hash(token),)).fetchone()
    if st is None or not st["enabled"]:
        return jsonify(error="unknown_address"), 404
    if request.content_length and request.content_length > 2 * 1024 * 1024:
        return jsonify(error="too_large"), 413
    now = time.time()
    asm = _asm.setdefault(st["id"], aisdecode.Assembler())
    msgs, lines = _messages(request.get_data(), request.content_type or "", asm, now)
    n = _store(d, st["id"], msgs, now)
    d.execute("UPDATE ais_stations SET last_report=now(), msgs=msgs+%s WHERE id=%s", (len(msgs), st["id"]))
    _clean(d, now)
    d.commit()
    return jsonify(ok=True, sentences=lines, messages=len(msgs), vessels=n)


# ------------------------------------------------------------------ what the pages read

def _vessel_row(r):
    length = (r["to_bow"] or 0) + (r["to_stern"] or 0)
    beam = (r["to_port"] or 0) + (r["to_starboard"] or 0)
    cat = aisdecode.category(r["ship_type"])
    return {"mmsi": r["mmsi"], "name": r["name"] or "", "callsign": r["callsign"] or "", "imo": r["imo"], "ship_type": r["ship_type"], "type_name": aisdecode.ship_type_name(r["ship_type"]), "category": cat,
            "status": r["status"], "status_name": aisdecode.STATUS.get(r["status"], "") if r["status"] is not None else "", "lat": r["lat"], "lon": r["lon"], "speed": r["speed"], "course": r["course"],
            "heading": r["heading"], "length": length or None, "beam": beam or None, "draught": r["draught"], "destination": r["destination"] or "", "first_seen": r["first_seen"].isoformat(), "last_seen": r["last_seen"].isoformat(),
            "msgs": r["msgs"], "station": r.get("station_name") or ""}


SELECT = ("SELECT v.*, CASE WHEN s.public THEN s.name ELSE '' END AS station_name FROM ais_vessels v LEFT JOIN ais_stations s ON s.id = v.station_id "
          "WHERE v.mmsi NOT IN (SELECT mmsi FROM ais_hidden) ")


def vessels(hours=None, q="", cat=""):
    hours = hours or cfg_int("ais_list_hours")
    rows = get_db().execute(SELECT + "AND v.last_seen > now() - make_interval(hours => %s) ORDER BY v.last_seen DESC", (hours,)).fetchall()
    out = []
    hide_pl = cfg("ais_hide_pleasure") == "1"
    for r in rows:
        x = _vessel_row(r)
        if hide_pl and x["category"] == "pleasure":
            continue
        if cat and x["category"] != cat:
            continue
        if q and q.lower() not in (x["name"] + " " + str(x["mmsi"]) + " " + x["callsign"] + " " + x["destination"]).lower():
            continue
        out.append(x)
    return out


def _cached_vessels():
    """The recent vessels, kept 15 seconds (the cache holds every vessel; the takedown list and the pleasure craft setting are applied on every read, so a change shows at once)."""
    raw = cached(("ais_vessels_raw",), lambda: [_vessel_row(r) for r in get_db().execute(SELECT + "AND v.last_seen > now() - make_interval(hours => %s) ORDER BY v.last_seen DESC", (cfg_int("ais_list_hours"),)).fetchall()], 15)
    hid = _hidden()
    hide_pl = cfg("ais_hide_pleasure") == "1"
    return [v for v in raw if v["mmsi"] not in hid and not (hide_pl and v["category"] == "pleasure")]


def flag_of(mmsi):
    """A rough origin of an MMSI from its first three digits (the maritime identification digits); only the ones this region sees are named."""
    return {"366": "United States", "367": "United States", "368": "United States", "369": "United States", "316": "Canada", "338": "United States"}.get(str(mmsi)[:3], "")


@bp.route("/api/ais/vessels.json")
def api_vessels():
    _need()
    hrs = cfg_int("ais_list_hours")
    return jsonify({"vessels": _cached_vessels(), "hours": hrs, "credit": CREDIT, "now_minutes": cfg_int("ais_underway_minutes")})


@bp.route("/api/ais/<int:mmsi>.json")
def api_vessel(mmsi):
    _need()
    r = get_db().execute(SELECT + "AND v.mmsi=%s", (mmsi,)).fetchone()
    if r is None:
        abort(404)
    trail = get_db().execute("SELECT ts, lat, lon, speed, course FROM ais_positions WHERE mmsi=%s ORDER BY ts DESC LIMIT 500", (mmsi,)).fetchall()
    return jsonify({"vessel": _vessel_row(r), "trail": [[t["lat"], t["lon"], t["ts"].isoformat(), t["speed"]] for t in reversed(trail)], "credit": CREDIT})


@bp.route("/api/ais.geojson")
def api_geojson():
    """The Marine Traffic layer for every map: a dot for each vessel heard lately, colored by kind, with its card."""
    from flask import Response
    if not enabled():
        return Response('{"type":"FeatureCollection","features":[]}', mimetype="application/geo+json")
    feats = []
    now = datetime.now(timezone.utc)
    for v in _cached_vessels():
        if v["lat"] is None:
            continue
        age = (now - datetime.fromisoformat(v["last_seen"])).total_seconds() / 60
        if age > cfg_int("ais_underway_minutes") * 6:
            continue
        nm = v["name"] or ("MMSI " + str(v["mmsi"]))
        rows = [["Kind", v["type_name"]], ["MMSI", str(v["mmsi"])]]
        if v["callsign"]:
            rows.append(["Call sign", v["callsign"]])
        if v["speed"] is not None:
            rows.append(["Speed", f"{v['speed']:.1f} knots ({v['speed'] * 1.15078:.1f} mph)"])
        if v["course"] is not None:
            rows.append(["Course", f"{v['course']:.0f}°"])
        if v["length"]:
            rows.append(["Size", f"{v['length']} by {v['beam'] or '?'} meters"])
        if v["destination"]:
            rows.append(["Going to", v["destination"]])
        if v["status_name"]:
            rows.append(["Status", v["status_name"]])
        rows.append(["Heard", v["last_seen"], "t"])
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(v["lon"], 5), round(v["lat"], 5)]}, "properties": {
            "KIND": v["category"], "size": 7, "event": nm, "headline": v["type_name"] + (f", {v['speed']:.0f} knots" if v["speed"] else ", stopped" if v["speed"] == 0 else ""), "rows": rows,
            "link": f"/ais/{v['mmsi']}", "linkText": "Vessel Page"}})
    resp = Response(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")), mimetype="application/geo+json")
    resp.headers["Cache-Control"] = "public, max-age=15"
    return resp


# ------------------------------------------------------------------ the public pages

def _icount(vs):
    out = {k: 0 for k in CATEGORIES}
    for v in vs:
        out[v["category"]] += 1
    return out


@bp.route("/ais/")
def overview():
    _need()
    q = " ".join(request.args.get("q", "").split())[:60]
    cat = request.args.get("cat", "")
    hours = request.args.get("hours", "")
    hrs = int(hours) if hours.isdigit() and 1 <= int(hours) <= 720 else cfg_int("ais_list_hours")
    rows = vessels(hrs, q, cat if cat in CATEGORIES else "")
    c = counts()
    return render_template("ais.html", rows=rows[:500], total=len(rows), c=c, q=q, cat=cat, hours=hrs, cats=CATEGORIES, by_cat=_icount(vessels(hrs)), credit=CREDIT, underway=cfg_int("ais_underway_minutes"))


@bp.route("/ais/map")
def ais_map():
    _need()
    from . import maplayers
    return render_template("ais_map.html", layers=maplayers.for_map(), label_scale=cfg("live_label_scale"), label_bg=cfg("live_label_bg"), map_lat=cfg("map_lat"), map_lon=cfg("map_lon"), map_zoom=cfg("map_zoom"),
                           wx_refresh=cfg_int("weather_refresh_minutes"), credit=CREDIT, cats=CATEGORIES, underway=cfg_int("ais_underway_minutes"), hours=cfg_int("ais_list_hours"))


@bp.route("/ais/<int:mmsi>")
def vessel_page(mmsi):
    _need()
    r = get_db().execute(SELECT + "AND v.mmsi=%s", (mmsi,)).fetchone()
    if r is None:
        abort(404)
    return render_template("ais_vessel.html", v=_vessel_row(r), flag=flag_of(mmsi), credit=CREDIT, cats=CATEGORIES, underway=cfg_int("ais_underway_minutes"), keep=cfg_int("ais_keep_hours"), map_lat=cfg("map_lat"), map_lon=cfg("map_lon"))


@bp.route("/feed/ais")
def feed_page():
    _need()
    base = "https://" + (cfg("feed_host") or cfg("main_address") or request.host)
    return render_template("feed_ais.html", fsub="ais", base=base, bridge=base + "/static/ais_bridge.py", keep=cfg_int("ais_keep_hours"), area=cfg("ais_area"), per_user=cfg_int("ais_per_user"))


# ------------------------------------------------------------------ the owner's pages

def _clean_form(f):
    name = " ".join((f.get("name") or "").split())[:60]
    if len(name) < 3:
        return None, "Give the station a name of at least three letters."
    return {"name": name, "place": " ".join((f.get("place") or "").split())[:120], "public": f.get("public") == "1", "notes": (f.get("notes") or "").strip()[:500]}, None


def upload_url(r):
    return request.host_url.rstrip("/") + "/ais/push/" + vault.unseal(r["token_sealed"])


def _create(owner, v):
    d = get_db()
    t = secrets.token_urlsafe(24)
    rid = d.execute("INSERT INTO ais_stations (owner_id, name, place, public, notes, token_hash, token_sealed) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (owner, v["name"], v["place"], v["public"], v["notes"], token_hash(t), vault.seal(t))).fetchone()["id"]
    d.commit()
    return rid


def _my_ctx():
    rows = get_db().execute("SELECT * FROM ais_stations WHERE owner_id=%s ORDER BY lower(name)", (current_user.id,)).fetchall()
    return {"stations": rows, "limit": cfg_int("ais_per_user"), "url_of": upload_url, "admin_view": False}


def _my_resp(ok, message):
    return action_response(ok, message, redirect_endpoint="ais.my_page", fragment_template="partials/_my_ais.html", fragment_context=_my_ctx())


@bp.route("/my/ais")
@login_required
def my_page():
    _need()
    return render_template("my_ais.html", **_my_ctx())


@bp.route("/my/ais/add", methods=["POST"])
@login_required
def my_add():
    _need()
    if get_db().execute("SELECT COUNT(*) AS n FROM ais_stations WHERE owner_id=%s", (current_user.id,)).fetchone()["n"] >= cfg_int("ais_per_user"):
        return _my_resp(False, f"You can have {cfg_int('ais_per_user')} AIS stations. Remove one to add another.")
    v, err = _clean_form(request.form)
    if err:
        return _my_resp(False, err)
    rid = _create(current_user.id, v)
    audit("ais.add", target=v["name"], detail=f"#{rid}")
    return _my_resp(True, f"{v['name']} is added. Its upload address is below: give it to the bridge or to your AIS receiver's software.")


def _own(rid):
    r = get_db().execute("SELECT * FROM ais_stations WHERE id=%s AND owner_id=%s", (rid, current_user.id)).fetchone()
    if r is None:
        abort(404)
    return r


@bp.route("/my/ais/<int:rid>/save", methods=["POST"])
@login_required
def my_save(rid):
    _need()
    _own(rid)
    v, err = _clean_form(request.form)
    if err:
        return _my_resp(False, err)
    d = get_db()
    d.execute("UPDATE ais_stations SET name=%s, place=%s, public=%s, notes=%s WHERE id=%s", (v["name"], v["place"], v["public"], v["notes"], rid))
    d.commit()
    audit("ais.update", target=v["name"], detail=f"#{rid}")
    return _my_resp(True, "Saved.")


def _newaddress(rid):
    t = secrets.token_urlsafe(24)
    d = get_db()
    d.execute("UPDATE ais_stations SET token_hash=%s, token_sealed=%s WHERE id=%s", (token_hash(t), vault.seal(t), rid))
    d.commit()


@bp.route("/my/ais/<int:rid>/newaddress", methods=["POST"])
@login_required
def my_newaddress(rid):
    _need()
    r = _own(rid)
    _newaddress(rid)
    audit("ais.newaddress", target=r["name"], detail=f"#{rid}")
    return _my_resp(True, "A new upload address is made. The old one no longer works: put the new one in your bridge.")


@bp.route("/my/ais/<int:rid>/delete", methods=["POST"])
@login_required
def my_delete(rid):
    _need()
    r = _own(rid)
    d = get_db()
    d.execute("DELETE FROM ais_stations WHERE id=%s", (rid,))
    d.commit()
    audit("ais.delete", target=r["name"], detail=f"#{rid}")
    return _my_resp(True, f"{r['name']} is removed. The vessels it heard stay on the map until they are not heard for a while.")


# ------------------------------------------------------------------ Admin

def _admin_ctx():
    d = get_db()
    rows = d.execute("SELECT s.*, u.username AS owner_name, u.display_name AS owner_display FROM ais_stations s LEFT JOIN users u ON u.id = s.owner_id ORDER BY lower(s.name)").fetchall()
    hid = d.execute("SELECT h.*, v.name FROM ais_hidden h LEFT JOIN ais_vessels v ON v.mmsi = h.mmsi ORDER BY h.hidden_at DESC").fetchall()
    return {"stations": rows, "hidden": hid, "url_of": upload_url, "admin_view": True, "c": counts(), "active": "ais"}


def _admin_resp(ok, message):
    return action_response(ok, message, redirect_endpoint="ais.admin_page", fragment_template="partials/_admin_ais.html", fragment_context=_admin_ctx())


@bp.route("/admin/ais")
@permission_required("manage_feeders")
def admin_page():
    return render_template("admin_ais.html", **_admin_ctx())


def _st(rid):
    r = get_db().execute("SELECT * FROM ais_stations WHERE id=%s", (rid,)).fetchone()
    if r is None:
        abort(404)
    return r


@bp.route("/admin/ais/add", methods=["POST"])
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
    audit("ais.admin_add", target=v["name"], detail=f"#{rid}" + (f" for {who}" if who else ""))
    return _admin_resp(True, f"{v['name']} is added. Its upload address is in the list below.")


@bp.route("/admin/ais/<int:rid>/save", methods=["POST"])
@permission_required("manage_feeders")
def admin_save(rid):
    _st(rid)
    v, err = _clean_form(request.form)
    if err:
        return _admin_resp(False, err)
    d = get_db()
    d.execute("UPDATE ais_stations SET name=%s, place=%s, public=%s, notes=%s WHERE id=%s", (v["name"], v["place"], v["public"], v["notes"], rid))
    d.commit()
    audit("ais.admin_update", target=v["name"], detail=f"#{rid}")
    return _admin_resp(True, "Saved.")


@bp.route("/admin/ais/<int:rid>/enabled", methods=["POST"])
@permission_required("manage_feeders")
def admin_enabled(rid):
    r = _st(rid)
    on = request.form.get("on") == "1"
    d = get_db()
    d.execute("UPDATE ais_stations SET enabled=%s WHERE id=%s", (on, rid))
    d.commit()
    audit("ais.admin_switch", target=r["name"], detail=f"#{rid}: {'on' if on else 'off'}")
    return _admin_resp(True, f"{r['name']} is switched {'on' if on else 'off'}.")


@bp.route("/admin/ais/<int:rid>/newaddress", methods=["POST"])
@permission_required("manage_feeders")
def admin_newaddress(rid):
    r = _st(rid)
    _newaddress(rid)
    audit("ais.admin_newaddress", target=r["name"], detail=f"#{rid}")
    return _admin_resp(True, "A new upload address is made; the old one no longer works.")


@bp.route("/admin/ais/<int:rid>/delete", methods=["POST"])
@permission_required("manage_feeders")
def admin_delete(rid):
    r = _st(rid)
    d = get_db()
    d.execute("DELETE FROM ais_stations WHERE id=%s", (rid,))
    d.commit()
    audit("ais.admin_delete", target=r["name"], detail=f"#{rid}")
    return _admin_resp(True, f"{r['name']} is deleted.")


@bp.route("/admin/ais/hide", methods=["POST"])
@permission_required("manage_feeders")
def admin_hide():
    """The takedown list: a vessel whose owner asked not to be shown. It is removed from every page and its broadcasts are no longer stored."""
    raw = re.sub(r"\D", "", request.form.get("mmsi") or "")
    if not re.fullmatch(r"[1-7]\d{8}", raw):
        return _admin_resp(False, "An MMSI is nine digits.")
    reason = " ".join((request.form.get("reason") or "").split())[:200]
    d = get_db()
    d.execute("INSERT INTO ais_hidden (mmsi, reason, hidden_by) VALUES (%s,%s,%s) ON CONFLICT (mmsi) DO UPDATE SET reason=EXCLUDED.reason, hidden_by=EXCLUDED.hidden_by, hidden_at=now()", (int(raw), reason, current_user.username))
    d.execute("DELETE FROM ais_positions WHERE mmsi=%s", (int(raw),))
    d.commit()
    audit("ais.hide", target=raw, detail=reason)
    return _admin_resp(True, f"MMSI {raw} is hidden and will not be stored.")


@bp.route("/admin/ais/unhide/<int:mmsi>", methods=["POST"])
@permission_required("manage_feeders")
def admin_unhide(mmsi):
    d = get_db()
    d.execute("DELETE FROM ais_hidden WHERE mmsi=%s", (mmsi,))
    d.commit()
    audit("ais.unhide", target=str(mmsi))
    return _admin_resp(True, f"MMSI {mmsi} shows again when it is heard.")
