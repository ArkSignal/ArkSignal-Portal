"""Signed-in members send balloon recovery reports and chase-car positions back to SondeHub, in the formats SondeHub's own tracker and ChaseMapper use.

SondeHub API (https://github.com/projecthorus/sondehub-infra/wiki/API-(Beta), data licensed CC BY-SA 2.0; recorded in DATA_SOURCES_TERMS.md):
  PUT /recovered   {serial, lat, lon, alt (metres), recovered, planned, recovered_by, description, recovery_software}, headers Date (RFC 7231) and User-Agent; no authentication;
                   the server adds the datetime; 200 = logged; retry only on 50x, up to 5 times.
  PUT /listeners   {software_name, software_version, uploader_callsign, uploader_position [lat, lon, alt], uploader_radio, uploader_antenna, mobile: true}: a chase car on the tracker map.
Rules we keep: only signed-in members, one human action per report, a per-member limit (Settings > Radiosondes), an identifying User-Agent, one request per report, never polled; a member's
live position is shared only while they have switched sharing on, only under their own profile call sign, and the member is told it becomes public on SondeHub.
Mode (Settings): on = send, test = do everything except send (to try it out), off = not offered."""
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import formatdate

from . import sondes as sd
from .db import get_db
from .settings import cfg, cfg_int

CALL_RE = re.compile(r"[A-Z0-9]{3,10}")


def _version():
    try:
        from . import build_info
        return (build_info.read() or {}).get("version") or "dev"
    except Exception:
        return "dev"


def mode(key):
    v = cfg(key)
    return v if v in ("1", "test") else "0"


def put_json(path, payload, timeout=15):
    """One PUT to SondeHub, retried only on a 50x answer (up to 3 times here, the API reference allows 5). Returns (ok, message)."""
    url = cfg("sonde_api_url").rstrip("/") + path
    body = json.dumps(payload).encode()
    ua = f"ArkSignal/{_version()} ({cfg('lookup_user_agent')})"
    last = ""
    for attempt in range(3):
        req = urllib.request.Request(url, data=body, method="PUT", headers={"Content-Type": "application/json", "Date": formatdate(usegmt=True), "User-Agent": ua})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return (r.status == 200), f"SondeHub answered {r.status}"
        except urllib.error.HTTPError as e:
            last = f"SondeHub answered {e.code}"
            if e.code < 500:
                return False, last
        except Exception as e:
            last = f"{type(e).__name__}: {e}"[:160]
        time.sleep(1.5 * (attempt + 1))
    return False, last


# ------------------------------------------------------------------ recovery state

def state_of(recovered, planned):
    """What the latest report says: recovered, planned (it is going to be recovered later) or notfound (someone looked and did not find it)."""
    if recovered:
        return "recovered"
    return "planned" if planned else "notfound"


def state_for(serial):
    r = get_db().execute("SELECT recovered, planned FROM sonde_recoveries WHERE serial=%s ORDER BY ts DESC LIMIT 1", (serial,)).fetchone()
    return state_of(r["recovered"], r["planned"]) if r else None


STATE_LABEL = {"recovered": "Recovered", "planned": "Recovery planned", "notfound": "Looked for, not found"}


# ------------------------------------------------------------------ the member's recovery report

def default_name(user):
    return (getattr(user, "callsign", "") or getattr(user, "display_name", "") or getattr(user, "username", "") or "").strip()[:60]


def report_recovery(user, serial, recovered, planned, by, note, lat, lon, alt):
    """Validate, send to SondeHub (unless in test mode) and keep a local copy. Returns (ok, message)."""
    m = mode("sonde_report_mode")
    if m == "0":
        return False, "Recovery reports are switched off on this site."
    f = sd.get(serial or "")
    if f is None:
        return False, "That balloon is not one this site knows. Report it on sondehub.org instead."
    by = (by or "").strip()[:60] or default_name(user)
    if not by:
        return False, "Enter who recovered it."
    note = (note or "").strip()[:600]
    for name, v, lo, hi in (("Latitude", lat, -90, 90), ("Longitude", lon, -180, 180)):
        if v is None or not (lo <= v <= hi):
            return False, f"{name} must be a number from {lo} to {hi}."
    if alt is None:
        alt = f["landing_alt"] if f["landing_alt"] is not None else (f["last_alt"] or 0.0)
    if not (-500 <= alt <= 50000):
        return False, "Altitude is in meters, from -500 to 50000."
    recovered, planned = bool(recovered), bool(planned) and not recovered
    d = get_db()
    n = d.execute("SELECT COUNT(*) AS n FROM sonde_recoveries WHERE user_id=%s AND source='portal' AND ts > now() - interval '1 hour'", (user.id,)).fetchone()["n"]
    if n >= cfg_int("sonde_report_per_hour"):
        return False, "You have sent a lot of reports this hour. Try again later."
    if d.execute("SELECT 1 FROM sonde_recoveries WHERE user_id=%s AND serial=%s AND source='portal' AND ts > now() - interval '60 seconds'", (user.id, f["serial"])).fetchone():
        return False, "That report was just sent."
    payload = {"serial": f["serial"], "lat": round(lat, 6), "lon": round(lon, 6), "alt": round(float(alt), 1), "recovered": recovered, "planned": planned, "recovered_by": by,
               "description": note, "recovery_software": f"ArkSignal {_version()}"}
    sent = "test"
    if m == "1":
        ok, msg = put_json("/recovered", payload)
        if not ok:
            return False, "SondeHub did not take the report (" + msg + "). Nothing was saved; try again in a minute."
        sent = "sent"
    d.execute("INSERT INTO sonde_recoveries (serial, ts, recovered, planned, recovered_by, description, lat, lon, alt, software, source, user_id, sent) VALUES (%s, now(), %s,%s,%s,%s,%s,%s,%s,%s,'portal',%s,%s)",
              (f["serial"], recovered, planned, by, note, payload["lat"], payload["lon"], payload["alt"], payload["recovery_software"], user.id, sent))
    d.commit()
    where = "SondeHub" if sent == "sent" else "this site only (test mode: nothing was sent to SondeHub)"
    return True, f"Report sent to {where}: {STATE_LABEL[state_of(recovered, planned)].lower()}."


# ------------------------------------------------------------------ chase mode

def chase_name(user):
    c = (getattr(user, "callsign", "") or "").upper()
    b = sd.base_call(c)
    return (b + "_chase") if CALL_RE.fullmatch(b or "") else ""


def share_position(user, lat, lon, alt):
    """The member's car on SondeHub's map (a mobile listener named CALLSIGN_chase). Only while they have sharing switched on in their browser; at most one update per
    `sonde_chase_min_seconds` and for `sonde_chase_max_hours` after sharing started. Returns (ok, message)."""
    m = mode("sonde_chase_mode")
    if m == "0":
        return False, "Chase sharing is switched off on this site."
    name = chase_name(user)
    if not name:
        return False, "Add your amateur radio call sign to your profile first: the car is shown as YOURCALL_chase."
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return False, "No usable position."
    d = get_db()
    row = d.execute("SELECT started, last_ts FROM sonde_chase_share WHERE user_id=%s", (user.id,)).fetchone()
    now = datetime.now(timezone.utc)
    if row and (now - row["last_ts"]).total_seconds() < max(5, cfg_int("sonde_chase_min_seconds")):
        return True, "Too soon; the last position is still current."
    if row and (now - row["last_ts"]).total_seconds() < 1800:
        started = row["started"]
        if (now - started).total_seconds() > cfg_int("sonde_chase_max_hours") * 3600:
            return False, "Sharing stopped after the longest time allowed. Switch it off and on again to continue."
    else:
        started = now
    if m == "1":
        ok, msg = put_json("/listeners", {"software_name": "ArkSignal", "software_version": _version(), "uploader_callsign": name, "uploader_position": [round(lat, 6), round(lon, 6), round(float(alt or 0), 1)],
                                          "uploader_radio": "", "uploader_antenna": "", "mobile": True})
        if not ok:
            return False, msg
    d.execute("INSERT INTO sonde_chase_share (user_id, started, last_ts, sent) VALUES (%s,%s,now(),1) ON CONFLICT (user_id) DO UPDATE SET started=%s, last_ts=now(), sent=sonde_chase_share.sent + 1", (user.id, started, started))
    d.commit()
    return True, f"Position shared as {name}" + ("" if m == "1" else " (test mode: not sent)")


def chase_cars():
    """Chase cars heard on SondeHub's feed lately (they are public there on purpose), for signed-in members only."""
    rows = get_db().execute("SELECT callsign, lat, lon, last_seen FROM sonde_stations WHERE mobile AND lat IS NOT NULL AND NOT disabled AND last_seen > now() - make_interval(mins => %s) ORDER BY callsign",
                            (cfg_int("sonde_chase_cars_minutes"),)).fetchall()
    now = datetime.now(timezone.utc)
    return [{"id": r["callsign"], "lat": r["lat"], "lon": r["lon"], "age": int((now - r["last_seen"]).total_seconds())} for r in rows]
