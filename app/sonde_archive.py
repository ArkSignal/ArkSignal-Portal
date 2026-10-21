"""Load one flight by serial from SondeHub's public archive (AWS Open Data, s3://sondehub-history/serial/<serial>.json.gz, anonymous, one GET per request).
The file is the flight's telemetry in the same messages the live feed carries, so it runs through the same Collector as live data: points thinned to our interval,
launch site, burst, heard-by. The flight is marked source='archive' so the nightly thinning never trims it. Data: SondeHub Radiosonde Telemetry, CC BY-SA 2.0
(https://registry.opendata.aws/sondehub-telemetry); terms recorded in DATA_SOURCES_TERMS.md."""
import gzip
import json
import re
import time
import urllib.error
import urllib.request

from . import sondes as sd
from .settings import cfg, cfg_int

SERIAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,39}$")


def enabled():
    return cfg("sonde_archive_enabled") == "1"


def fetch_serial(serial):
    """The archive's list of telemetry frames for one serial, or (None, reason) when it is not there (the archive trails live by a few days)."""
    url = cfg("sonde_archive_url").rstrip("/") + "/serial/" + urllib.request.quote(serial) + ".json.gz"
    req = urllib.request.Request(url, headers={"User-Agent": cfg("lookup_user_agent"), "Accept-Encoding": "identity"})
    cap = cfg_int("sonde_archive_max_mb") * 1_000_000
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read(cap + 1)
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return None, "SondeHub's archive does not have that serial. Very recent flights appear there a few days after they land, and only serials it has seen exist."
        return None, f"The archive answered with an error ({e.code}). Try again later."
    except Exception as e:
        return None, "The archive could not be reached (" + str(e)[:80] + "). Try again later."
    if len(body) > cap:
        return None, f"That flight's file is larger than the {cfg_int('sonde_archive_max_mb')} MB limit."
    try:
        data = json.loads(gzip.decompress(body).decode("utf-8"))
    except Exception:
        return None, "The archive's file for that serial could not be read."
    if not isinstance(data, list) or not data:
        return None, "The archive's file for that serial is empty."
    return data, ""


def import_packets(conn, serial, packets, keep_every=None):
    """Store a flight from archive frames through the live Collector. Returns the number of points kept."""
    col = sd.Collector()
    col.configure()
    if keep_every:
        col.keep_every = keep_every   # the history import keeps fewer points than a live flight
    col.known[serial] = time.time()   # an archived flight is wanted wherever it flew, not only inside our boxes
    saved = dict(sd.STATE)
    try:
        for p in sorted((x for x in packets if isinstance(x, dict) and str(x.get("serial") or "") == serial), key=lambda x: x.get("datetime") or ""):
            col.telemetry(p, "sondes")
    finally:
        sd.STATE.update(saved)   # the live feed's counters are not for archive frames
    with col.lock:
        col.stations = {}   # archive frames carry where stations were then, not now: keep the station list as it is
        col.backfill = type(col.backfill)()
    n = col.flush(conn)
    last = conn.execute("SELECT last_alt FROM sondes WHERE serial=%s", (serial,)).fetchone()
    if last is None:
        return 0
    low = (last["last_alt"] or 0) < float(cfg("sonde_landed_below_m"))
    conn.execute("UPDATE sondes SET source='archive', status=%s, landing_lat=CASE WHEN %s THEN last_lat END, landing_lon=CASE WHEN %s THEN last_lon END, "
                 "landing_alt=CASE WHEN %s THEN last_alt END WHERE serial=%s", ("landed" if low else "lost", low, low, low, serial))
    conn.commit()
    sd.assign_states(conn)
    return n


def load_serial(conn, serial, packets=None):
    """(ok, message, serial). A flight already stored from the live feed is left as it is."""
    serial = (serial or "").strip()
    if not SERIAL_RE.match(serial):
        return False, "A serial is letters and numbers, for example 26013531 or V3550338.", serial
    row = conn.execute("SELECT serial, source FROM sondes WHERE lower(serial)=lower(%s)", (serial,)).fetchone()
    if row is not None:
        return True, "That flight is already here.", row["serial"]
    if packets is None:
        packets, why = fetch_serial(serial)
        if packets is None:
            return False, why, serial
    n = import_packets(conn, serial, packets)
    if not n:
        return False, "The archive's file had no usable positions for that serial.", serial
    return True, f"Loaded {serial} from SondeHub's archive ({n} positions kept).", serial
