"""The repeater directory: amateur radio repeaters, linked nodes and gateways in every mode (FM, DMR, D-STAR, System Fusion, P25, ...), kept by this network.

Where the records come from (terms in DATA_SOURCES_TERMS.md): Hearham's open JSON listing (credited on the pages and on every record from it, refreshed no more than daily,
our own User-Agent), records Arkansas hams submit, and records administrators and sysops enter or correct. RepeaterBook and RadioID are NOT used (their terms forbid it); the
pages link out to them instead. A record that a person verified or edited is never overwritten by an import. Reports from members ("wrong tone", "off the air") wait for an
editor, who applies or rejects them; every change is kept in the history."""
import json
import math
import re
import urllib.request

from .db import get_db
from .settings import cfg, cfg_int

KINDS = [("repeater", "Repeater"), ("irlp", "IRLP node"), ("allstar", "AllStar node"), ("echolink", "EchoLink node"), ("winlink", "Winlink gateway"), ("aprs", "APRS digipeater"), ("simplex", "Simplex or calling frequency")]
MODES = ["FM", "DMR", "D-STAR", "Fusion", "P25", "NXDN", "AM"]
BANDS_ALL = ["10 m", "6 m", "2 m", "1.25 m", "70 cm", "33 cm", "23 cm"]
STATUSES = [("active", "Active"), ("off", "Off the air"), ("unverified", "Not verified")]
REPORT_KINDS = [("frequency", "Wrong frequency or offset"), ("tone", "Wrong tone or color code"), ("off", "Off the air"), ("moved", "Moved"), ("closed", "Closed"), ("coords", "Wrong location"), ("other", "Something else")]
CREDIT = "Repeater listings: Hearham (hearham.com, open data) and Arkansas hams"
FIELDS = ("callsign", "city", "state", "lat", "lon", "freq_hz", "offset_hz", "tone", "modes", "color_code", "network", "kind", "description", "power", "restriction", "status", "dmr_id", "attrs")
EDITABLE_BY_OWNER = ("status", "tone", "offset_hz", "freq_hz", "modes", "color_code", "network", "description", "power", "restriction", "city", "dmr_id", "attrs")


def mhz(hz):
    return f"{hz / 1e6:.4f}".rstrip("0").rstrip(".") if hz is not None else ""


def parse_mhz(text):
    """'146.94' or '146,940,000' or '146940000' -> hertz; None when it is not a frequency."""
    try:
        v = float(str(text).replace(",", "").strip())
    except ValueError:
        return None
    if v <= 0:
        return None
    hz = round(v * 1e6) if v < 100000 else round(v)
    return hz if 1_000_000 <= hz <= 6_000_000_000 else None


def parse_offset(text):
    """'+0.6' (MHz), '-600 kHz', '5' (MHz), '600000' (Hz) or '' -> signed hertz; None when it is not an offset."""
    t = str(text or "").strip().lower().replace(" ", "")
    if t in ("", "0", "simplex"):
        return 0
    mult = 1e6
    if t.endswith("khz"):
        t, mult = t[:-3], 1e3
    elif t.endswith("mhz"):
        t = t[:-3]
    try:
        v = float(t)
    except ValueError:
        return None
    if mult == 1e6 and abs(v) >= 1000:
        mult = 1.0   # already hertz
    hz = round(v * mult)
    return hz if abs(hz) <= 100_000_000 else None


def band_of(hz):
    for lo, hi, name in ((28e6, 29.7e6, "10 m"), (50e6, 54e6, "6 m"), (144e6, 148e6, "2 m"), (222e6, 225e6, "1.25 m"), (420e6, 450e6, "70 cm"), (902e6, 928e6, "33 cm"), (1240e6, 1300e6, "23 cm")):
        if lo <= hz <= hi:
            return name
    return "Other"


def output_input(r):
    """(output, input) frequencies in hertz of a record."""
    return r["freq_hz"], r["freq_hz"] + (r["offset_hz"] or 0)


# ------------------------------------------------------------------ Hearham

_MODE_MAP = (("d-star", "D-STAR"), ("dstar", "D-STAR"), ("dmr", "DMR"), ("ysf", "Fusion"), ("fusion", "Fusion"), ("c4fm", "Fusion"), ("p25", "P25"), ("nxdn", "NXDN"), ("fm", "FM"), ("am", "AM"))
_KIND_MAP = {"irlp": "irlp", "allstar": "allstar", "echolink": "echolink", "winlink": "winlink", "aprs": "aprs"}


def normalize_hearham(h):
    """One Hearham record -> our fields (or None when it has no usable frequency or position)."""
    try:
        hz = int(h["frequency"])
        lat, lon = float(h["latitude"]), float(h["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    if hz < 1_000_000 or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    raw = re.sub(r"[^a-z0-9/ -]", "", str(h.get("mode") or "FM").lower())
    modes = []
    for part in re.split(r"[/ ]+", raw):
        for key, label in _MODE_MAP:
            if part.startswith(key) and label not in modes:
                modes.append(label)
                break
    modes = modes or ["FM"]
    enc = str(h.get("encode") or "").strip()
    cc = ""
    tone = ""
    if re.match(r"^cc\d+$", enc.lower()):
        cc = enc[2:]
    elif re.match(r"^\d+(\.\d+)?$", enc) and float(enc) > 0:
        tone = f"{float(enc):.1f}"
    elif re.match(r"^d?\d{3}$", enc.lower()) and enc.lower().startswith("d"):
        tone = enc.upper()
    desc = str(h.get("description") or "").strip()
    net = ""
    m = re.search(r"network:\s*([A-Za-z0-9 ._-]+)", desc, re.I)
    if m:
        net = m.group(1).strip().title()[:40]
    grp = str(h.get("group") or "").strip().lower()
    city = str(h.get("city") or "").strip()
    city = re.sub(r",?\s*(USA|United States)$", "", city).strip()
    return {"callsign": str(h.get("callsign") or "").strip().upper()[:20], "city": city[:80], "lat": lat, "lon": lon, "freq_hz": hz, "offset_hz": int(h.get("offset") or 0), "tone": tone,
            "modes": ",".join(modes), "color_code": cc, "network": net, "kind": _KIND_MAP.get(grp, "repeater"), "description": desc[:600], "power": str(h.get("power") or "")[:20] if str(h.get("power") or "") != "unknown" else "",
            "restriction": str(h.get("restriction") or "")[:200], "status": "active" if h.get("operational") else "off", "source_id": str(h.get("id"))}


def fetch_hearham():
    req = urllib.request.Request(cfg("repeater_hearham_url"), headers={"User-Agent": cfg("lookup_user_agent"), "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read(60_000_000).decode("utf-8"))


def import_hearham(raw=None):
    """Load the listing into the directory for the states in Settings. Returns counts. A record someone verified or edited is left alone; records Hearham no longer lists are removed
    (only those nobody touched). Refuses a result that looks wrong (fewer than 1000 records worldwide) rather than emptying the directory."""
    from . import states
    data = raw if raw is not None else fetch_hearham()
    if not isinstance(data, list) or len(data) < (1000 if raw is None else 1):
        raise RuntimeError("Hearham's listing came back empty or in an unexpected shape; nothing was changed.")
    want = set(states.chosen(cfg("repeater_states")))
    db = get_db()
    added = updated = skipped = 0
    seen = set()
    for h in data:
        rec = normalize_hearham(h)
        if rec is None or states.state_of(rec["lat"], rec["lon"]) not in want:
            continue
        rec["state"] = states.state_of(rec["lat"], rec["lon"])
        sid = rec["source_id"]
        seen.add(sid)
        cur = db.execute("SELECT id, edited, verified_at FROM repeaters WHERE source='hearham' AND source_id=%s", (sid,)).fetchone()
        if cur is None:
            db.execute("INSERT INTO repeaters (callsign, city, state, lat, lon, freq_hz, offset_hz, tone, modes, color_code, network, kind, description, power, restriction, status, source, source_id) "
                       "VALUES (%(callsign)s,%(city)s,%(state)s,%(lat)s,%(lon)s,%(freq_hz)s,%(offset_hz)s,%(tone)s,%(modes)s,%(color_code)s,%(network)s,%(kind)s,%(description)s,%(power)s,%(restriction)s,%(status)s,'hearham',%(source_id)s)", rec)
            added += 1
        elif cur["edited"] or cur["verified_at"]:
            skipped += 1
        else:
            db.execute("UPDATE repeaters SET callsign=%(callsign)s, city=%(city)s, state=%(state)s, lat=%(lat)s, lon=%(lon)s, freq_hz=%(freq_hz)s, offset_hz=%(offset_hz)s, tone=%(tone)s, modes=%(modes)s, color_code=%(color_code)s, "
                       "network=%(network)s, kind=%(kind)s, description=%(description)s, power=%(power)s, restriction=%(restriction)s, status=%(status)s, updated=now() WHERE id=%(id)s", dict(rec, id=cur["id"]))
            updated += 1
    gone = 0
    if len(seen) >= 20:
        for r in db.execute("SELECT id, source_id FROM repeaters WHERE source='hearham' AND NOT edited AND verified_at IS NULL AND owner_id IS NULL").fetchall():
            if r["source_id"] not in seen:
                db.execute("DELETE FROM repeaters WHERE id=%s", (r["id"],))
                gone += 1
    db.commit()
    return {"added": added, "updated": updated, "skipped": skipped, "removed": gone, "total": db.execute("SELECT COUNT(*) AS n FROM repeaters WHERE approved").fetchone()["n"]}


# ------------------------------------------------------------------ APRS repeater beacons and club lists

_STD_OFFSET = (("10 m", 0.1), ("6 m", 1.0), ("2 m", 0.6), ("1.25 m", 1.6), ("70 cm", 5.0), ("33 cm", 12.0), ("23 cm", 20.0))


def _std_offset(hz, sign):
    for name, mhz_ in _STD_OFFSET:
        if band_of(hz) == name:
            return int(sign * mhz_ * 1e6)
    return 0


CTCSS = (67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5, 94.8, 97.4, 100.0, 103.5, 107.2, 110.9, 114.8, 118.8, 123.0, 127.3, 131.8, 136.5, 141.3, 146.2, 151.4, 156.7, 162.2, 167.9, 173.8,
         179.9, 186.2, 192.8, 203.5, 206.5, 210.7, 218.1, 225.7, 229.1, 233.6, 241.8, 250.3, 254.1)


def _snap_tone(v, code):
    """A CTCSS tone as text. A three-digit APRS code drops the decimal (088 is 88.5, 107 is 107.2), so it is matched to the standard tone with that whole number; other values to the nearest standard tone."""
    if code:
        for t in CTCSS:
            if int(t) == int(v):
                return f"{t:.1f}"
    best = min(CTCSS, key=lambda t: abs(t - v))
    return f"{best:.1f}" if abs(best - v) <= 0.6 else f"{v:.1f}"


def parse_aprs_repeater(name, comment):
    """A repeater from an APRS beacon or object: the frequency (in the object's name or the comment), the offset, the tone and the modes, as far as the text says. None when no amateur frequency is found.
    Beacons are free text, so only what is clearly stated is taken: 'CC2' is a DMR color code, 'T107' or 'PL114.8' a tone, '+060' an offset of 0.60 MHz, '+500' of 5 MHz."""
    text = f"{name} {comment}"
    m = re.search(r"(\d{2,4}\.\d{2,4})\s*MHz", comment or "", re.I) or re.search(r"^(\d{2,4}\.\d{2,3})", name or "") or re.search(r"(?<![\d.])(\d{3}\.\d{2,3})(?![\d.])", comment or "")
    hz = parse_mhz(m.group(1)) if m else None
    if not hz or band_of(hz) == "Other":
        return None
    off = None
    mo = re.search(r"(?<![\d.])([+-])\s?(\d{3})(?![\d.])", comment or "")
    if mo:
        off = int(mo.group(1) + str(int(mo.group(2)))) * 10_000   # APRS repeater objects give the offset in units of 10 kHz: +060 is 0.6 MHz, +500 is 5 MHz
    else:
        mo = re.search(r"(?<![\w.])([+-])\s?(\d\.\d{1,2})\s*(?:MHz)?(?![\d.])", comment or "")
        if mo:
            off = int(float(mo.group(1) + mo.group(2)) * 1e6)
        else:
            ms = re.search(r"(?<![\w.])([+-])(?=\s|$)", comment or "") or re.search(r"\d(?:MHz)?\s*([+-])\s*(?:T|PL|\d{2,3}\.\d)", comment or "", re.I)
            if ms:
                off = _std_offset(hz, 1 if ms.group(1) == "+" else -1)
    tone = ""
    mt = re.search(r"(?<![A-Z0-9])T\s?(\d{3})(?!\d)", text) or re.search(r"\bPL\s?i?\s?(\d{2,3}(?:\.\d)?)", text, re.I)
    if mt:
        tone = _snap_tone(float(mt.group(1)), "." not in mt.group(1))   # T088 is the code for 88.5 Hz; PL114.8 is exact
    else:
        mt = re.search(r"[\s,/+-](\d{2,3}\.\d)(?:\s*(?:Hz|PL))?(?=\s|$|,)", comment or "")
        if mt and 60 <= float(mt.group(1)) <= 260:
            tone = _snap_tone(float(mt.group(1)), False)
        else:
            md = re.search(r"\bD(\d{3})\b", text)
            if md:
                tone = "D" + md.group(1)
    if tone and not tone.startswith("D") and not (60 <= float(tone) <= 260):
        tone = ""
    up = text.upper()
    modes = []
    for pat, label in (("DMR", "DMR"), ("C4FM|FUSION|YSF", "Fusion"), ("D-?STAR", "D-STAR"), ("P25", "P25"), ("NXDN", "NXDN")):
        if re.search(pat, up):
            modes.append(label)
    cc = ""
    mc = re.search(r"\bCC\s?(\d{1,2})\b", up)
    if mc and "DMR" in modes:
        cc = mc.group(1)
    if not modes:
        modes = ["FM"]
    net = "Brandmeister" if re.search(r"BRAND?M[IE]+STER|\bBM\b", up) else ""
    return {"freq_hz": hz, "offset_hz": 0 if off is None else off, "tone": tone, "modes": ",".join(modes or ["FM"]), "color_code": cc, "network": net,
            "has_offset": off is not None, "allstar": bool(re.search(r"ALL\s?STA?R|ALSTR", up))}


def _near_existing(db, hz, lat, lon, km=3.0):
    """An existing record on (nearly) the same frequency within a few kilometers: the same repeater."""
    dlat, dlon = km / 111.0, km / (111.0 * max(0.2, math.cos(math.radians(lat))))
    return db.execute("SELECT id FROM repeaters WHERE ABS(freq_hz - %s) < 6000 AND lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s LIMIT 1", (hz, lat - dlat, lat + dlat, lon - dlon, lon + dlon)).fetchone()


def import_aprs(stations):
    """Add the repeaters the APRS network announces that the directory does not have yet (`stations`: the APRS service's repeater beacons and objects for the area). Records are marked as coming
    from APRS and not verified; a repeater already listed on that frequency nearby is left alone. Returns counts."""
    from . import states
    want = set(states.chosen(cfg("repeater_states")))
    db = get_db()
    added = known = unreadable = 0
    for r in stations:
        p = parse_aprs_repeater(r["call"], r.get("comment") or "")
        if p is None or r.get("lat") is None:
            unreadable += 1
            continue
        st = states.state_of(r["lat"], r["lon"])
        if st not in want:
            continue
        if _near_existing(db, p["freq_hz"], r["lat"], r["lon"]):
            known += 1
            continue
        base = re.sub(r"-[A-Z]?\d*$", "", r["call"])
        call = base if re.match(r"^[A-Z]{1,2}\d[A-Z]{1,3}$", base) else ""
        if db.execute("SELECT 1 FROM repeaters WHERE source='aprs' AND source_id=%s", (r["call"],)).fetchone():
            known += 1
            continue
        db.execute("INSERT INTO repeaters (callsign, city, state, lat, lon, freq_hz, offset_hz, tone, modes, color_code, network, kind, description, status, source, source_id) VALUES (%s,'',%s,%s,%s,%s,%s,%s,%s,%s,%s,'repeater',%s,'unverified','aprs',%s)",
                   (call, st, r["lat"], r["lon"], p["freq_hz"], p["offset_hz"], p["tone"], p["modes"], p["color_code"], p["network"], ("Announced on APRS: " + (r.get("comment") or ""))[:600], r["call"]))
        added += 1
    db.commit()
    return {"added": added, "known": known, "unreadable": unreadable}


CSV_FIELDS = ("freq", "offset", "tone", "callsign", "city", "state", "lat", "lon", "modes", "color_code", "network", "kind", "power", "restriction", "notes", "status")


def import_csv(text, source_label, actor):
    """Load a list a club or council gave us permission to use: one repeater per row, columns as in CSV_FIELDS (only freq is required; a row with a city but no position is placed at the city).
    A repeater already listed on that frequency nearby is left alone. Returns (counts, problems)."""
    import csv
    import io
    from . import places, states
    source_label = re.sub(r"[^A-Za-z0-9 ._,'()-]", "", source_label).strip()[:60] or "club list"
    db = get_db()
    added = known = 0
    problems = []
    rdr = csv.DictReader(io.StringIO(text))
    if not rdr.fieldnames or "freq" not in [f.strip().lower() for f in rdr.fieldnames]:
        return {"added": 0, "known": 0}, ["The first row must name the columns, and one of them must be freq (the output frequency in MHz)."]
    for i, raw in enumerate(rdr, start=2):
        row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        hz = parse_mhz(row.get("freq", ""))
        if not hz or band_of(hz) == "Other":
            problems.append(f"Row {i}: '{row.get('freq', '')}' is not an amateur band frequency in MHz.")
            continue
        off = parse_offset(row.get("offset", ""))
        if off is None:
            problems.append(f"Row {i}: the offset '{row.get('offset', '')}' is not a number in MHz.")
            continue
        try:
            lat, lon = float(row["lat"]), float(row["lon"])
        except (KeyError, ValueError):
            lat = lon = None
        if lat is None and row.get("city"):
            hit = places.search(row["city"] + ", " + (row.get("state") or "AR"), limit=1)
            if hit:
                lat, lon = hit[0]["lat"], hit[0]["lon"]
        if lat is None:
            problems.append(f"Row {i}: no position and the city '{row.get('city', '')}' was not found.")
            continue
        if _near_existing(db, hz, lat, lon):
            known += 1
            continue
        modes = [m for m in re.split(r"[ ,/;]+", row.get("modes", "FM") or "FM") if m in MODES] or ["FM"]
        tone = row.get("tone", "").upper()
        if tone and not re.match(r"^(\d{2,3}(\.\d)?|D\d{3}[NI]?)$", tone):
            tone = ""
        state = states.state_of(lat, lon)
        db.execute("INSERT INTO repeaters (callsign, city, state, lat, lon, freq_hz, offset_hz, tone, modes, color_code, network, kind, description, power, restriction, status, source, source_id, edited) "
                   "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE)",
                   (re.sub(r"[^A-Za-z0-9/-]", "", row.get("callsign", "")).upper()[:20], row.get("city", "")[:80], state if state != "-" else (row.get("state", "")[:2].upper()), lat, lon, hz, off, tone, ",".join(modes),
                    row.get("color_code", "")[:2], row.get("network", "")[:40], row.get("kind") if row.get("kind") in dict(KINDS) else "repeater", row.get("notes", "")[:600], row.get("power", "")[:20], row.get("restriction", "")[:200],
                    row.get("status") if row.get("status") in dict(STATUSES) else "unverified", "list:" + source_label, f"{source_label}:{i}"))
        added += 1
    db.commit()
    return {"added": added, "known": known}, problems


# ------------------------------------------------------------------ queries

def get(rid):
    return get_db().execute("SELECT r.*, u.username AS owner_name FROM repeaters r LEFT JOIN users u ON u.id = r.owner_id WHERE r.id=%s", (rid,)).fetchone()


def search(q="", mode="", band="", kind="", status="", state="", city="", only_public=True, sort="freq", limit=60, offset=0, direction="asc", group=0):
    where, args = [], {}
    if group:
        where.append("r.id IN (SELECT repeater_id FROM repeater_group_members WHERE group_id = %(group)s)")
        args["group"] = int(group)
    if only_public:
        where.append("r.approved")
    if q:
        freq = re.fullmatch(r"[0-9.,\s]+", q) is not None   # only a number (146.94) is also tried as a frequency
        where.append("(r.callsign ILIKE %(q)s OR r.city ILIKE %(q)s OR r.description ILIKE %(q)s OR r.network ILIKE %(q)s" + (" OR CAST(r.freq_hz / 1000 AS TEXT) LIKE %(f)s)" if freq else ")"))
        args["q"] = f"%{q}%"
        if freq:
            args["f"] = f"%{re.sub(r'[^0-9]', '', q)}%" if re.sub(r"[^0-9]", "", q) else "-"
    if mode:
        where.append("%(mode)s = ANY (string_to_array(r.modes, ','))")
        args["mode"] = mode
    if kind:
        where.append("r.kind = %(kind)s")
        args["kind"] = kind
    if status:
        where.append("r.status = %(status)s")
        args["status"] = status
    if state:
        where.append("r.state = %(state)s")
        args["state"] = state
    if city:
        where.append("r.city ILIKE %(city)s")
        args["city"] = city + "%"
    bands = {"10 m": (28e6, 29.7e6), "6 m": (50e6, 54e6), "2 m": (144e6, 148e6), "1.25 m": (222e6, 225e6), "70 cm": (420e6, 450e6), "33 cm": (902e6, 928e6), "23 cm": (1240e6, 1300e6)}
    if band in bands:
        where.append("r.freq_hz BETWEEN %(blo)s AND %(bhi)s")
        args["blo"], args["bhi"] = int(bands[band][0]), int(bands[band][1])
    w = (" WHERE " + " AND ".join(where)) if where else ""
    col = {"freq": "r.freq_hz", "offset": "r.offset_hz", "tone": "r.tone", "modes": "r.modes", "city": "r.city", "call": "r.callsign", "kind": "r.kind", "status": "r.status", "updated": "r.updated"}.get(sort, "r.freq_hz")
    order = f"{col} {'DESC' if direction == 'desc' else 'ASC'}, r.freq_hz, r.callsign"
    db = get_db()
    total = db.execute(f"SELECT COUNT(*) AS n FROM repeaters r{w}", args).fetchone()["n"]
    rows = db.execute(f"SELECT r.*, (SELECT count(*) FROM repeater_talkgroups t WHERE t.repeater_id = r.id) AS tg_n FROM repeaters r{w} ORDER BY {order} LIMIT %(lim)s OFFSET %(off)s", dict(args, lim=limit, off=offset)).fetchall()
    return total, rows


# ------------------------------------------------------------------ linked repeaters: a named system and the repeaters that are linked together in it

def all_groups():
    """[{id, name, notes, n}] every group with how many repeaters it holds, biggest first."""
    return get_db().execute("SELECT g.id, g.name, g.notes, (SELECT COUNT(*) FROM repeater_group_members m WHERE m.group_id = g.id) AS n FROM repeater_groups g ORDER BY n DESC, lower(g.name)").fetchall()


def group_names(ids):
    """{repeater id: [(group id, name)]} for the repeaters on a page."""
    ids = [int(i) for i in ids]
    if not ids:
        return {}
    out = {}
    for r in get_db().execute("SELECT m.repeater_id, g.id, g.name FROM repeater_group_members m JOIN repeater_groups g ON g.id = m.group_id WHERE m.repeater_id = ANY(%s) ORDER BY lower(g.name)", (ids,)).fetchall():
        out.setdefault(r["repeater_id"], []).append((r["id"], r["name"]))
    return out


def group_members(gid):
    return get_db().execute("SELECT r.id, r.callsign, r.city, r.state, r.freq_hz, r.offset_hz, r.modes, r.lat, r.lon, r.status FROM repeater_group_members m JOIN repeaters r ON r.id = m.repeater_id "
                            "WHERE m.group_id = %s AND r.approved ORDER BY r.freq_hz, r.callsign", (gid,)).fetchall()


def groups_of(rid):
    """[{id, name, notes, others: [member rows except this repeater]}] for the repeater page."""
    out = []
    for g in get_db().execute("SELECT g.id, g.name, g.notes FROM repeater_group_members m JOIN repeater_groups g ON g.id = m.group_id WHERE m.repeater_id = %s ORDER BY lower(g.name)", (rid,)).fetchall():
        out.append({"id": g["id"], "name": g["name"], "notes": g["notes"], "others": [m for m in group_members(g["id"]) if m["id"] != rid]})
    return out


def make_group(name, notes, user_id):
    """(id, error). A name is kept once, whatever its case."""
    name = re.sub(r"\s+", " ", (name or "").strip())[:60]
    if len(name) < 2:
        return None, "Give the linked system a name."
    d = get_db()
    row = d.execute("SELECT id FROM repeater_groups WHERE lower(name) = lower(%s)", (name,)).fetchone()
    if row:
        return row["id"], None
    gid = d.execute("INSERT INTO repeater_groups (name, notes, created_by) VALUES (%s,%s,%s) RETURNING id", (name, (notes or "").strip()[:300], user_id)).fetchone()["id"]
    d.commit()
    return gid, None


def link(gid, rid, user_id):
    d = get_db()
    d.execute("INSERT INTO repeater_group_members (group_id, repeater_id, added_by) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (gid, rid, user_id))
    d.commit()


def unlink(gid, rid):
    d = get_db()
    d.execute("DELETE FROM repeater_group_members WHERE group_id=%s AND repeater_id=%s", (gid, rid))
    d.execute("DELETE FROM repeater_groups WHERE id=%s AND NOT EXISTS (SELECT 1 FROM repeater_group_members WHERE group_id=%s)", (gid, gid))   # a system with nothing left in it goes away
    d.commit()


def delete_group(gid):
    d = get_db()
    d.execute("DELETE FROM repeater_groups WHERE id=%s", (gid,))
    d.commit()


def _km(a, b):
    import math
    return math.hypot((a[0] - b[0]) * 111.2, (a[1] - b[1]) * 111.2 * math.cos(math.radians((a[0] + b[0]) / 2)))


def group_lines(members):
    """The links to draw for one group: each repeater joined to its nearest neighbor in the system, as the shortest set of lines that joins them all (so a system of ten is nine lines, not forty-five).
    members: [{id, lat, lon}] with positions. Returns [(id_a, id_b)]."""
    pts = [m for m in members if m["lat"] is not None and m["lon"] is not None]
    if len(pts) < 2:
        return []
    inside, out = {pts[0]["id"]}, []
    left = {m["id"]: m for m in pts[1:]}
    byid = {m["id"]: m for m in pts}
    while left:
        best = None
        for i in inside:
            for j, m in left.items():
                d = _km((byid[i]["lat"], byid[i]["lon"]), (m["lat"], m["lon"]))
                if best is None or d < best[0]:
                    best = (d, i, j)
        out.append((best[1], best[2]))
        inside.add(best[2])
        del left[best[2]]
    return out


def links_for_map():
    """[{id, name, notes, members: [{id, call, lat, lon, out}], lines: [[lat1, lon1, lat2, lon2]]}] for the groups with two or more repeaters on the map."""
    out = []
    for g in all_groups():
        ms = [dict(m) for m in group_members(g["id"])]
        if len(ms) < 2:
            continue
        pos = {m["id"]: m for m in ms if m["lat"] is not None}
        lines = [[pos[a]["lat"], pos[a]["lon"], pos[b]["lat"], pos[b]["lon"]] for a, b in group_lines(ms)]
        out.append({"id": g["id"], "name": g["name"], "notes": g["notes"], "members": [{"id": m["id"], "call": m["callsign"], "city": m["city"], "lat": m["lat"], "lon": m["lon"], "out": m["freq_hz"]} for m in ms], "lines": lines})
    return out


def map_points(only_status=""):
    """Everything with a position, for the maps (small records, one request)."""
    rows = get_db().execute("SELECT id, callsign, city, state, lat, lon, freq_hz, offset_hz, tone, modes, color_code, network, kind, status FROM repeaters WHERE approved AND lat IS NOT NULL "
                            + ("AND status = %s " if only_status else "") + "ORDER BY freq_hz", (only_status,) if only_status else ()).fetchall()
    gn = group_names([r["id"] for r in rows])
    return [{"groups": [{"id": g, "name": n} for g, n in gn.get(r["id"], [])], "id": r["id"], "call": r["callsign"], "city": r["city"], "lat": r["lat"], "lon": r["lon"], "out": r["freq_hz"], "off": r["offset_hz"], "tone": r["tone"], "modes": r["modes"].split(","), "cc": r["color_code"],
             "net": r["network"], "kind": r["kind"], "status": r["status"]} for r in rows]


def counts():
    d = get_db()
    r = d.execute("SELECT COUNT(*) FILTER (WHERE approved) AS total, COUNT(*) FILTER (WHERE approved AND status='active') AS active, COUNT(*) FILTER (WHERE approved AND 'DMR' = ANY (string_to_array(modes, ','))) AS dmr, "
                  "COUNT(*) FILTER (WHERE NOT approved) AS pending, COUNT(*) FILTER (WHERE approved AND source='hearham') AS hearham, COUNT(*) FILTER (WHERE approved AND verified_at IS NOT NULL) AS verified FROM repeaters").fetchone()
    r = dict(r)
    r["reports"] = d.execute("SELECT COUNT(*) AS n FROM repeater_reports WHERE status='open'").fetchone()["n"]
    r["claims"] = d.execute("SELECT COUNT(*) AS n FROM repeaters WHERE claim_status='pending'").fetchone()["n"]
    return r


def history(rid, limit=30):
    return get_db().execute("SELECT actor, action, detail, ts FROM repeater_edits WHERE repeater_id=%s ORDER BY ts DESC LIMIT %s", (rid, limit)).fetchall()


def log_edit(rid, actor, action, detail=""):
    get_db().execute("INSERT INTO repeater_edits (repeater_id, actor, action, detail) VALUES (%s,%s,%s,%s)", (rid, actor, action, detail[:1000]))


# ------------------------------------------------------------------ changes

def clean(form):
    """Validated fields from a submitted form: (fields, error)."""
    f = {}
    hz = parse_mhz(form.get("freq", ""))
    if hz is None:
        return None, "Enter the repeater's output frequency in MHz, for example 146.94."
    f["freq_hz"] = hz
    off = parse_offset(form.get("offset", ""))
    if off is None:
        return None, "The offset is a number in MHz such as +0.6 or -5, or 0 for simplex."
    f["offset_hz"] = off
    f["callsign"] = re.sub(r"[^A-Za-z0-9/-]", "", form.get("callsign", "")).upper()[:20]
    f["city"] = form.get("city", "").strip()[:80]
    modes = [m for m in form.getlist("modes") if m in MODES] if hasattr(form, "getlist") else [m for m in (form.get("modes") or "FM").split(",") if m in MODES]
    f["modes"] = ",".join(modes) or "FM"
    tone = form.get("tone", "").strip().upper()
    if tone and not re.match(r"^(\d{2,3}(\.\d)?|D\d{3}[NI]?)$", tone):
        return None, "A tone is a CTCSS number such as 100.0 or a DCS code such as D023."
    f["tone"] = tone
    cc = form.get("color_code", "").strip()
    if cc and not re.match(r"^\d{1,2}$", cc):
        return None, "A DMR color code is a number from 0 to 15."
    f["color_code"] = cc
    f["network"] = form.get("network", "").strip()[:40]
    f["kind"] = form.get("kind") if form.get("kind") in dict(KINDS) else "repeater"
    f["description"] = form.get("description", "").strip()[:600]
    f["power"] = form.get("power", "").strip()[:20]
    f["restriction"] = form.get("restriction", "").strip()[:200]
    f["status"] = form.get("status") if form.get("status") in dict(STATUSES) else "unverified"
    dm = (form.get("dmr_id") or "").strip()
    if dm and not re.match(r"^\d{3,9}$", dm):
        return None, "A DMR ID is a number of three to nine digits."
    f["dmr_id"] = int(dm) if dm else None
    from .repeater_digital import attrs_from_form
    f["attrs"] = attrs_from_form(form)
    try:
        la, lo = float(form.get("lat", "")), float(form.get("lon", ""))
        if not (-90 <= la <= 90 and -180 <= lo <= 180):
            raise ValueError
    except ValueError:
        return None, "Give the repeater's position as latitude and longitude (for example 34.8, -92.3)."
    f["lat"], f["lon"] = la, lo
    from . import states
    f["state"] = states.state_of(la, lo)
    if f["state"] == "-":
        f["state"] = (form.get("state") or "").strip().upper()[:2]
    return f, ""


def create(fields, actor, source="local", approved=True, submitted_by=None, owner_id=None):
    cols = list(fields)
    row = get_db().execute(f"INSERT INTO repeaters ({','.join(cols)}, source, approved, submitted_by, owner_id, edited) VALUES ({','.join('%(' + c + ')s' for c in cols)}, %(source)s, %(approved)s, %(sb)s, %(ow)s, TRUE) RETURNING id",
                           dict(fields, source=source, approved=approved, sb=submitted_by, ow=owner_id)).fetchone()
    log_edit(row["id"], actor, "created" if approved else "submitted", f"{mhz(fields['freq_hz'])} MHz {fields['callsign']}")
    get_db().commit()
    return row["id"]


def update(rid, fields, actor, allowed=None):
    """Apply changes (only the allowed fields), mark the record as edited so imports leave it alone, and keep the history of what changed."""
    r = get(rid)
    if r is None:
        return False
    changes = []
    sets = {}
    for k, v in fields.items():
        if allowed is not None and k not in allowed:
            continue
        if k == "attrs" and json.loads(r["attrs"] or "{}") == json.loads(v or "{}"):
            continue
        if k in FIELDS and r[k] != v:
            sets[k] = v
            changes.append(f"{k}: {r[k]!s} -> {v!s}")
    if not sets:
        return True
    get_db().execute("UPDATE repeaters SET " + ",".join(f"{k}=%({k})s" for k in sets) + ", edited=TRUE, updated=now() WHERE id=%(id)s", dict(sets, id=rid))
    log_edit(rid, actor, "edited", "; ".join(changes))
    get_db().commit()
    return True


def verify(rid, actor):
    get_db().execute("UPDATE repeaters SET verified_at=now(), verified_by=%s, edited=TRUE, status=CASE WHEN status='unverified' THEN 'active' ELSE status END, updated=now() WHERE id=%s", (actor, rid))
    log_edit(rid, actor, "verified")
    get_db().commit()


def report(user, rid, kind, note, suggested=None):
    """A member's report about a record (waits for an editor). Returns (ok, message)."""
    if kind not in dict(REPORT_KINDS):
        return False, "Choose what is wrong."
    note = (note or "").strip()[:600]
    d = get_db()
    if d.execute("SELECT COUNT(*) AS n FROM repeater_reports WHERE user_id=%s AND created > now() - interval '1 hour'", (user.id,)).fetchone()["n"] >= cfg_int("repeater_reports_per_hour"):
        return False, "You have sent a lot of reports this hour. Try again later."
    if d.execute("SELECT 1 FROM repeater_reports WHERE user_id=%s AND repeater_id=%s AND kind=%s AND status='open'", (user.id, rid, kind)).fetchone():
        return False, "You already have that report open for this repeater."
    if kind == "other" and not note:
        return False, "Say what is wrong."
    d.execute("INSERT INTO repeater_reports (repeater_id, user_id, kind, note, suggested) VALUES (%s,%s,%s,%s,%s)", (rid, user.id, kind, note, json.dumps(suggested or {})))
    d.commit()
    return True, "Thank you. An editor will look at your report."


def handle_report(report_id, actor, apply):
    """Apply (with the suggested changes, when it has any) or reject a report."""
    d = get_db()
    r = d.execute("SELECT * FROM repeater_reports WHERE id=%s AND status='open'", (report_id,)).fetchone()
    if r is None:
        return False
    if apply:
        sug = {}
        try:
            sug = json.loads(r["suggested"] or "{}")
        except ValueError:
            pass
        if r["kind"] in ("off", "closed"):
            sug.setdefault("status", "off")
        if sug:
            update(r["repeater_id"], sug, actor)
        log_edit(r["repeater_id"], actor, "report applied", f"{r['kind']}: {r['note']}"[:300])
    d.execute("UPDATE repeater_reports SET status=%s, handled_by=%s, handled_at=now() WHERE id=%s", ("applied" if apply else "rejected", actor, report_id))
    d.commit()
    return True
