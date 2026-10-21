"""Claiming feeders, station details and station IDs."""
import re
from datetime import timedelta
from urllib.parse import urlparse

from . import airports as ap
from .db import get_db
from .settings import cfg, cfg_int


def _same_ip_window():
    return timedelta(minutes=cfg_int("claim_same_ip_minutes"))


def _paste_window():
    return timedelta(days=cfg_int("claim_paste_days"))

CALLSIGN_RE = re.compile(r"^[A-Z0-9]{1,3}[0-9][A-Z]{1,4}$")
STATION_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,15}$")
AIRPORT_IDENT_RE = re.compile(r"^[A-Z0-9]{3,4}$")
from .privacy import PRIVACY   # the one list of location choices: Exact, Near, Close, General, Wide, Hidden

SETUP_FIELDS = [
    ("setup_receiver", "Receiver / SDR", "For example RTL-SDR Blog V4, Airspy Mini", 200),
    ("setup_antenna", "Antenna", "For example Ground plane, Jetvision, Flightaware", 200),
    ("setup_filter_lna", "Filter / LNA", "For example Uputronics filtered preamp", 200),
    ("setup_feedline", "Feedline", "For example 25 ft LMR-400", 200),
    ("setup_computer", "Computer", "For example Raspberry Pi 4, Unraid server", 200),
    ("setup_software", "Software", "For example ultrafeeder, readsb, Docker", 200),
    ("setup_other", "Other Notes About the Setup", "Anything else worth sharing", 500),
]


def valid_callsign(value):
    return bool(CALLSIGN_RE.match(value or ""))


# ---------------------------------------------------------------- claiming

def my_feeders(user_id):
    return get_db().execute("SELECT * FROM feeders WHERE owner_id=%s ORDER BY first_seen", (user_id,)).fetchall()


def get_owned(uuid, user):
    f = get_db().execute("SELECT * FROM feeders WHERE uuid=%s", (uuid,)).fetchone()
    return f if f and f["owner_id"] == user.id else None


def claim_candidates(ip):
    """Unclaimed feeders connected right now from this public IP."""
    return get_db().execute(
        "SELECT uuid, last_ip, first_seen, last_seen FROM feeders WHERE owner_id IS NULL AND NOT banned AND online "
        "AND last_ip = %s AND last_seen > now() - %s ORDER BY first_seen", (ip, _same_ip_window())).fetchall()


def claim(uuid, user, ip):
    """Claim by UUID. Returns (ok, message). Allowed for a feeder connected from the user's own IP, or
    (pasted UUID) one seen within the last 7 days."""
    uuid = (uuid or "").strip().lower()
    d = get_db()
    f = d.execute("SELECT * FROM feeders WHERE uuid=%s", (uuid,)).fetchone()
    if f is None or f["banned"]:
        return False, "No feeder with that UUID has connected to the hub recently."
    if f["owner_id"] is not None:
        return False, "That feeder has already been claimed. If it is yours, ask an administrator to reassign it."
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    same_ip = f["online"] and f["last_ip"] == ip and now - f["last_seen"] < _same_ip_window()
    if not same_ip and now - f["last_seen"] > _paste_window():
        days = cfg_int("claim_paste_days")
        return False, f"That feeder has not connected in the last {days} day{'' if days == 1 else 's'}, so it cannot be claimed now."
    d.execute("UPDATE feeders SET owner_id=%s, claimed_at=now(), claim_method=%s, privacy=COALESCE(privacy, %s) "
              "WHERE uuid=%s AND owner_id IS NULL",
              (user.id, "same_ip" if same_ip else "pasted_uuid", cfg("default_privacy"), uuid))
    d.execute("INSERT INTO feeder_events (feeder_uuid, event, detail) VALUES (%s,'claimed',%s)",
              (uuid, f"{user.username} ({'same IP' if same_ip else 'pasted UUID'})"))
    d.commit()
    return True, "Feeder claimed. Add its location and details below."


def release(uuid, user, force=False):
    """Give a feeder up. `force` is for an administrator releasing someone else's."""
    d = get_db()
    f = get_owned(uuid, user) if not force else d.execute("SELECT * FROM feeders WHERE uuid=%s AND owner_id IS NOT NULL", (uuid,)).fetchone()
    if f is None:
        return False
    d.execute("UPDATE feeders SET owner_id=NULL, station_id=NULL, station_id_kind=NULL, claimed_at=NULL, claim_method=NULL WHERE uuid=%s", (uuid,))
    d.execute("INSERT INTO feeder_events (feeder_uuid, event, detail) VALUES (%s,'released',%s)", (uuid, user.username))
    d.commit()
    return True


# ---------------------------------------------------------------- details

def _num(value, lo, hi, label, required=False):
    value = (value or "").strip()
    if not value:
        if required:
            raise ValueError(f"{label} is required.")
        return None
    try:
        n = float(value)
    except ValueError:
        raise ValueError(f"{label} must be a number.")
    if not (lo <= n <= hi):
        raise ValueError(f"{label} must be between {lo:g} and {hi:g}.")
    return n


def parse_details(form):
    """Validate the station details form. Returns (values dict, error message or None)."""
    try:
        v = {}
        v["name"] = form.get("name", "").strip()[:60] or None
        v["description"] = form.get("description", "").strip()[:500] or None
        v["notes"] = form.get("notes", "").strip()[:1000] or None
        site = form.get("website", "").strip()
        if site:
            u = urlparse(site)
            if u.scheme not in ("http", "https") or not u.netloc or len(site) > 200:
                raise ValueError("Website must be a full http:// or https:// address.")
        mu = form.get("mlat_user", "").strip()
        if mu and not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", mu):
            raise ValueError("MLAT User Name may only contain letters, digits, dashes, underscores and dots (40 characters at most), the same as MLAT_USER.")
        v["mlat_user"] = mu or None
        v["website"] = site or None
        v["website_public"] = bool(form.get("website_public")) and bool(site)
        lat = _num(form.get("lat"), -90, 90, "Latitude")
        lon = _num(form.get("lon"), -180, 180, "Longitude")
        if (lat is None) != (lon is None):
            raise ValueError("Enter both latitude and longitude, or leave both blank.")
        v["lat"], v["lon"] = lat, lon
        height = _num(form.get("antenna_height_ft"), 0, 3000, "Antenna height")
        ref = form.get("height_ref", "")
        if height is not None and ref not in ("agl", "asl"):
            raise ValueError("Choose whether the antenna height is above ground level (AGL) or above sea level (ASL).")
        v["antenna_height_ft"], v["height_ref"] = height, (ref if height is not None else None)
        v["ground_elev_ft"] = _num(form.get("ground_elev_ft"), -500, 15000, "Ground elevation")
        if v["height_ref"] == "asl" and v["ground_elev_ft"] is None and v["lat"] is not None:
            pass  # allowed: AGL simply cannot be shown without a ground elevation
        privacy = form.get("privacy", "")
        if privacy not in PRIVACY:
            raise ValueError("Choose a location privacy option.")
        v["privacy"] = privacy
        for key, _label, _ph, maxlen in SETUP_FIELDS:
            v[key] = form.get(key, "").strip()[:maxlen] or None
    except ValueError as e:
        return None, str(e)
    return v, None


def save_details(uuid, v):
    cols = ", ".join(f"{k}=%s" for k in v)
    d = get_db()
    d.execute(f"UPDATE feeders SET {cols} WHERE uuid=%s", list(v.values()) + [uuid])
    d.commit()


# ---------------------------------------------------------------- station IDs

def _taken(sid, exclude_uuid=None):
    return get_db().execute("SELECT 1 FROM feeders WHERE lower(station_id)=lower(%s) AND uuid <> COALESCE(%s, '')",
                            (sid, exclude_uuid)).fetchone() is not None


def callsign_candidate(callsign, exclude_uuid=None):
    """KD5VCF, then KD5VCF-2, KD5VCF-3 ... the first free one."""
    base = (callsign or "").upper()
    if not valid_callsign(base):
        return None
    n = 1
    while True:
        sid = base if n == 1 else f"{base}-{n}"
        if not _taken(sid, exclude_uuid):
            return sid
        n += 1


def airport_candidate(lat, lon, exclude_uuid=None):
    """Nearest airport with a recognizable 3 or 4 character identifier, then a two digit sequence: K9M8-01.
    Returns (station_id, airport row) or (None, None) when there is no location or no airport data."""
    if lat is None or lon is None:
        return None, None
    near = [a for a in ap.nearest(lat, lon, 25, ("large_airport", "medium_airport", "small_airport"))
            if AIRPORT_IDENT_RE.match(a["ident"] or "")]
    if not near:
        return None, None
    a = near[0]
    for n in range(1, 100):
        sid = f"{a['ident']}-{n:02d}"
        if not _taken(sid, exclude_uuid):
            return sid, a
    return None, a


def set_station_id(uuid, sid, kind):
    sid = sid.upper()
    if not STATION_ID_RE.match(sid):
        return False, "A station ID is 3 to 16 characters: letters, numbers and dashes."
    if _taken(sid, uuid):
        return False, f"{sid} is already in use."
    d = get_db()
    d.execute("UPDATE feeders SET station_id=%s, station_id_kind=%s WHERE uuid=%s", (sid, kind, uuid))
    d.execute("INSERT INTO feeder_events (feeder_uuid, event, detail) VALUES (%s,'station_id',%s)", (uuid, sid))
    d.commit()
    return True, f"Station ID set to {sid}."
