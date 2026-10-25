"""My Flights, a flight's own page and its track: the details of one flight, a track attached to it (a Flightradar24 or other KML, a GPX file, or the track from an AirTrail export or instance), and the ways of bringing flights in
with their people and tracks: track files (attached to the flight they match, or added as a new one), an AirTrail JSON export, and a pull straight from an AirTrail instance with an API key (used once, never kept)."""
import ipaddress
import json
import math
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from . import tzplace
from .action_response import action_response
from . import vault
from .audit import audit
from .db import get_db
from .myflights import FLIGHT_RE, REASONS, SEATS, STAGES, CLASSES, _airport, _load, _on, code_of, derive, dist_txt, haversine_km, hm
from .myflightsio import _TYPES, _find_airport, _near, find_airport_by_name
from .settings import cfg, cfg_int

bp = Blueprint("myflightstracks", __name__)
MAX_POINTS = 1500
JSON_MAX = 30_000_000


# ------------------------------------------------------------------ reading a track

def _tag(e):
    return e.tag.rsplit("}", 1)[-1]


def _when(t):
    if isinstance(t, (int, float)) and not isinstance(t, bool):   # epoch seconds, or milliseconds
        return datetime.fromtimestamp(t / 1000 if t > 1e11 else t, tz=timezone.utc)
    t = (t or "").strip().replace("Z", "+00:00").replace(" UTC", "+00:00")
    d = datetime.fromisoformat(t)
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _meta_from_description(html):
    """What a Flightradar24 KML says about the flight in its first description: flight number, call sign, airline, the airports' codes, aircraft type and registration."""
    meta = {}
    txt = re.sub(r"( ?\| ?)+", "|", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "|", re.sub(r"<style.*?</style>", "", html or "", flags=re.S))))
    m = re.search(r"\|([A-Z0-9]{3,8})\|/ ?([A-Z0-9]{3,8})\|([^|]+)\|", txt)
    if m:
        meta["flight_no"], meta["callsign"], meta["airline"] = m.group(1), m.group(2), m.group(3).replace("&amp;", "&")
    d, a = re.search(r"airport/([a-z0-9]{3,4})/departures", html or "", re.I), re.search(r"airport/([a-z0-9]{3,4})/arrivals", html or "", re.I)
    if d:
        meta["from"] = d.group(1).upper()
    if a:
        meta["to"] = a.group(1).upper()
    m = re.search(r"Aircraft\|\(([A-Z0-9]{2,4})\)\|([^|]+)", txt)
    if m:
        meta["type"], meta["model"] = m.group(1), m.group(2)
    m = re.search(r"Registration\|([A-Z0-9-]{3,8})", txt)
    if m:
        meta["reg"] = m.group(1)
    return meta


def read_track(text):
    """(points, meta) from a track file: [(UTC datetime, lat, lon, altitude in feet or None, speed in knots or None)] oldest first. A KML with a timed point for each placemark (Flightradar24), a KML gx:Track or a GPX track.
    A file that declares entities or a doctype is refused (ValueError)."""
    import xml.etree.ElementTree as ET
    low = text[:4000].lower()
    if "<!doctype" in low or "<!entity" in low:
        raise ValueError("That file uses features this site does not read.")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise ValueError("That file is not a GPX or KML track.")
    pts, meta = [], {}
    try:
        doc = next((e for e in root.iter() if _tag(e) == "Document"), None)
        if doc is not None:
            nm = next((c.text for c in doc if _tag(c) == "name"), "") or ""
            ds = next((c.text for c in doc if _tag(c) == "description"), "") or ""
            meta = _meta_from_description(ds)
            if "flight_no" not in meta and re.match(r"^[A-Z0-9]{2,3}[0-9]{1,4}[A-Z]?(/|$)", nm.strip()):
                meta["flight_no"] = nm.split("/")[0].strip()
        for pm in [e for e in root.iter() if _tag(e) == "Placemark"]:
            tm = next((w.text for w in pm.iter() if _tag(w) == "when"), None)
            pt = next((c for c in pm.iter() if _tag(c) == "Point"), None)
            if tm and pt is not None:
                co = next((c.text for c in pt.iter() if _tag(c) == "coordinates"), "") or ""
                p = co.strip().split(",")
                desc = next((c.text for c in pm if _tag(c) == "description"), "") or ""
                alt = re.search(r"Altitude:</b></span>\s*<span>([\d,]+)\s*ft", desc)
                spd = re.search(r"Speed:</b></span>\s*<span>(\d+)\s*kt", desc)
                if len(p) >= 2:
                    altf = int(alt.group(1).replace(",", "")) if alt else (float(p[2]) * 3.28084 if len(p) > 2 and p[2] else None)
                    pts.append((_when(tm), float(p[1]), float(p[0]), altf, int(spd.group(1)) if spd else None))
        if not pts:
            for tr in [e for e in root.iter() if _tag(e) == "Track"]:
                whens = [_when(c.text) for c in tr if _tag(c) == "when"]
                coords = [c.text.split() for c in tr if _tag(c) == "coord"]
                for w, c in zip(whens, coords):
                    if len(c) >= 2:
                        pts.append((w, float(c[1]), float(c[0]), float(c[2]) * 3.28084 if len(c) > 2 else None, None))
        if not pts:
            for tp in [e for e in root.iter() if _tag(e) == "trkpt"]:
                tm = next((c.text for c in tp if _tag(c) == "time"), None)
                ele = next((c.text for c in tp if _tag(c) == "ele"), None)
                if tm and tp.get("lat") and tp.get("lon"):
                    pts.append((_when(tm), float(tp.get("lat")), float(tp.get("lon")), float(ele) * 3.28084 if ele else None, None))
    except (ValueError, TypeError):
        raise ValueError("The times or positions in that file could not be read.")
    pts.sort(key=lambda p: p[0])
    return pts, meta


def takeoff_landing(pts):
    """The times a track says the aircraft left and met the ground: the last ground-level point before it climbed (or the first point), and the start of the ground run at the end (or the last point)."""
    n = len(pts)
    alts = [p[3] for p in pts]
    t_off = pts[0][0]
    for i in range(n):
        if alts[i] is not None and alts[i] > 50:
            t_off = pts[max(0, i - 1)][0]
            break
    t_on = pts[-1][0]
    i = n - 1
    while i > 0 and alts[i] is not None and alts[i] <= 50:
        i -= 1
    if i < n - 1:
        t_on = pts[i + 1][0]
    return t_off, t_on


def _store_points(pts):
    step = max(1, math.ceil(len(pts) / MAX_POINTS))
    keep = pts[::step]
    if keep[-1] is not pts[-1]:
        keep = keep + [pts[-1]]
    return [[int(p[0].timestamp()), round(p[1], 5), round(p[2], 5), None if p[3] is None else round(p[3]), p[4]] for p in keep]


def save_track(d, fid, pts, source):
    d.execute("INSERT INTO my_flight_tracks (flight_id, source, points, n) VALUES (%s,%s,%s,%s) ON CONFLICT (flight_id) DO UPDATE SET source=EXCLUDED.source, points=EXCLUDED.points, n=EXCLUDED.n, created_at=now()",
              (fid, source[:60], json.dumps(_store_points(pts), separators=(",", ":")), len(pts)))


def track_of(fid):
    r = get_db().execute("SELECT source, points, n, created_at FROM my_flight_tracks WHERE flight_id=%s", (fid,)).fetchone()
    return (dict(r, points=json.loads(r["points"]))) if r else None


def track_stats(points):
    if len(points) < 2:
        return {}
    km = sum(haversine_km(points[i - 1][1], points[i - 1][2], points[i][1], points[i][2]) for i in range(1, len(points)))
    alts = [p[3] for p in points if p[3] is not None]
    spd = [p[4] for p in points if p[4] is not None]
    return {"km": round(km, 1), "minutes": round((points[-1][0] - points[0][0]) / 60), "max_alt": max(alts) if alts else None, "max_kt": max(spd) if spd else None, "n": len(points)}


# ------------------------------------------------------------------ putting a track or an export into the diary

def _flight_cols(a, b, extra):
    za, zb = tzplace.zone_for(a["country"], a["lat"], a["lon"]), tzplace.zone_for(b["country"], b["lat"], b["lon"])
    return {"from_ident": a["ident"], "from_code": code_of(a), "from_name": a["name"], "from_city": a["municipality"] or "", "from_lat": a["lat"], "from_lon": a["lon"], "from_tz": za, "from_country": a["country"] or "",
            "to_ident": b["ident"], "to_code": code_of(b), "to_name": b["name"], "to_city": b["municipality"] or "", "to_lat": b["lat"], "to_lon": b["lon"], "to_tz": zb, "to_country": b["country"] or "",
            "distance_km": round(haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]), 1), **extra}


def _fmt_flight_no(v):
    v = re.sub(r"\s+", "", (v or "").upper())
    m = re.match(r"^([A-Z0-9]{2,3}?)([0-9]{1,4}[A-Z]?)$", v)
    return (m.group(1) + " " + m.group(2)) if m and FLIGHT_RE.match(m.group(1) + " " + m.group(2)) else ""


def find_matching_flight(d, user_id, meta, t_off, a_id=None, b_id=None):
    """The diary flight a track belongs to: the same flight number or registration within a day of it, or the same two airports within eight hours."""
    rows = d.execute("SELECT id, flight_no, registration, from_ident, to_ident, COALESCE(act_off, sched_off, act_out, sched_out) AS t FROM my_flights WHERE user_id=%s AND COALESCE(act_off, sched_off, act_out, sched_out) BETWEEN %s AND %s",
                     (user_id, t_off - timedelta(hours=26), t_off + timedelta(hours=26))).fetchall()
    fno, reg = _fmt_flight_no(meta.get("flight_no")), (meta.get("reg") or "").replace("-", "").upper()
    best = None
    for r in rows:
        gap = abs((r["t"] - t_off).total_seconds()) / 3600
        score = 0
        if fno and r["flight_no"] == fno and gap <= 26:
            score = 3
        elif reg and (r["registration"] or "").replace("-", "").upper() == reg and gap <= 12:
            score = 2
        elif a_id and b_id and r["from_ident"] == a_id and r["to_ident"] == b_id and gap <= 8:
            score = 1
        if score and (best is None or (score, -gap) > best[0]):
            best = ((score, -gap), r["id"])
    return best[1] if best else None


def import_track_file(d, user_id, text, name=""):
    """One track file: attached to the diary flight it matches, or added as a new flight. Returns (ok, message)."""
    try:
        pts, meta = read_track(text)
    except ValueError as e:
        return False, str(e)
    if len(pts) < 2:
        return False, "The track has no points with times. A KML with timed points, a KML gx:Track or a GPX track with times is needed."
    t_off, t_on = takeoff_landing(pts)
    if pts[-1][0] - pts[0][0] > timedelta(hours=48):
        return False, "The track's times are more than 48 hours apart."
    a = _find_airport(meta.get("from")) if meta.get("from") else None
    b = _find_airport(meta.get("to")) if meta.get("to") else None
    a = a or _near(pts[0][1], pts[0][2])
    b = b or _near(pts[-1][1], pts[-1][2])
    fid = find_matching_flight(d, user_id, meta, t_off, a["ident"] if a else None, b["ident"] if b else None)
    label = (meta.get("flight_no") or name or "track")
    if fid:
        row = d.execute("SELECT * FROM my_flights WHERE id=%s", (fid,)).fetchone()
        sets, vals = {}, []
        if row["act_off"] is None:
            sets["act_off"] = t_off
        if row["act_on"] is None:
            sets["act_on"] = t_on
        for col, v in (("registration", re.sub(r"[^A-Z0-9-]", "", (meta.get("reg") or "").upper())[:12]), ("aircraft_type", (meta.get("type") or "")[:30]), ("airline", (meta.get("airline") or "")[:60]), ("flight_no", _fmt_flight_no(meta.get("flight_no")))):
            if v and not (row[col] or "").strip():
                sets[col] = v
        if sets:
            d.execute("UPDATE my_flights SET " + ", ".join(f"{k}=%s" for k in sets) + ", updated_at=now() WHERE id=%s", list(sets.values()) + [fid])
        save_track(d, fid, pts, "KML" if "kml" in text[:300].lower() else "GPX")
        d.commit()
        return True, f"{label}: the track is attached to your flight {row['from_code']} to {row['to_code']}."
    if a is None or b is None:
        return False, f"{label}: no airport was found for the " + ("start" if a is None else "end") + " of the track (within 40 km), and no flight in your diary matches it. Add the flight by hand, then attach the track on its page."
    if a["id"] == b["id"]:
        return False, f"{label}: the track starts and ends at the same airport."
    if d.execute("SELECT COUNT(*) AS n FROM my_flights WHERE user_id=%s", (user_id,)).fetchone()["n"] >= cfg_int("myflights_max_per_member"):
        return False, "Your diary is full."
    cols = _flight_cols(a, b, {"mode": "detailed", "flight_no": _fmt_flight_no(meta.get("flight_no")), "airline": (meta.get("airline") or "")[:60], "aircraft_type": (meta.get("type") or "")[:30],
                               "registration": re.sub(r"[^A-Z0-9-]", "", (meta.get("reg") or "").upper())[:12], "act_off": t_off, "act_on": t_on, "notes": "From a track file."})
    fid = d.execute("INSERT INTO my_flights (user_id, " + ", ".join(cols) + ") VALUES (%s, " + ", ".join(["%s"] * len(cols)) + ") RETURNING id", [user_id] + list(cols.values())).fetchone()["id"]
    save_track(d, fid, pts, "KML" if "kml" in text[:300].lower() else "GPX")
    d.commit()
    return True, f"{label}: added as a new flight, {code_of(a)} to {code_of(b)}, with its track."


# ------------------------------------------------------------------ AirTrail: an export file, or an instance

def _airport_of(ap):
    """An AirTrail airport ({icao, iata, name, lat, lon, tz, country, municipality}) as one of ours, or a stand-in built from what it says."""
    if not isinstance(ap, dict):
        return None
    for code in (ap.get("icao"), ap.get("iata")):
        if code:
            r = _find_airport(str(code))
            if r:
                return r
    if ap.get("name"):
        r = find_airport_by_name(ap["name"])
        if r:
            return r
    if ap.get("lat") is not None and ap.get("lon") is not None:
        return {"id": 0, "ident": str(ap.get("icao") or ap.get("iata") or ap.get("name"))[:20], "iata_code": ap.get("iata") or "", "icao_code": ap.get("icao") or "", "gps_code": "", "name": str(ap.get("name") or "")[:120],
                "municipality": ap.get("municipality") or "", "country": ap.get("country") or "", "lat": float(ap["lat"]), "lon": float(ap["lon"]), "tz": ap.get("tz") or ""}
    return None


def _utc(v):
    try:
        return _when(v) if v else None
    except (ValueError, TypeError, AttributeError, OverflowError, OSError):
        return False


def _track_from_json(tr):
    """AirTrail's track object ({coordinates: [[lon, lat, alt?]], times: [iso], groundSpeedKt?}) as points."""
    if not isinstance(tr, dict) or not tr.get("coordinates"):
        return []
    co, tm, sp = tr["coordinates"], tr.get("times") or [], tr.get("groundSpeedKt") or []
    out = []
    for i, c in enumerate(co):
        try:
            t = _when(tm[i]) if i < len(tm) and tm[i] else None
            if t is None or len(c) < 2:
                continue
            out.append((t, float(c[1]), float(c[0]), float(c[2]) * 3.28084 if len(c) > 2 and c[2] is not None else None, int(sp[i]) if i < len(sp) and sp[i] is not None else None))
        except (ValueError, TypeError, AttributeError, OverflowError, OSError):
            continue
    return out


def import_airtrail_flights(d, user_id, flights, who, me_ids=()):
    """Flights in AirTrail's own shape (the JSON export, or what its API returns): airports from their codes, every time field, aircraft and airline, ALL the passengers with their seats, classes and reasons, and the flight's track.
    Returns (added, notes, skipped lines)."""
    room = cfg_int("myflights_max_per_member") - d.execute("SELECT COUNT(*) AS n FROM my_flights WHERE user_id=%s", (user_id,)).fetchone()["n"]
    added, bad, tracks, people = 0, [], 0, 0
    for n, fl in enumerate(flights, start=1):
        label = f"Flight {n}" + (f" ({fl.get('flightNumber')})" if isinstance(fl, dict) and fl.get("flightNumber") else "")
        if not isinstance(fl, dict):
            bad.append((n, "Not a flight."))
            continue
        if added >= room:
            bad.append((n, "Your diary is full."))
            break
        a, b = _airport_of(fl.get("from")), _airport_of(fl.get("to"))
        if a is None or b is None:
            bad.append((n, f"{label}: an airport was not found."))
            continue
        if a["ident"] == b["ident"]:
            bad.append((n, f"{label}: the two airports are the same."))
            continue
        t = {"sched_out": _utc(fl.get("departureScheduled")), "sched_off": _utc(fl.get("takeoffScheduled")), "act_off": _utc(fl.get("takeoffActual")) or _utc(fl.get("departure")), "sched_on": _utc(fl.get("landingScheduled")),
             "act_on": _utc(fl.get("landingActual")), "sched_in": _utc(fl.get("arrivalScheduled")), "act_out": None, "act_in": _utc(fl.get("arrival"))}
        if any(v is False for v in t.values()):
            bad.append((n, f"{label}: a time was not understood."))
            continue
        track = _track_from_json(fl.get("track"))
        if not any(t[k] for k in ("sched_out", "act_out", "sched_off", "act_off")):
            if track:
                t["act_off"], t["act_on"] = takeoff_landing(track)
            else:
                try:
                    t["act_off"] = tzplace_local(fl.get("date"), a)
                except ValueError:
                    t["act_off"] = None
            if t["act_off"] is None:
                bad.append((n, f"{label}: no date or time."))
                continue
        for kind in ("sched", "act"):   # a time out of order is dropped, not the flight
            seq = [(s, t[f"{kind}_{s}"]) for s, _l, _w in STAGES if t[f"{kind}_{s}"]]
            for i in range(len(seq) - 1):
                if seq[i][1] > seq[i + 1][1]:
                    t[f"{kind}_{seq[i + 1][0]}"] = None
        first = t["act_out"] or t["sched_out"] or t["act_off"] or t["sched_off"]
        if d.execute("SELECT 1 FROM my_flights WHERE user_id=%s AND from_ident=%s AND to_ident=%s AND COALESCE(act_out, sched_out, act_off, sched_off)=%s", (user_id, a["ident"], b["ident"], first)).fetchone():
            bad.append((n, f"{label}: already in the diary."))
            continue
        ac = fl.get("aircraft") if isinstance(fl.get("aircraft"), dict) else {}
        al = fl.get("airline") if isinstance(fl.get("airline"), dict) else {}
        cols = _flight_cols(a, b, {"mode": "detailed", "flight_no": _fmt_flight_no(fl.get("flightNumber")), "airline": " ".join(str(al.get("name") or "").split())[:60],
                                   "aircraft_type": str(ac.get("icao") or _TYPES.get(str(ac.get("name") or "").lower(), ac.get("name") or ""))[:30],
                                   "registration": re.sub(r"[^A-Z0-9-]", "", str(fl.get("aircraftReg") or "").upper())[:12], "notes": vault.seal(str(fl.get("note") or "").strip()[:2000]), **t})
        if a.get("tz"):
            cols["from_tz"] = a["tz"]
        if b.get("tz"):
            cols["to_tz"] = b["tz"]
        fid = d.execute("INSERT INTO my_flights (user_id, " + ", ".join(cols) + ") VALUES (%s, " + ", ".join(["%s"] * len(cols)) + ") RETURNING id", [user_id] + list(cols.values())).fetchone()["id"]
        pax = fl.get("passengers")
        if not isinstance(pax, list) or not pax:   # an older export keeps the one person's seat on the flight itself
            pax = [{"seat": s.get("seat"), "seatNumber": s.get("seatNumber"), "seatClass": s.get("seatClass"), "flightReason": fl.get("flightReason") or s.get("flightReason"), "isMe": True} for s in (fl.get("seats") or [])] or \
                  ([{"seat": fl.get("seat"), "seatNumber": fl.get("seatNumber"), "seatClass": fl.get("seatClass"), "flightReason": fl.get("flightReason"), "isMe": True}] if any(fl.get(k) for k in ("seatNumber", "seatClass", "flightReason")) else [])
        for p in pax[:cfg_int("myflights_max_passengers")]:
            if not isinstance(p, dict):
                continue
            u = p.get("user") if isinstance(p.get("user"), dict) else None
            mine = p.get("isMe") or (p.get("userId") in me_ids if p.get("userId") is not None and me_ids else False)
            name = who if mine else (p.get("guestName") or (u and (u.get("displayName") or u.get("name") or u.get("username"))) or "Passenger")
            klass = {"economy": "Economy", "premium": "Premium Economy", "premium economy": "Premium Economy", "business": "Business", "first": "First", "private": "Private or other"}.get(str(p.get("seatClass") or "").strip().lower(), "Economy")
            rs = str(p.get("flightReason") or "").strip().lower()
            reason = {"leisure": "Leisure", "business": "Business", "crew": "Crew"}.get(rs, "Other" if rs else "Leisure")
            kind = str(p.get("seat") or "").strip().capitalize()
            d.execute("INSERT INTO my_flight_pax (flight_id, name, klass, reason, seat, seat_kind) VALUES (%s,%s,%s,%s,%s,%s)",
                      (fid, vault.seal(str(name)[:60]), klass, reason, re.sub(r"[^A-Za-z0-9 -]", "", str(p.get("seatNumber") or ""))[:6].upper(), kind if kind in SEATS else ""))
            people += 1
        if len(track) >= 2:
            save_track(d, fid, track, "AirTrail")
            tracks += 1
        added += 1
    d.commit()
    notes = [f"{added} flight{'' if added == 1 else 's'} added with {people} {'person' if people == 1 else 'people'} and {tracks} track{'' if tracks == 1 else 's'}."] if added else []
    return added, notes, bad


def tzplace_local(day, a):
    from .myflights import to_utc
    day = (str(day or "")).strip()[:10]
    return to_utc(day + "T12:00", a.get("tz") or tzplace.zone_for(a["country"], a["lat"], a["lon"])) if day else None


def flights_of_export(data):
    """The flights in an AirTrail JSON export ({users, flights}), or a bare list."""
    if isinstance(data, dict):
        data = data.get("flights") or data.get("data") or []
    return [f for f in data if isinstance(f, dict)] if isinstance(data, list) else []


def _private_ok():
    return cfg("myflights_airtrail_private") == "1"


def _safe_base(url):
    """The address of an AirTrail instance, checked: http or https, a host name, and (unless an administrator allowed the site's own network) only public addresses. Returns the base address or raises ValueError."""
    u = urllib.parse.urlparse((url or "").strip())
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
        raise ValueError("Give the address of your AirTrail, like https://airtrail.example.com")
    from . import safenet
    try:
        safenet.resolve(u.hostname, u.port or (443 if u.scheme == "https" else 80), _private_ok())
    except safenet.Blocked as e:
        raise ValueError("That address could not be found." if "found" in str(e) else "That address is on a private network. An administrator can allow those in Settings > My Flights.")
    if u.scheme == "http" and not _private_ok():
        raise ValueError("Use the https address of your AirTrail.")
    return f"{u.scheme}://{u.netloc}{u.path.rstrip('/')}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _get(base, path, key):
    req = urllib.request.Request(base + path, headers={"Authorization": "Bearer " + key, "Accept": "application/json", "User-Agent": cfg("lookup_user_agent")})
    try:
        from . import safenet
        with safenet.opener(_private_ok()).open(req, timeout=20) as r:   # connects only to the checked address
            raw = r.read(JSON_MAX + 1)
    except urllib.error.HTTPError as e:
        raise ValueError({401: "AirTrail did not accept that API key.", 403: "That API key may not read flights (it needs the flights read scope).", 404: "AirTrail did not have that."}.get(e.code, f"AirTrail answered with an error ({e.code})."))
    except Exception:
        raise ValueError("AirTrail did not answer.")
    if len(raw) > JSON_MAX:
        raise ValueError("AirTrail's answer was too large.")
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise ValueError("AirTrail's answer was not what an AirTrail API sends. Check the address.")


def pull_airtrail(base, key):
    """Every flight of the key's owner from an AirTrail instance (API v1): (flights, my user ids). Tracks are asked for flight by flight when the list does not carry them."""
    me = {}
    try:
        me = (_get(base, "/api/v1/me", key) or {}).get("data") or {}
    except ValueError as e:
        if "API key" in str(e) or "address" in str(e) or "did not answer" in str(e):
            raise
    ids = {x for x in ((me.get("user") or me).get("id"),) if x is not None}
    flights, cursor, pages = [], None, 0
    while pages < 200:
        q = "/api/v1/flights?limit=100" + ("&cursor=" + urllib.parse.quote(cursor) if cursor else "")
        d = _get(base, q, key)
        flights += [f for f in (d.get("data") or []) if isinstance(f, dict)]
        cursor = ((d.get("page") or {}).get("nextCursor"))
        pages += 1
        if not cursor or len(flights) >= cfg_int("myflights_max_per_member"):
            break
    for f in flights[:400]:
        if not f.get("track") and f.get("id") is not None:
            try:
                tr = (_get(base, f"/api/v1/flights/{f['id']}/track", key) or {}).get("data")
                if tr:
                    f["track"] = tr
            except ValueError:
                pass
    return flights, ids


# ------------------------------------------------------------------ a flight's own page and its track

@bp.route("/my/flights/<int:fid>")
@login_required
def flight_page(fid):
    _on()
    row, pax = _load(fid)
    tr = track_of(fid)
    row["when"] = row.get("act_out") or row.get("sched_out") or row.get("act_off") or row.get("sched_off") or row.get("act_in") or row.get("sched_in")
    return render_template("my_flight.html", f=row, pax=pax, der=derive(row), track=tr, stats=track_stats(tr["points"]) if tr else None, hm=hm, dist=dist_txt, stages=STAGES, map_lat=cfg("map_lat"), map_lon=cfg("map_lon"), active="myflights")


@bp.route("/my/flights/<int:fid>/track.json")
@login_required
def track_json(fid):
    _on()
    _load(fid)
    tr = track_of(fid)
    if tr is None:
        abort(404)
    return jsonify({"points": tr["points"], "source": tr["source"]})


@bp.route("/my/flights/<int:fid>/track/upload", methods=["POST"])
@login_required
def track_upload(fid):
    _on()
    row, _p = _load(fid)
    up = request.files.get("file")
    raw = up.read(JSON_MAX + 1) if up else b""
    if not raw:
        return action_response(False, "Choose a track file first.", redirect_endpoint="myflightstracks.flight_page", redirect_kwargs={"fid": fid})
    if len(raw) > JSON_MAX:
        return action_response(False, "That file is too large.", redirect_endpoint="myflightstracks.flight_page", redirect_kwargs={"fid": fid})
    text = raw.decode("utf-8-sig", "replace")
    try:
        if text.lstrip()[:1] in ("{", "["):
            data = json.loads(text)
            fl = flights_of_export(data) if not (isinstance(data, dict) and "coordinates" in data) else [{"track": data}]
            pts = _track_from_json((fl[0].get("track") if fl else None) or {})
            meta = {}
        else:
            pts, meta = read_track(text)
    except ValueError as e:
        return action_response(False, str(e), redirect_endpoint="myflightstracks.flight_page", redirect_kwargs={"fid": fid})
    if len(pts) < 2:
        return action_response(False, "The track has no points with times.", redirect_endpoint="myflightstracks.flight_page", redirect_kwargs={"fid": fid})
    d = get_db()
    t_off, t_on = takeoff_landing(pts)
    sets = {}
    if row["act_off"] is None:
        sets["act_off"] = t_off
    if row["act_on"] is None:
        sets["act_on"] = t_on
    for col, v in (("registration", re.sub(r"[^A-Z0-9-]", "", (meta.get("reg") or "").upper())[:12]), ("aircraft_type", (meta.get("type") or "")[:30]), ("airline", (meta.get("airline") or "")[:60]), ("flight_no", _fmt_flight_no(meta.get("flight_no")))):
        if v and not (row[col] or "").strip():
            sets[col] = v
    if sets:
        d.execute("UPDATE my_flights SET " + ", ".join(f"{k}=%s" for k in sets) + ", updated_at=now() WHERE id=%s", list(sets.values()) + [fid])
    save_track(d, fid, pts, "AirTrail" if text.lstrip()[:1] in ("{", "[") else ("KML" if "kml" in text[:300].lower() else "GPX"))
    d.commit()
    audit("myflights.track", target=current_user.username, detail=f"{row['from_code']} to {row['to_code']}: attached")
    return action_response(True, "The track is attached.", redirect_endpoint="myflightstracks.flight_page", redirect_kwargs={"fid": fid})


@bp.route("/my/flights/<int:fid>/track/remove", methods=["POST"])
@login_required
def track_remove(fid):
    _on()
    row, _p = _load(fid)
    d = get_db()
    d.execute("DELETE FROM my_flight_tracks WHERE flight_id=%s", (fid,))
    d.commit()
    audit("myflights.track", target=current_user.username, detail=f"{row['from_code']} to {row['to_code']}: removed")
    return action_response(True, "The track is removed.", redirect_endpoint="myflightstracks.flight_page", redirect_kwargs={"fid": fid})
