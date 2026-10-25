"""The portal's side of ACARS (deploy/acars, its own container and database): members' receivers send their datalink messages (ACARS, VDL2, HFDL) to a private address on this site, the portal
checks the station's token and passes them on to the service, and the pages read them back. When the service is away every call returns a plain note and the rest of the site works as
before. Terms: DATA_SOURCES_TERMS.md (only members' own receivers; the text is public by Marty's decision, a setting can narrow it)."""
import hashlib
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from . import privacy, vault
from .cache import cached
from .db import get_db
from .settings import cfg, cfg_int

STATION_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,15}$")
MODES = {"acars": "ACARS (VHF)", "vdl2": "VDL Mode 2", "hfdl": "HFDL (HF)", "iridium": "Iridium", "inmarsat": "Inmarsat"}
VISIBILITY = [("everyone", "Everyone"), ("members", "Signed-in members only"), ("none", "Nobody (metadata only)")]
CREDIT = "ACARS messages: received by members of the network on open, public airwaves"
SELECT = ("SELECT s.*, u.username AS owner_name, u.display_name AS owner_display FROM acars_stations s LEFT JOIN users u ON u.id = s.owner_id ")
_bad = {}


def enabled():
    return cfg("acars_enabled") == "1"


def call(path, method="GET", body=None, timeout=8):
    """(ok, data or message). Never raises."""
    if not enabled():
        return False, "ACARS is switched off in Settings."
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(cfg("acars_url").rstrip("/") + path, data=data, method=method, headers=dict({"User-Agent": cfg("lookup_user_agent"), "Content-Type": "application/json"}, **__import__("app.servicecall", fromlist=["headers"]).headers()))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, json.loads(r.read(20_000_000).decode("utf-8"))
    except urllib.error.HTTPError as e:
        return False, f"The ACARS service answered with an error ({e.code})."
    except Exception as e:
        return False, "The ACARS service did not answer (" + type(e).__name__ + ")."


def push_config():
    return call("/config", "POST", {"keep_days": cfg("acars_keep_days"), "max_text": cfg("acars_max_text"), "dedupe_seconds": cfg("acars_dedupe_seconds")})


def stats():
    def load():
        ok, d = call("/stats", timeout=3)
        if ok and not d.get("messages") and d.get("config", {}).get("keep_days") != cfg("acars_keep_days"):
            push_config()
        return {"ok": ok, "data": d if ok else None, "note": "" if ok else d}
    return cached("acars_stats", load, 15)


def positions(minutes):
    """[{tail, flight, hex, lat, lon, alt_ft, ts, label}]: the newest position each aircraft reported in an ACARS message lately (no text), for the live map. [] when the service is away."""
    minutes = max(5, min(int(minutes), 720))

    def load():
        ok, d = call("/positions?minutes=" + str(minutes), timeout=5)
        return d.get("positions", []) if ok and isinstance(d, dict) else []
    return cached(("acars_positions", minutes), load, 30)


# ------------------------------------------------------------------ what visitors may see

def can_see_text(user):
    v = cfg("acars_text_visibility")
    return v == "everyone" or (v == "members" and user is not None and getattr(user, "is_authenticated", False))


def hidden_tails():
    return {r["tail"] for r in get_db().execute("SELECT tail FROM acars_hidden").fetchall()}


def prepare(rows, user):
    """Messages ready for a page: the aircraft that asked to be left out are gone, the text is dropped where the setting says so, the station name is the public one."""
    hid = hidden_tails()
    names = {r["station_id"]: r for r in get_db().execute("SELECT station_id, name, public FROM acars_stations").fetchall()}
    show = can_see_text(user)
    out = []
    for m in rows:
        if (m.get("tail") or "").upper() in hid:
            continue
        m = dict(m)
        st = names.get(m.get("station"))
        m["station_name"] = (st["name"] or m["station"]) if st and st["public"] else ("" if st else m.get("station", ""))
        if not show:
            m["text"] = ""
            m["text_hidden"] = True
        out.append(m)
    return out


def messages(args, user, limit=100):
    q = {k: v for k, v in args.items() if v and k in ("tail", "flight", "label", "station", "mode", "hex", "q", "hours", "offset", "position")}
    q["limit"] = max(1, min(limit, 300))
    ok, d = call("/messages?" + urllib.parse.urlencode(q))
    return (prepare(d.get("messages", []), user) if ok else []), ("" if ok else d)


def for_aircraft(hexid, tail, user):
    key = hexid or tail
    if not key:
        return []
    ok, d = call("/aircraft/" + urllib.parse.quote(key, safe="") + "?limit=12")
    rows = d.get("messages", []) if ok else []
    if ok and hexid and tail and not rows:
        ok, d = call("/aircraft/" + urllib.parse.quote(tail, safe="") + "?limit=12")
        rows = d.get("messages", []) if ok else []
    return prepare(rows, user)


# ------------------------------------------------------------------ stations (members add them; the pattern of weather stations)

def new_token():
    return secrets.token_urlsafe(24)


def token_hash(token):
    return hashlib.sha256(("acars\0" + token).encode()).hexdigest()


def by_token(token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", token or ""):
        return None
    return get_db().execute(SELECT + "WHERE s.token_hash=%s", (token_hash(token),)).fetchone()


def push_token(row):
    return vault.unseal(row["token_sealed"])


def too_many_bad_tokens(ip, note=False):
    now = time.time()
    hits = [t for t in _bad.get(ip, []) if now - t < 60]
    if note:
        hits.append(now)
    _bad[ip] = hits
    if len(_bad) > 5000:
        _bad.clear()
    return len(hits) >= max(1, cfg_int("wx_bad_token_per_minute"))


def mine(uid):
    return get_db().execute(SELECT + "WHERE s.owner_id=%s ORDER BY s.created", (uid,)).fetchall()


def all_stations():
    return get_db().execute(SELECT + "ORDER BY s.created DESC").fetchall()


def get(sid):
    return get_db().execute(SELECT + "WHERE lower(s.station_id)=lower(%s)", (sid,)).fetchone()


def get_id(wid):
    return get_db().execute(SELECT + "WHERE s.id=%s", (wid,)).fetchone()


def public_stations():
    return get_db().execute(SELECT + "WHERE s.public AND NOT s.disabled ORDER BY s.messages DESC, s.created").fetchall()


def create(user_id, sid, name, lat=None, lon=None, privacy_level="general", bands="", hardware="", description="", public=False, by_admin=False):
    """Add a station. Returns (id, error)."""
    d = get_db()
    sid = (sid or "").strip().upper()
    if not STATION_ID_RE.match(sid):
        return None, "A station ID is 3 to 16 characters: letters, numbers and dashes."
    if d.execute("SELECT 1 FROM acars_stations WHERE lower(station_id)=lower(%s)", (sid,)).fetchone():
        return None, f"{sid} is already in use."
    if not by_admin and d.execute("SELECT COUNT(*) AS n FROM acars_stations WHERE owner_id=%s", (user_id,)).fetchone()["n"] >= cfg_int("acars_max_per_user"):
        return None, f"You can have {cfg_int('acars_max_per_user')} ACARS stations."
    token = new_token()
    row = d.execute("INSERT INTO acars_stations (station_id, owner_id, name, lat, lon, privacy, bands, hardware, description, public, token_hash, token_sealed) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (sid, user_id, (name or "").strip()[:60], lat, lon, privacy_level if privacy_level in privacy.PRIVACY else "general", (bands or "").strip()[:120], (hardware or "").strip()[:200],
                     (description or "").strip()[:500], bool(public), token_hash(token), vault.seal(token))).fetchone()
    d.commit()
    return row["id"], None


def shown_position(row):
    return privacy.position(row["lat"], row["lon"], row["privacy"])


def is_online(row):
    from datetime import datetime, timezone
    return bool(row["last_report"]) and (datetime.now(timezone.utc) - row["last_report"]).total_seconds() < cfg_int("acars_stale_minutes") * 60


def note_report(station_id, n):
    d = get_db()
    d.execute("UPDATE acars_stations SET last_report=now(), messages = messages + %s WHERE station_id=%s", (n, station_id))
    d.commit()


def parse_body(raw):
    """The body of an upload: one JSON object, a list of objects, or one object per line (what acars_router writes). Returns a list of dicts (at most the number set in Settings)."""
    text = raw.decode("utf-8", "replace").strip()
    out = []
    try:
        j = json.loads(text)
        out = j if isinstance(j, list) else (j.get("messages") if isinstance(j, dict) and isinstance(j.get("messages"), list) else [j])
    except ValueError:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return [m for m in out if isinstance(m, dict)][: max(1, cfg_int("acars_max_per_post"))]


def forward(station_id, items):
    """Hand the messages to the service, leaving out aircraft that asked not to be shown. (ok, answer or note)."""
    hid = hidden_tails()
    if hid:
        items = [m for m in items if str(m.get("tail") or m.get("reg") or "").strip().upper().lstrip(".") not in hid]
    if not items:
        return True, {"new": 0, "duplicates": 0, "rejected": 0}
    return call("/ingest", "POST", {"station": station_id, "messages": items}, timeout=10)


def hide_tail(tail, reason, user_id):
    tail = tail.strip().upper().lstrip(".")
    d = get_db()
    d.execute("INSERT INTO acars_hidden (tail, reason, added_by) VALUES (%s,%s,%s) ON CONFLICT (tail) DO UPDATE SET reason=EXCLUDED.reason", (tail, reason[:200], user_id))
    d.commit()
    return call("/delete", "POST", {"tail": tail})


def unhide_tail(tail):
    d = get_db()
    d.execute("DELETE FROM acars_hidden WHERE tail=%s", (tail.strip().upper(),))
    d.commit()


def hidden_rows():
    return get_db().execute("SELECT h.tail, h.reason, h.added, u.username FROM acars_hidden h LEFT JOIN users u ON u.id = h.added_by ORDER BY h.tail").fetchall()
