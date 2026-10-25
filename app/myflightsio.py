"""My Flights, the ways in and out: a CSV file of the diary (download and upload) and a look-up that suggests flights from what the site's own receivers heard (by flight number or registration around a date)."""
import csv
import io
import re
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, jsonify, render_template, request
from flask_login import current_user, login_required

from . import lookups, tzplace
from .action_response import action_response
from . import vault
from .audit import audit
from .db import get_db
from .myflights import open_pax, open_row, CLASSES, FLIGHT_RE, REASONS, SEATS, STAGES, _airport, _on, code_of, haversine_km, local_text, to_utc
from .settings import cfg, cfg_int

bp = Blueprint("myflightsio", __name__)

CSV_COLS = ["date", "from", "to", "flight", "airline", "aircraft", "registration", "mode", "sched_out", "act_out", "sched_off", "act_off", "sched_on", "act_on", "sched_in", "act_in", "notes", "people"]
CSV_MAX_BYTES = 1_000_000
CSV_MAX_ROWS = 5000
_ALIASES = {"flight_no": "flight", "flight_number": "flight", "origin": "from", "departure": "from", "dep": "from", "destination": "to", "arrival": "to", "arr": "to", "aircraft_type": "aircraft", "type": "aircraft",
            "reg": "registration", "tail": "registration", "dep_time": "act_out", "departure_time": "act_out", "arr_time": "act_in", "arrival_time": "act_in", "note": "notes", "comment": "notes", "passengers": "people"}


def _csv_safe(v):
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v   # a spreadsheet must not run a cell as a formula


def export_rows(user_id):
    d = get_db()
    pax = {}
    for p in d.execute("SELECT p.* FROM my_flight_pax p JOIN my_flights f ON f.id = p.flight_id WHERE f.user_id=%s ORDER BY p.id", (user_id,)).fetchall():
        pax.setdefault(p["flight_id"], []).append(open_pax(p))
    out = []
    for r in map(open_row, d.execute("SELECT * FROM my_flights WHERE user_id=%s ORDER BY COALESCE(act_out, sched_out, act_off, sched_off) ASC NULLS LAST, id", (user_id,)).fetchall()):
        row = {"from": r["from_code"], "to": r["to_code"], "flight": r["flight_no"], "airline": r["airline"], "aircraft": r["aircraft_type"], "registration": r["registration"], "mode": r["mode"], "notes": r["notes"],
               "people": "; ".join(f"{p['name']}|{p['klass']}|{p['reason']}|{p['seat']}|{p['seat_kind']}" for p in pax.get(r["id"], []))}
        first = r["act_out"] or r["sched_out"] or r["act_off"] or r["sched_off"]
        row["date"] = local_text(first, r["from_tz"])[:10] if first else ""
        for s, _l, w in STAGES:
            for kind in ("sched", "act"):
                row[f"{kind}_{s}"] = local_text(r[f"{kind}_{s}"], r["from_tz"] if w == "dep" else r["to_tz"]).replace("T", " ")
        out.append(row)
    return out


@bp.route("/my/flights/export.csv")
@login_required
def export_csv():
    _on()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLS)
    rows = export_rows(current_user.id)
    for r in rows:
        w.writerow([_csv_safe(r.get(c, "")) for c in CSV_COLS])
    audit("myflights.export", target=current_user.username, detail=f"{len(rows)} flights")
    return Response("﻿" + buf.getvalue(), mimetype="text/csv", headers={"Content-Disposition": 'attachment; filename="my-flights.csv"'})


def _find_airport(code):
    code = (code or "").strip().upper()
    if not code:
        return None
    r = get_db().execute("SELECT id FROM airports WHERE iata_code=%s OR icao_code=%s OR gps_code=%s OR ident=%s ORDER BY (iata_code=%s) DESC, id LIMIT 1", (code, code, code, code, code)).fetchone()
    return _airport(r["id"]) if r else None


def _local_time(text, zone):
    """A time from a file: None when empty or only a date, False when it cannot be read."""
    text = (text or "").strip().replace(" ", "T", 1)
    if len(text) <= 10:
        return None
    try:
        return to_utc(text, zone)
    except ValueError:
        return False


def import_csv(text, user_id):
    """Add the flights of a CSV file to a diary: returns (added, [(line, why), ...]). Airports are found by IATA or ICAO code and times are local to each airport (YYYY-MM-DD HH:MM); a row needs from, to and a date or a departure time."""
    d = get_db()
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    try:
        head = [_ALIASES.get(h.strip().lower().replace(" ", "_"), h.strip().lower().replace(" ", "_")) for h in next(reader)]
    except StopIteration:
        return 0, [(1, "The file is empty.")]
    if "from" not in head or "to" not in head:
        return 0, [(1, "The first line must name the columns, and must include from and to.")]
    room = cfg_int("myflights_max_per_member") - d.execute("SELECT COUNT(*) AS n FROM my_flights WHERE user_id=%s", (user_id,)).fetchone()["n"]
    added, bad = 0, []
    for n, cells in enumerate(reader, start=2):
        if not any(c.strip() for c in cells):
            continue
        if n > CSV_MAX_ROWS + 1:
            bad.append((n, f"Only the first {CSV_MAX_ROWS} lines are read."))
            break
        row = {h: (cells[i].strip() if i < len(cells) else "") for i, h in enumerate(head)}
        if added >= room:
            bad.append((n, "Your diary is full."))
            break
        a, b = _find_airport(row.get("from")), _find_airport(row.get("to"))
        if a is None or b is None:
            bad.append((n, f"Airport not found: {row.get('from') if a is None else row.get('to')}."))
            continue
        if a["id"] == b["id"]:
            bad.append((n, "The two airports are the same."))
            continue
        za, zb = tzplace.zone_for(a["country"], a["lat"], a["lon"]), tzplace.zone_for(b["country"], b["lat"], b["lon"])
        times, ok = {}, True
        for s, _l, w in STAGES:
            for kind in ("sched", "act"):
                v = _local_time(row.get(f"{kind}_{s}"), za if w == "dep" else zb)
                if v is False:
                    bad.append((n, f"A time was not understood ({kind}_{s}); use YYYY-MM-DD HH:MM."))
                    ok = False
                    break
                times[f"{kind}_{s}"] = v
            if not ok:
                break
        if not ok:
            continue
        if not any(times[k] for k in ("sched_out", "act_out", "sched_off", "act_off")):
            day = (row.get("date") or "").strip()
            try:
                times["act_out"] = to_utc(day + "T12:00", za) if day else None
            except ValueError:
                times["act_out"] = None
            if times["act_out"] is None:
                bad.append((n, "A flight needs a date or a departure time."))
                continue
        bad_order = False
        for kind in ("sched", "act"):
            seq = [times[f"{kind}_{s}"] for s, _l, _w in STAGES if times[f"{kind}_{s}"]]
            bad_order = bad_order or any(seq[i] > seq[i + 1] for i in range(len(seq) - 1))
        allt = [v for v in times.values() if v]
        if bad_order or (allt and max(allt) - min(allt) > timedelta(hours=48)):
            bad.append((n, "The times are out of order or more than 48 hours apart."))
            continue
        no = re.sub(r"\s+", " ", row.get("flight", "").upper())[:8]
        if no and not FLIGHT_RE.match(no):
            no = ""
        mode = "simple" if row.get("mode") == "simple" or not any(times[k] for k in ("sched_off", "act_off", "sched_on", "act_on")) else "detailed"
        first = times["act_out"] or times["sched_out"] or times["act_off"] or times["sched_off"]
        if d.execute("SELECT 1 FROM my_flights WHERE user_id=%s AND from_ident=%s AND to_ident=%s AND COALESCE(act_out, sched_out, act_off, sched_off)=%s", (user_id, a["ident"], b["ident"], first)).fetchone():
            bad.append((n, "Already in the diary."))
            continue
        cols = {"mode": mode, "flight_no": no, "airline": " ".join(row.get("airline", "").split())[:60], "from_ident": a["ident"], "from_code": code_of(a), "from_name": a["name"], "from_city": a["municipality"] or "", "from_lat": a["lat"], "from_lon": a["lon"], "from_tz": za,
                "from_country": a["country"] or "", "to_country": b["country"] or "", "to_ident": b["ident"], "to_code": code_of(b), "to_name": b["name"], "to_city": b["municipality"] or "", "to_lat": b["lat"], "to_lon": b["lon"], "to_tz": zb,
                "aircraft_type": " ".join(row.get("aircraft", "").split())[:30], "registration": re.sub(r"[^A-Z0-9-]", "", row.get("registration", "").upper())[:12], "notes": vault.seal(row.get("notes", "")[:2000]),
                "distance_km": round(haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]), 1), **times}
        fid = d.execute("INSERT INTO my_flights (user_id, " + ", ".join(cols) + ") VALUES (%s, " + ", ".join(["%s"] * len(cols)) + ") RETURNING id", [user_id] + list(cols.values())).fetchone()["id"]
        for part in [p for p in row.get("people", "").split(";") if p.strip()][:cfg_int("myflights_max_passengers")]:
            f = [x.strip() for x in part.split("|")] + [""] * 5
            d.execute("INSERT INTO my_flight_pax (flight_id, name, klass, reason, seat, seat_kind) VALUES (%s,%s,%s,%s,%s,%s)",
                      (fid, vault.seal(f[0][:60]), f[1] if f[1] in CLASSES else "Economy", f[2] if f[2] in REASONS else "Leisure", re.sub(r"[^A-Za-z0-9 -]", "", f[3])[:6].upper(), f[4] if f[4] in SEATS else ""))
        added += 1
    d.commit()
    return added, bad


# ------------------------------------------------------------------ an AirTrail export

_STOP = {"airport", "international", "intl", "field", "regional", "national", "municipal", "memorial", "and", "the", "of", "airfield", "aerodrome"}
_TYPES = {"boeing 737-700": "B737", "boeing 737-800": "B738", "boeing 737-900": "B739", "boeing 737 max 8": "B38M", "boeing 737 max 7": "B37M", "boeing 737 max 9": "B39M", "boeing 737-300": "B733", "boeing 737-400": "B734", "boeing 737-500": "B735",
          "boeing 747-400": "B744", "boeing 757-200": "B752", "boeing 767-300": "B763", "boeing 777-200": "B772", "boeing 777-300": "B77W", "boeing 787-8": "B788", "boeing 787-9": "B789", "airbus a319": "A319", "airbus a320": "A320", "airbus a321": "A321",
          "airbus a330-300": "A333", "airbus a350-900": "A359", "airbus a220-300": "BCS3", "embraer 175": "E175", "embraer 170": "E170", "embraer 145": "E145", "canadair regional jet 700": "CRJ7", "canadair regional jet 900": "CRJ9", "bombardier crj-700": "CRJ7", "bombardier crj-900": "CRJ9"}


def _name_words(n):
    n = (n or "").lower().replace("&", " and ").replace("'", "")
    n = re.sub(r"[^a-z0-9]+", " ", n)
    return {w for w in n.split() if w not in _STOP}


def find_airport_by_name(name):
    """An airport by the name another program used for it ('Harry Reid Intl.', 'Dallas Love Field'): the same words apart from Airport, International, Field and the like. The biggest airport wins a tie."""
    want = _name_words(name)
    if not want:
        return None
    longest = max(want, key=len)
    best = None
    for r in get_db().execute("SELECT id, name, type FROM airports WHERE name ILIKE %s AND type IN ('large_airport','medium_airport','small_airport') LIMIT 80", ("%" + longest + "%",)).fetchall():
        have = _name_words(r["name"])
        if have == want or (want <= have and len(want) >= 2) or (have <= want and len(have) >= 2):
            score = (2 if have == want else 1, {"large_airport": 3, "medium_airport": 2}.get(r["type"], 1))
            if best is None or score > best[0]:
                best = (score, r["id"])
    return _airport(best[1]) if best else None


def is_airtrail(head):
    return {"flightnumber", "takeoffactual", "landingactual"} <= {h.lower() for h in head}


def _utc(text):
    t = (text or "").strip()
    if not t:
        return None
    try:
        d = datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def import_airtrail(text, user_id, who):
    """The flights of an AirTrail CSV export (airports by name, times in UTC with the offset, the seat, class and reason of the person who flew). Returns (added, [(line, why)])."""
    d = get_db()
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    room = cfg_int("myflights_max_per_member") - d.execute("SELECT COUNT(*) AS n FROM my_flights WHERE user_id=%s", (user_id,)).fetchone()["n"]
    added, bad = 0, []
    for n, row in enumerate(reader, start=2):
        if n > CSV_MAX_ROWS + 1:
            bad.append((n, f"Only the first {CSV_MAX_ROWS} lines are read."))
            break
        if added >= room:
            bad.append((n, "Your diary is full."))
            break
        a, b = find_airport_by_name(row.get("from")), find_airport_by_name(row.get("to"))
        if a is None or b is None:
            bad.append((n, f"Airport not found: {row.get('from') if a is None else row.get('to')}."))
            continue
        if a["id"] == b["id"]:
            bad.append((n, "The two airports are the same."))
            continue
        t = {"sched_out": _utc(row.get("departureScheduled")), "sched_off": _utc(row.get("takeoffScheduled")), "act_off": _utc(row.get("takeoffActual")) or _utc(row.get("departure")),
             "sched_on": _utc(row.get("landingScheduled")), "act_on": _utc(row.get("landingActual")), "sched_in": _utc(row.get("arrivalScheduled")), "act_out": None, "act_in": _utc(row.get("arrival"))}
        if any(v is False for v in t.values()):
            bad.append((n, "A time was not understood."))
            continue
        if not any(t[k] for k in ("sched_out", "act_out", "sched_off", "act_off")):
            day = (row.get("date") or "").strip()
            try:
                t["act_off"] = to_utc(day + "T12:00", tzplace.zone_for(a["country"], a["lat"], a["lon"])) if day else None
            except ValueError:
                t["act_off"] = None
            if t["act_off"] is None:
                bad.append((n, "A flight needs a date or a departure time."))
                continue
        for kind in ("sched", "act"):   # a time out of order is dropped rather than the flight
            seq = [(s, t[f"{kind}_{s}"]) for s, _l, _w in STAGES if t[f"{kind}_{s}"]]
            for i in range(len(seq) - 1):
                if seq[i][1] > seq[i + 1][1]:
                    t[f"{kind}_{seq[i + 1][0]}"] = None
        first = t["act_out"] or t["sched_out"] or t["act_off"] or t["sched_off"]
        if d.execute("SELECT 1 FROM my_flights WHERE user_id=%s AND from_ident=%s AND to_ident=%s AND COALESCE(act_out, sched_out, act_off, sched_off)=%s", (user_id, a["ident"], b["ident"], first)).fetchone():
            bad.append((n, "Already in the diary."))
            continue
        fno = re.sub(r"\s+", "", (row.get("flightNumber") or "").upper())
        m = re.match(r"^([A-Z0-9]{2,3}?)([0-9]{1,4}[A-Z]?)$", fno)
        fno = (m.group(1) + " " + m.group(2)) if m and FLIGHT_RE.match(m.group(1) + " " + m.group(2)) else ""
        typ = (row.get("aircraft") or "").strip()
        za, zb = tzplace.zone_for(a["country"], a["lat"], a["lon"]), tzplace.zone_for(b["country"], b["lat"], b["lon"])
        cols = {"mode": "detailed", "flight_no": fno, "airline": " ".join((row.get("airline") or "").split())[:60], "from_ident": a["ident"], "from_code": code_of(a), "from_name": a["name"], "from_city": a["municipality"] or "", "from_lat": a["lat"], "from_lon": a["lon"],
                "from_tz": za, "from_country": a["country"] or "", "to_ident": b["ident"], "to_code": code_of(b), "to_name": b["name"], "to_city": b["municipality"] or "", "to_lat": b["lat"], "to_lon": b["lon"], "to_tz": zb, "to_country": b["country"] or "",
                "aircraft_type": _TYPES.get(typ.lower(), typ)[:30], "registration": re.sub(r"[^A-Z0-9-]", "", (row.get("aircraftReg") or "").upper())[:12], "notes": vault.seal((row.get("note") or "").strip()[:2000]),
                "distance_km": round(haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]), 1), **t}
        fid = d.execute("INSERT INTO my_flights (user_id, " + ", ".join(cols) + ") VALUES (%s, " + ", ".join(["%s"] * len(cols)) + ") RETURNING id", [user_id] + list(cols.values())).fetchone()["id"]
        klass = {"economy": "Economy", "premium": "Premium Economy", "premium economy": "Premium Economy", "business": "Business", "first": "First", "private": "Private or other"}.get((row.get("seatClass") or "").strip().lower(), "Economy")
        reason = {"leisure": "Leisure", "business": "Business", "crew": "Crew"}.get((row.get("flightReason") or "").strip().lower(), "Leisure" if (row.get("flightReason") or "").strip() else "Other")
        if (row.get("seatNumber") or "").strip() or (row.get("seatClass") or "").strip() or (row.get("flightReason") or "").strip():
            kind = (row.get("seat") or "").strip().capitalize()
            d.execute("INSERT INTO my_flight_pax (flight_id, name, klass, reason, seat, seat_kind) VALUES (%s,%s,%s,%s,%s,%s)",
                      (fid, vault.seal(who[:60]), klass, reason, re.sub(r"[^A-Za-z0-9 -]", "", row.get("seatNumber") or "")[:6].upper(), kind if kind in SEATS else ""))
        added += 1
    d.commit()
    return added, bad


def parse_track(text):
    """The timed points of a GPX or KML file: [(UTC datetime, lat, lon)], oldest first. GPX track points with a time; KML gx:Track (a when for every coord). A file that declares entities or a doctype is refused."""
    import xml.etree.ElementTree as ET
    low = text[:4000].lower()
    if "<!doctype" in low or "<!entity" in low:
        raise ValueError("That file uses features this site does not read.")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise ValueError("That file is not a GPX or KML track.")
    def tag(e):
        return e.tag.rsplit("}", 1)[-1]
    def when(t):
        t = (t or "").strip().replace("Z", "+00:00")
        d = datetime.fromisoformat(t)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    pts = []
    try:
        for tp in [e for e in root.iter() if tag(e) == "trkpt"]:
            tm = next((c.text for c in tp if tag(c) == "time"), None)
            if tm and tp.get("lat") and tp.get("lon"):
                pts.append((when(tm), float(tp.get("lat")), float(tp.get("lon"))))
        if not pts:
            for tr in [e for e in root.iter() if tag(e) == "Track"]:
                whens = [when(c.text) for c in tr if tag(c) == "when"]
                coords = [c.text.split() for c in tr if tag(c) == "coord"]
                for w, c in zip(whens, coords):
                    if len(c) >= 2:
                        pts.append((w, float(c[1]), float(c[0])))
    except (ValueError, TypeError):
        raise ValueError("The times or positions in that file could not be read.")
    pts.sort(key=lambda p: p[0])
    return pts


def import_track(text, user_id):
    """One flight from a GPX or KML track with times (the first and last points give takeoff and landing, and the airports near them). Returns (added, [(1, why)])."""
    try:
        pts = parse_track(text)
    except ValueError as e:
        return 0, [(1, str(e))]
    if len(pts) < 2:
        return 0, [(1, "The track has no points with times. A GPX track with times, or a KML gx:Track, is needed.")]
    (t0, la0, lo0), (t1, la1, lo1) = pts[0], pts[-1]
    if t1 - t0 > timedelta(hours=48) or t1 <= t0:
        return 0, [(1, "The track's times are out of order or more than 48 hours apart.")]
    a, b = _near(la0, lo0), _near(la1, lo1)
    if a is None or b is None:
        return 0, [(1, "No airport was found near the " + ("start" if a is None else "end") + " of the track (within 40 km). Add the flight by hand instead.")]
    if a["id"] == b["id"]:
        return 0, [(1, "The track starts and ends at the same airport.")]
    d = get_db()
    if d.execute("SELECT COUNT(*) AS n FROM my_flights WHERE user_id=%s", (user_id,)).fetchone()["n"] >= cfg_int("myflights_max_per_member"):
        return 0, [(1, "Your diary is full.")]
    if d.execute("SELECT 1 FROM my_flights WHERE user_id=%s AND from_ident=%s AND to_ident=%s AND act_off=%s", (user_id, a["ident"], b["ident"], t0)).fetchone():
        return 0, [(1, "Already in the diary.")]
    za, zb = tzplace.zone_for(a["country"], a["lat"], a["lon"]), tzplace.zone_for(b["country"], b["lat"], b["lon"])
    cols = {"mode": "detailed", "from_ident": a["ident"], "from_code": code_of(a), "from_name": a["name"], "from_city": a["municipality"] or "", "from_lat": a["lat"], "from_lon": a["lon"], "from_tz": za, "from_country": a["country"] or "",
            "to_ident": b["ident"], "to_code": code_of(b), "to_name": b["name"], "to_city": b["municipality"] or "", "to_lat": b["lat"], "to_lon": b["lon"], "to_tz": zb, "to_country": b["country"] or "",
            "distance_km": round(haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]), 1), "act_off": t0, "act_on": t1, "notes": "From a track file: takeoff and landing are the first and last points."}
    d.execute("INSERT INTO my_flights (user_id, " + ", ".join(cols) + ") VALUES (%s, " + ", ".join(["%s"] * len(cols)) + ")", [user_id] + list(cols.values()))
    d.commit()
    return 1, []


@bp.route("/my/flights/import", methods=["GET", "POST"])
@login_required
def import_page():
    _on()
    result = None
    if request.method == "POST":
        from . import myflightstracks as mt
        files = [f for f in request.files.getlist("file") if f and f.filename]
        if not files:
            return action_response(False, "Choose a file first.", redirect_endpoint="myflightsio.import_page")
        who = current_user.display_name or current_user.username
        added, bad, notes = 0, [], []
        d = get_db()
        for up in files[:30]:
            raw = up.read(mt.JSON_MAX + 1)
            name = up.filename or "file"
            if len(raw) > mt.JSON_MAX or (len(raw) > CSV_MAX_BYTES and not (raw.lstrip()[:1] in (b"{", b"[") or raw.lstrip()[:5] in (b"<?xml", b"<kml ", b"<gpx "))):
                bad.append((0, f"{name}: the file is too large."))
                continue
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("latin-1")
            low = name.lower()
            try:
                if text.lstrip()[:1] in ("{", "["):   # an AirTrail JSON export: every field, all the people and the tracks
                    import json as _json
                    n_, notes_, bad_ = mt.import_airtrail_flights(d, current_user.id, mt.flights_of_export(_json.loads(text)), who)
                    added += n_
                    notes += [f"{name}: {x}" for x in notes_]
                    bad += [(a, f"{name}: {b}") for a, b in bad_]
                elif low.endswith((".gpx", ".kml")) or text.lstrip()[:5].lower() in ("<?xml", "<gpx ", "<kml "):
                    ok, msg = mt.import_track_file(d, current_user.id, text, name)
                    if ok and "added as a new flight" in msg:
                        added += 1
                    (notes if ok else bad).append(msg if ok else (0, msg))
                else:
                    head = next(csv.reader(io.StringIO(text.lstrip("\ufeff").split("\n", 1)[0])), [])
                    n_, bad_ = import_airtrail(text, current_user.id, who) if is_airtrail(head) else import_csv(text, current_user.id)
                    added += n_
                    bad += [(a, f"{name}: {b}" if len(files) > 1 else b) for a, b in bad_]
            except ValueError as e:
                bad.append((0, f"{name}: {e}"))
        audit("myflights.import", target=current_user.username, detail=f"{len(files)} file{'s' if len(files) != 1 else ''}: {added} added, {len(bad)} skipped")
        result = {"added": added, "bad": bad[:50], "more": max(0, len(bad) - 50), "notes": notes[:50]}
    return render_template("my_flights_import.html", result=result, cols=CSV_COLS, active="flightimport", max_rows=CSV_MAX_ROWS, private_ok=cfg("myflights_airtrail_private") == "1")


@bp.route("/my/flights/import/airtrail", methods=["POST"])
@login_required
def import_airtrail_instance():
    """Flights straight from an AirTrail instance with an API key (flights read scope, and tracks read for the tracks): used for this one import and never kept."""
    _on()
    from . import myflightstracks as mt
    url, key = (request.form.get("url") or "").strip(), (request.form.get("key") or "").strip()
    if not key or len(key) > 300:
        return action_response(False, "Paste the API key from AirTrail (Settings, Security, API Keys).", redirect_endpoint="myflightsio.import_page")
    try:
        base = mt._safe_base(url)
        flights, ids = mt.pull_airtrail(base, key)
    except ValueError as e:
        return action_response(False, str(e), redirect_endpoint="myflightsio.import_page")
    who = current_user.display_name or current_user.username
    added, notes, bad = mt.import_airtrail_flights(get_db(), current_user.id, flights, who, ids)
    audit("myflights.import", target=current_user.username, detail=f"AirTrail instance {base.split('//', 1)[-1][:60]}: {len(flights)} read, {added} added, {len(bad)} skipped")
    return render_template("my_flights_import.html", result={"added": added, "bad": bad[:50], "more": max(0, len(bad) - 50), "notes": notes or [f"{len(flights)} flights were read from AirTrail."]}, cols=CSV_COLS, active="flightimport", max_rows=CSV_MAX_ROWS, private_ok=cfg("myflights_airtrail_private") == "1")


# ------------------------------------------------------------------ the look-up from what the site's receivers heard

def _near(lat, lon, km=40):
    """The nearest real airport (a large, medium or small one with a code people use) to a point, within km, or None."""
    if lat is None or lon is None:
        return None
    dl = km / 111.0
    best = None
    for r in get_db().execute("SELECT id FROM airports WHERE lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s AND type IN ('large_airport','medium_airport','small_airport') ORDER BY (type='large_airport') DESC, (type='medium_airport') DESC",
                              (lat - dl, lat + dl, lon - dl * 1.4, lon + dl * 1.4)).fetchall():
        a = _airport(r["id"])
        dist = haversine_km(lat, lon, a["lat"], a["lon"])
        if dist <= km and (best is None or dist < best[0] - 8):   # a bigger airport within a few kilometers of a smaller one wins
            best = (dist, a)
    return best[1] if best else None


def _airport_of(a):
    """One of our airports from a lookup's airport ({icao, iata, name}), as the form wants it, or None."""
    if not a:
        return None
    code_i, code_a = (a.get("icao") or "").upper(), (a.get("iata") or "").upper()
    r = get_db().execute("SELECT id FROM airports WHERE (%s <> '' AND (icao_code=%s OR ident=%s OR gps_code=%s)) OR (%s <> '' AND iata_code=%s) ORDER BY (icao_code=%s) DESC, id LIMIT 1", (code_i, code_i, code_i, code_i, code_a, code_a, code_i)).fetchone()
    ap = _airport(r["id"]) if r else None
    return {"id": ap["id"], "code": code_of(ap), "label": f"{ap['name']} ({code_of(ap)})"} if ap else None


def split_flight_number(q):
    """'WN1672' or 'SWA 1672' -> (airline code, number), or None for anything else (a registration, say)."""
    q = re.sub(r"[^A-Z0-9]", "", (q or "").upper())
    m = re.match(r"^([A-Z]{3})([0-9]{1,4}[A-Z]?)$", q) or re.match(r"^([A-Z][A-Z0-9]|[0-9][A-Z])([0-9]{1,4}[A-Z]?)$", q)
    if not m or (q[0] == "N" and q[1].isdigit()):
        return None
    return m.group(1), m.group(2)


def suggest(q):
    """What the outside lookups know of a flight number or a registration: {airline, aircraft_type, registration, flight_no, from, to, notes[]} (every part optional). A flight number gives the airline and the route that number
    usually flies; a registration gives the aircraft's type and owner."""
    out, notes = {}, []
    fn = split_flight_number(q)
    if fn:
        al = lookups.airline_info(fn[0])
        if al and al.get("name"):
            out["airline"] = al["name"]
            if al.get("iata"):
                out["flight_no"] = al["iata"] + " " + fn[1]
            r = lookups.route_for((al.get("icao") or "") + fn[1])
            if r and len(r.get("airports") or []) >= 2:
                a, b = _airport_of(r["airports"][0]), _airport_of(r["airports"][-1])
                if a and b:
                    out["from"], out["to"] = a, b
                    notes.append("The airports are the route this flight number usually flies; check them against your ticket.")
        elif not al:
            notes.append("The airline code was not recognized.")
    else:
        reg = re.sub(r"[^A-Z0-9-]", "", (q or "").upper())
        ac = lookups.aircraft_by_reg(reg) if len(reg) >= 3 else None
        if ac:
            out["registration"] = ac.get("registration") or reg
            if ac.get("icao_type"):
                out["aircraft_type"] = ac["icao_type"]
            out["aircraft_name"] = " ".join(x for x in (ac.get("manufacturer"), ac.get("type")) if x)
            if ac.get("registered_owner"):
                out["airline"] = ac["registered_owner"]
    out["notes"] = notes
    return out


@bp.route("/my/flights/lookup.json")
@login_required
def lookup_json():
    """Flights the site's receivers heard that match a flight number or registration, around a date (the local date of the flight; empty = lately): {flights: [...]}. Only what the receivers heard can be found."""
    _on()
    q = re.sub(r"[^A-Z0-9-]", "", (request.args.get("q") or "").upper())[:12]
    if len(q) < 3:
        return jsonify({"flights": [], "note": "Type a flight number or a registration of at least three characters."})
    sug = suggest(q)
    day = (request.args.get("date") or "").strip()
    args, where = [], []
    digits = re.sub(r"[^0-9]", "", q)
    letters = re.sub(r"[^A-Z]", "", q)
    if FLIGHT_RE.match(q) and digits and len(letters) >= 2 and len(digits) >= 2 and "-" not in q and not (q[0] == "N" and q[1].isdigit()):   # a flight number: WN1672 matches the callsign SWA1672 by its number
        where.append("(upper(callsign) = %s OR upper(callsign) LIKE %s)")
        args += [q, "%" + digits]
    else:
        where.append("(upper(replace(reg,'-','')) = %s OR upper(callsign) = %s)")
        args += [q.replace("-", ""), q]
    if day:
        try:
            d0 = datetime.fromisoformat(day[:10]).replace(tzinfo=timezone.utc)
        except ValueError:
            return jsonify({"flights": [], "suggest": sug, "note": "The date was not understood."})
        where.append("last_seen >= %s AND first_seen < %s")
        args += [d0 - timedelta(hours=14), d0 + timedelta(hours=40)]
    rows = get_db().execute("SELECT hex, callsign, reg, type, first_seen, last_seen, start_lat, start_lon, end_lat, end_lon, positions FROM flight_log WHERE " + " AND ".join(where) + " ORDER BY first_seen DESC LIMIT 12", args).fetchall()
    out = []
    for r in rows:
        a, b = _near(r["start_lat"], r["start_lon"]), _near(r["end_lat"], r["end_lon"])
        item = {"hex": r["hex"], "callsign": r["callsign"] or "", "reg": r["reg"] or "", "type": r["type"] or "", "first": r["first_seen"].isoformat(), "last": r["last_seen"].isoformat(), "positions": r["positions"],
                "from": {"id": a["id"], "code": code_of(a), "label": f"{a['name']} ({code_of(a)})"} if a else None, "to": {"id": b["id"], "code": code_of(b), "label": f"{b['name']} ({code_of(b)})"} if b else None}
        if a and b and a["id"] != b["id"]:   # the times the receivers first and last heard it, written in each airport's own time (the first and last positions are close to takeoff and landing)
            item["act_off"] = local_text(r["first_seen"], tzplace.zone_for(a["country"], a["lat"], a["lon"]))
            item["act_on"] = local_text(r["last_seen"], tzplace.zone_for(b["country"], b["lat"], b["lon"]))
        out.append(item)
    return jsonify({"flights": out, "suggest": sug, "note": "" if out else "The site's receivers did not hear a flight like that around then."})
