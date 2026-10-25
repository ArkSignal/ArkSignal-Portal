"""My Flights: a member's own diary of flights they took, kept private to them.

Each flight is the two airports (picked from the site's airport list), the date and times, and optionally the airline and flight number, the aircraft and registration, notes and the people on board with their
class, reason for travel and seat. The times can be simple (one departure and one arrival) or detailed: for gate departure, takeoff, landing and gate arrival there is a scheduled time and an actual time,
and taxi out, air time, taxi in and total time are worked out from them. Times are typed in each airport's own local time (its zone is found from where the airport is, tzplace.py) and stored in UTC. The distance
is the great circle between the airports. Nothing here is shown to anyone but the member: administrators see counts, never a diary. Limits (flights per member, people per flight) are settings. Every change is audit-logged."""
import json
import math
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from . import airports as ap
from . import tzplace
from . import units as _units
from .action_response import action_response
from . import vault
from .audit import audit
from .db import get_db
from .settings import cfg, cfg_int

bp = Blueprint("myflights", __name__)

CLASSES = ["Economy", "Premium Economy", "Business", "First", "Private or other"]
REASONS = ["Leisure", "Business", "Crew", "Other"]
SEATS = ["Window", "Aisle", "Middle", "Pilot", "Co-pilot", "Jumpseat", "Other"]
STAGES = [("out", "Gate departure", "dep"), ("off", "Takeoff", "dep"), ("on", "Landing", "arr"), ("in", "Gate arrival", "arr")]   # which airport's time zone each is written in
FLIGHT_RE = re.compile(r"^[A-Z0-9]{2,3} ?[0-9]{1,4}[A-Z]?$")


def on():
    return cfg("myflights_enabled") == "1"


def _on():
    if not on():
        abort(404)


def _airport(aid):
    try:
        r = get_db().execute("SELECT id, ident, name, municipality, country, lat, lon, iata_code, icao_code, gps_code FROM airports WHERE id=%s", (int(aid),)).fetchone()
    except (TypeError, ValueError):
        return None
    return r


def code_of(a):
    return a["iata_code"] or a["icao_code"] or a["gps_code"] or a["ident"]


def haversine_km(la1, lo1, la2, lo2):
    r = math.radians
    dla, dlo = r(la2 - la1), r(lo2 - lo1)
    h = math.sin(dla / 2) ** 2 + math.cos(r(la1)) * math.cos(r(la2)) * math.sin(dlo / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def _zone(name):
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def to_utc(text, zone):
    """'2026-10-23T14:30' written in a place's local time -> a UTC datetime; empty -> None; anything else raises ValueError."""
    text = (text or "").strip()
    if not text:
        return None
    return datetime.fromisoformat(text).replace(tzinfo=_zone(zone)).astimezone(timezone.utc)


def local_text(dt, zone):
    """A stored UTC time as the text a datetime-local box wants, in the place's local time."""
    return dt.astimezone(_zone(zone)).strftime("%Y-%m-%dT%H:%M") if dt else ""


def _best(row, stage):
    return row.get("act_" + stage) or row.get("sched_" + stage)


def derive(row):
    """Minutes for taxi out, air time, taxi in and total time, from the actual times where there are some and the scheduled ones where not; None where the times to work it out are missing or out of order."""
    t = {s: _best(row, s) for s, _l, _w in STAGES}
    def gap(a, b):
        if t[a] is None or t[b] is None:
            return None
        m = (t[b] - t[a]).total_seconds() / 60
        return round(m) if m >= 0 else None
    return {"taxi_out": gap("out", "off"), "air": gap("off", "on"), "taxi_in": gap("on", "in"), "total": gap("out", "in") if t["out"] and t["in"] else (gap("off", "on") if row.get("mode") == "simple" else None)}


def dist_txt(km):
    """A distance in the viewer's own units (miles, nautical miles or kilometers)."""
    if km is None:
        return "-"
    fac, unit = _units._SETS.get(_units.system(), _units._SETS["metric"])["dist"]
    return f"{round(km * fac):,} {unit}"


def hm(minutes):
    if minutes is None:
        return "-"
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


def open_row(r):
    """A diary flight as a dict with its notes opened (they are sealed in the database)."""
    r = dict(r)
    if "notes" in r:
        r["notes"] = vault.unseal(r["notes"])
    return r


def open_pax(p):
    p = dict(p)
    if "name" in p:
        p["name"] = vault.unseal(p["name"])
    return p


def _load(fid):
    d = get_db()
    row = d.execute("SELECT * FROM my_flights WHERE id=%s AND user_id=%s", (fid, current_user.id)).fetchone()
    if row is None:
        abort(404)
    return open_row(row), [open_pax(p) for p in d.execute("SELECT * FROM my_flight_pax WHERE flight_id=%s ORDER BY id", (fid,)).fetchall()]


def _summary(rows):
    n = len(rows)
    km = sum(r["distance_km"] or 0 for r in rows)
    air = sum((derive(r)["air"] or derive(r)["total"] or 0) for r in rows)
    codes = {r["from_code"] for r in rows} | {r["to_code"] for r in rows}
    return {"flights": n, "km": km, "minutes": air, "airports": len(codes - {""}), "airlines": len({r["airline"].strip().lower() for r in rows if r["airline"].strip()})}


# ------------------------------------------------------------------ the list

@bp.route("/my/flights")
@login_required
def page():
    _on()
    d = get_db()
    q = (request.args.get("q") or "").strip()[:40]
    year = request.args.get("year", type=int)
    rows = [open_row(r) for r in d.execute("SELECT * FROM my_flights WHERE user_id=%s ORDER BY COALESCE(act_out, sched_out, act_off, sched_off, act_in, sched_in) DESC NULLS LAST, id DESC", (current_user.id,)).fetchall()]
    allrows = rows
    def when(r):
        return r.get("act_out") or r.get("sched_out") or r.get("act_off") or r.get("sched_off") or r.get("act_in") or r.get("sched_in")
    for r in rows:
        r["when"] = when(r)
        r["der"] = derive(r)
    years = sorted({r["when"].year for r in rows if r["when"]}, reverse=True)
    if year:
        rows = [r for r in rows if r["when"] and r["when"].year == year]
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in " ".join(str(r.get(k) or "") for k in ("from_code", "from_name", "from_city", "to_code", "to_name", "to_city", "airline", "flight_no", "aircraft_type", "registration", "notes")).lower()]
    pax = {}
    for p in d.execute("SELECT p.* FROM my_flight_pax p JOIN my_flights f ON f.id = p.flight_id WHERE f.user_id=%s ORDER BY p.id", (current_user.id,)).fetchall():
        pax.setdefault(p["flight_id"], []).append(open_pax(p))
    for r in rows[:200]:   # a flight the site's own receivers heard (same registration around the time) links to its track and replay
        r["heard"] = None
        reg = (r.get("registration") or "").replace("-", "")
        if reg and r["when"]:
            h = d.execute("SELECT hex, first_seen FROM flight_log WHERE upper(replace(reg,'-','')) = %s AND last_seen >= %s AND first_seen <= %s ORDER BY first_seen LIMIT 1", (reg.upper(), r["when"] - timedelta(hours=2), r["when"] + timedelta(hours=24))).fetchone()
            if h:
                r["heard"] = {"hex": h["hex"], "at": int(h["first_seen"].timestamp()) + 1, "from": int(h["first_seen"].timestamp()) - 120}
    return render_template("my_flights.html", rows=rows, pax=pax, summary=_summary(rows), total=len(allrows), years=years, year=year, q=q, hm=hm, dist=dist_txt, limit=cfg_int("myflights_max_per_member"), active="myflights")


# ------------------------------------------------------------------ the form

def _form_ctx(f, pax, error=""):
    return dict(f=f, pax=pax, classes=CLASSES, reasons=REASONS, seats=SEATS, stages=STAGES, max_pax=cfg_int("myflights_max_passengers"), schedules_on=__import__("app.schedules", fromlist=["configured"]).configured(), error=error, active="myflights", my_name=(current_user.display_name or current_user.username))


@bp.route("/my/flights/new")
@login_required
def new():
    _on()
    d = get_db()
    if d.execute("SELECT COUNT(*) AS n FROM my_flights WHERE user_id=%s", (current_user.id,)).fetchone()["n"] >= cfg_int("myflights_max_per_member"):
        return action_response(False, f"You can keep {cfg_int('myflights_max_per_member')} flights. Delete one to add another.", redirect_endpoint="myflights.page")
    f = {"id": None, "mode": "detailed", "from_id": "", "to_id": "", "flight_no": "", "airline": "", "aircraft_type": "", "registration": "", "notes": ""}
    for s, _l, _w in STAGES:
        f["sched_" + s] = f["act_" + s] = ""
    f["from_label"] = f["to_label"] = ""
    f["from_tz"] = f["to_tz"] = ""
    return render_template("my_flight_form.html", **_form_ctx(f, [{"name": current_user.display_name or current_user.username, "klass": "Economy", "reason": "Leisure", "seat": "", "seat_kind": ""}]))


@bp.route("/my/flights/<int:fid>/edit")
@login_required
def edit(fid):
    _on()
    row, pax = _load(fid)
    f = dict(row)
    for side in ("from", "to"):
        r = get_db().execute("SELECT id FROM airports WHERE ident=%s", (row[side + "_ident"],)).fetchone()
        f[side + "_id"] = r["id"] if r else ""
    f["from_label"] = f"{row['from_code']} {row['from_name']}"
    f["to_label"] = f"{row['to_code']} {row['to_name']}"
    for s, _l, w in STAGES:
        zone = row["from_tz"] if w == "dep" else row["to_tz"]
        f["sched_" + s] = local_text(row["sched_" + s], zone)
        f["act_" + s] = local_text(row["act_" + s], zone)
    return render_template("my_flight_form.html", **_form_ctx(f, pax))


@bp.route("/my/flights/airports.json")
@login_required
def airports_json():
    _on()
    q = (request.args.get("q") or "").strip()[:40]
    if len(q) < 2:
        return jsonify({"airports": []})
    rows, _n = ap.search(q=q, limit=12)
    return jsonify({"airports": [{"id": r["id"], "code": code_of(r), "name": r["name"], "city": r["municipality"] or "", "country": r["country"] or ""} for r in rows]})


def _zones(f_id, t_id):
    a, b = _airport(f_id), _airport(t_id)
    if a is None or b is None:
        return None, None, None, None
    return a, b, tzplace.zone_for(a["country"], a["lat"], a["lon"]), tzplace.zone_for(b["country"], b["lat"], b["lon"])


@bp.route("/my/flights/derive.json", methods=["POST"])
@login_required
def derive_json():
    """The worked-out times and distance for what is in the form so far (the form asks as the person types)."""
    _on()
    j = request.get_json(silent=True) or {}
    a, b, za, zb = _zones(j.get("from_id"), j.get("to_id"))
    if a is None:
        return jsonify({"ok": False})
    row = {"mode": j.get("mode")}
    try:
        for s, _l, w in STAGES:
            row["sched_" + s] = to_utc(j.get("sched_" + s), za if w == "dep" else zb)
            row["act_" + s] = to_utc(j.get("act_" + s), za if w == "dep" else zb)
    except ValueError:
        return jsonify({"ok": False})
    der = derive(row)
    return jsonify({"ok": True, "from_country": a["country"] or "", "to_country": b["country"] or "", "from_tz": za, "to_tz": zb, "km": round(haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])), **{k: hm(v) for k, v in der.items()}})


@bp.route("/my/flights/save", methods=["POST"])
@login_required
def save():
    _on()
    f = request.form
    d = get_db()
    fid = f.get("id", type=int)
    old = None
    if fid:
        old, _p = _load(fid)
    elif d.execute("SELECT COUNT(*) AS n FROM my_flights WHERE user_id=%s", (current_user.id,)).fetchone()["n"] >= cfg_int("myflights_max_per_member"):
        return action_response(False, f"You can keep {cfg_int('myflights_max_per_member')} flights. Delete one to add another.", redirect_endpoint="myflights.page")
    def back(msg):
        return action_response(False, msg, redirect_endpoint="myflights.new" if not fid else "myflights.edit", redirect_kwargs={"fid": fid} if fid else None)
    a, b, za, zb = _zones(f.get("from_id"), f.get("to_id"))
    if a is None:
        return back("Choose both airports from the list that appears as you type.")
    if a["id"] == b["id"]:
        return back("The two airports are the same. A flight goes from one to another.")
    mode = "simple" if f.get("mode") == "simple" else "detailed"
    times = {}
    try:
        for s, label, w in STAGES:
            times["sched_" + s] = to_utc(f.get("sched_" + s), za if w == "dep" else zb)
            times["act_" + s] = to_utc(f.get("act_" + s), za if w == "dep" else zb)
    except ValueError:
        return back("A time was not understood. Pick it with the date and time boxes.")
    if mode == "simple":   # departure and arrival only: the gate times carry them
        for k in ("sched_off", "act_off", "sched_on", "act_on"):
            times[k] = None
    if not any(times[k] for k in ("sched_out", "act_out", "sched_off", "act_off")):
        return back("A flight needs a departure date and time.")
    for kind in ("sched", "act"):
        seq = [times[f"{kind}_{s}"] for s, _l, _w in STAGES if times[f"{kind}_{s}"]]
        if any(seq[i] > seq[i + 1] for i in range(len(seq) - 1)):
            return back("The times are out of order: gate departure, takeoff, landing and gate arrival must come one after the other.")
    allt = [v for v in times.values() if v]
    if allt and (max(allt) - min(allt)) > timedelta(hours=48):
        return back("A flight cannot take more than 48 hours.")
    no = re.sub(r"\s+", " ", (f.get("flight_no") or "").strip().upper())[:8]
    if no and not FLIGHT_RE.match(no):
        return back("A flight number is the airline's letters and the number, for example WN 1672.")
    airline = " ".join((f.get("airline") or "").split())[:60]
    typ = " ".join((f.get("aircraft_type") or "").split())[:30]
    reg = re.sub(r"[^A-Z0-9-]", "", (f.get("registration") or "").upper())[:12]
    if (not airline and (no or reg)) or (not typ and reg):   # what the outside lookups know fills what was left empty (a failed lookup leaves it empty)
        try:
            from .myflightsio import suggest
            for q in [x for x in (no, reg) if x]:
                sg = suggest(q)
                airline, typ = airline or sg.get("airline", "")[:60], typ or sg.get("aircraft_type", "")[:30]
        except Exception:
            pass
    notes = (f.get("notes") or "").strip()[:2000]
    people = []
    for i in range(cfg_int("myflights_max_passengers")):
        name = " ".join((f.get(f"p{i}_name") or "").split())[:60]
        if not name:
            continue
        people.append({"name": name, "klass": f.get(f"p{i}_class") if f.get(f"p{i}_class") in CLASSES else "Economy", "reason": f.get(f"p{i}_reason") if f.get(f"p{i}_reason") in REASONS else "Leisure",
                       "seat": re.sub(r"[^A-Za-z0-9 -]", "", f.get(f"p{i}_seat") or "")[:6].upper(), "seat_kind": f.get(f"p{i}_kind") if f.get(f"p{i}_kind") in SEATS else ""})
    km = round(haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]), 1)
    cols = {"mode": mode, "flight_no": no, "airline": airline, "from_ident": a["ident"], "from_code": code_of(a), "from_name": a["name"], "from_city": a["municipality"] or "", "from_lat": a["lat"], "from_lon": a["lon"], "from_tz": za,
            "to_ident": b["ident"], "to_code": code_of(b), "to_name": b["name"], "to_city": b["municipality"] or "", "to_lat": b["lat"], "to_lon": b["lon"], "to_tz": zb,
            "aircraft_type": typ, "registration": reg, "notes": vault.seal(notes), "distance_km": km, **times}
    if fid:
        d.execute("UPDATE my_flights SET " + ", ".join(f"{k}=%s" for k in cols) + ", updated_at=now() WHERE id=%s AND user_id=%s", list(cols.values()) + [fid, current_user.id])
        d.execute("DELETE FROM my_flight_pax WHERE flight_id=%s", (fid,))
    else:
        fid = d.execute("INSERT INTO my_flights (user_id, " + ", ".join(cols) + ") VALUES (%s, " + ", ".join(["%s"] * len(cols)) + ") RETURNING id", [current_user.id] + list(cols.values())).fetchone()["id"]
    for p in people:
        d.execute("INSERT INTO my_flight_pax (flight_id, name, klass, reason, seat, seat_kind) VALUES (%s,%s,%s,%s,%s,%s)", (fid, vault.seal(p["name"]), p["klass"], p["reason"], p["seat"], p["seat_kind"]))
    d.commit()
    audit("myflights.save", target=current_user.username, detail=f"{code_of(a)} to {code_of(b)}" + (" (changed)" if old else ""))
    return action_response(True, f"The flight {code_of(a)} to {code_of(b)} is saved.", redirect_endpoint="myflights.page")


@bp.route("/my/flights/<int:fid>/delete", methods=["POST"])
@login_required
def delete(fid):
    _on()
    row, _p = _load(fid)
    d = get_db()
    d.execute("DELETE FROM my_flights WHERE id=%s AND user_id=%s", (fid, current_user.id))
    d.commit()
    audit("myflights.delete", target=current_user.username, detail=f"{row['from_code']} to {row['to_code']}")
    return action_response(True, "The flight is deleted.", redirect_endpoint="myflights.page")


# ------------------------------------------------------------------ the map of the routes and the statistics

def _own(year=None, user_id=None):
    rows = [open_row(r) for r in get_db().execute("SELECT * FROM my_flights WHERE user_id=%s ORDER BY COALESCE(act_out, sched_out, act_off, sched_off, act_in, sched_in) DESC NULLS LAST, id DESC", (user_id or current_user.id,)).fetchall()]
    for r in rows:
        r["when"] = r.get("act_out") or r.get("sched_out") or r.get("act_off") or r.get("sched_off") or r.get("act_in") or r.get("sched_in")
        r["der"] = derive(r)
    years = sorted({r["when"].year for r in rows if r["when"]}, reverse=True)
    if year:
        rows = [r for r in rows if r["when"] and r["when"].year == year]
    return rows, years


def map_data(rows):
    """The airports (with how many flights touched each) and the routes (a pair of airports, either way round, with its flights) for the map."""
    air, routes = {}, {}
    for r in rows:
        if r["from_lat"] is None or r["to_lat"] is None:
            continue
        day = r["when"].strftime("%Y-%m-%d") if r["when"] else ""
        item = {"id": r["id"], "date": day, "from": r["from_code"], "to": r["to_code"], "airline": r["airline"], "no": r["flight_no"], "km": round(r["distance_km"] or 0)}
        for side in ("from", "to"):
            a = air.setdefault(r[side + "_ident"], {"code": r[side + "_code"], "name": r[side + "_name"], "city": r[side + "_city"], "lat": r[side + "_lat"], "lon": r[side + "_lon"], "count": 0, "flights": []})
            a["count"] += 1
            a["flights"].append(item)
        key = tuple(sorted((r["from_ident"], r["to_ident"])))
        rt = routes.setdefault(key, {"a": r["from_code"], "b": r["to_code"], "alat": r["from_lat"], "alon": r["from_lon"], "blat": r["to_lat"], "blon": r["to_lon"], "count": 0, "km": round(r["distance_km"] or 0), "last": "", "flights": []})
        rt["count"] += 1
        rt["last"] = max(rt["last"], day)
        rt["flights"].append(item)
    return {"airports": list(air.values()), "routes": list(routes.values()), "tracks": _map_tracks([r["id"] for r in rows if r["from_lat"] is not None and r["to_lat"] is not None])}


def _map_tracks(ids):
    """The recorded track of each of these flights that has one, thinned to a few points ([lat, lon, feet]) so the map stays light; the flight's own page holds the full track."""
    if not ids:
        return []
    cap = max(20, cfg_int("myflights_map_track_points") or 150)
    out = []
    for r in get_db().execute("SELECT flight_id, points FROM my_flight_tracks WHERE flight_id = ANY(%s)", (ids,)).fetchall():
        pts = json.loads(r["points"]) if isinstance(r["points"], str) else r["points"]
        if len(pts) < 2:
            continue
        step = max(1, math.ceil(len(pts) / cap))
        keep = pts[::step]
        if keep[-1] is not pts[-1]:
            keep.append(pts[-1])
        out.append({"id": r["flight_id"], "pts": [[p[1], p[2], p[3]] for p in keep]})
    return out


@bp.route("/my/flights/map")
@login_required
def map_page():
    _on()
    year = request.args.get("year", type=int)
    rows, years = _own(year)
    return render_template("my_flights_map.html", data=map_data(rows), years=years, year=year, summary=_summary(rows), dist=dist_txt, map_lat=cfg("map_lat"), map_lon=cfg("map_lon"), active="flightmap")


@bp.route("/my/flights/map.json")
@login_required
def map_json():
    _on()
    rows, years = _own(request.args.get("year", type=int))
    return jsonify(dict(map_data(rows), years=years))


def _top(counter, n=8):
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def _pax_of(d, user_id):
    pax = {}
    for p in d.execute("SELECT p.* FROM my_flight_pax p JOIN my_flights f ON f.id = p.flight_id WHERE f.user_id=%s", (user_id,)).fetchall():
        pax.setdefault(p["flight_id"], []).append(open_pax(p))
    return pax


def stats_ctx(rows, pax):
    """Everything the statistics boards need, from a diary's rows and the people on its flights (empty when the people are not to be counted)."""
    from collections import Counter
    ids = {r["id"] for r in rows}
    airports, airlines, types, routes, classes, reasons, countries, by_year = Counter(), Counter(), Counter(), Counter(), Counter(), Counter(), Counter(), {}
    labels = {}
    for r in rows:
        for side in ("from", "to"):
            airports[r[side + "_code"]] += 1
            labels[r[side + "_code"]] = r[side + "_city"] or r[side + "_name"]
            if r.get(side + "_country"):
                countries[r[side + "_country"]] += 1
        if r["airline"].strip():
            airlines[r["airline"].strip()] += 1
        if r["aircraft_type"].strip():
            types[r["aircraft_type"].strip().upper()] += 1
        routes[" - ".join(sorted((r["from_code"], r["to_code"])))] += 1
        if r["when"]:
            y = by_year.setdefault(r["when"].year, {"flights": 0, "km": 0.0})
            y["flights"] += 1
            y["km"] += r["distance_km"] or 0
    for fid, ps in pax.items():
        if fid in ids:
            for p in ps:
                classes[p["klass"]] += 1
                reasons[p["reason"]] += 1
    s = _summary(rows)
    timed = [r for r in rows if (r["der"]["air"] or r["der"]["total"])]
    longest = sorted(rows, key=lambda r: -(r["distance_km"] or 0))[:3]
    shortest = sorted([r for r in rows if r["distance_km"]], key=lambda r: r["distance_km"])[:3]
    longest_time = sorted(timed, key=lambda r: -(r["der"]["air"] or r["der"]["total"] or 0))[:3]
    earth = round(s["km"] / 40075.0, 2) if s["km"] else 0
    moon = round(s["km"] / 384400.0, 3) if s["km"] else 0
    return dict(s=s, airports=_top(airports), airlines=_top(airlines), types=_top(types), routes=_top(routes), classes=_top(classes), reasons=_top(reasons), countries=_top(countries),
                by_year=sorted(by_year.items()), labels=labels, longest=longest, shortest=shortest, longest_time=longest_time, earth=earth, moon=moon, total=len(rows),
                avg_km=round(s["km"] / s["flights"]) if s["flights"] else 0)


@bp.route("/my/flights/stats")
@login_required
def stats_page():
    _on()
    d = get_db()
    d.execute("UPDATE my_flights f SET from_country = COALESCE((SELECT country FROM airports a WHERE a.ident = f.from_ident LIMIT 1), '') WHERE f.user_id=%s AND f.from_country = ''", (current_user.id,))
    d.execute("UPDATE my_flights f SET to_country = COALESCE((SELECT country FROM airports a WHERE a.ident = f.to_ident LIMIT 1), '') WHERE f.user_id=%s AND f.to_country = ''", (current_user.id,))
    d.commit()
    year = request.args.get("year", type=int)
    rows, years = _own(year)
    pax = _pax_of(d, current_user.id)
    return render_template("my_flights_stats.html", years=years, year=year, dist=dist_txt, hm=hm, active="flightstats", **stats_ctx(rows, pax))
