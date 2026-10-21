"""Automatic updates for every outside data set the portal keeps (airports, places, hospitals, trauma levels, air ambulances, ...), on one page.

Each source has a schedule (every N days, 0 = off; one shared time of day), a Run Now button, the last result and the next run. A background loop looks every
ten minutes and runs what is due; a database lock means two web workers never run the same source at once; a failed run is tried again no sooner than six
hours later and the source keeps its last good data. Everything is written to the audit log and a failure shows on Admin > Status.
To add a source: add a Job below, its `<key>_update_days` setting (settings.py and setting_specs.py), and nothing else."""
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from .db import get_db
from .settings import cfg, cfg_int, get_setting, set_setting


STARTED = time.time()
FIRST_RUN_AFTER = 600   # seconds; protects restores and updates in the first minutes, not a user-facing setting


class Job:
    def __init__(self, key, label, what, run, needs="", first_now=False):
        self.key, self.label, self.what, self.run, self.needs = key, label, what, run, needs
        self.first_now = first_now   # never run yet: start at the next pass (after an update) instead of waiting for the daily time


def _bridgeverify():
    from . import bridge_verify
    if cfg("bridge_aprs_enabled") != "1":
        return "The APRS bridge is switched off in Settings > Radio Bridges."
    return bridge_verify.recheck_all()


def _airports():
    from . import airports as ap
    c = ap.import_airports([x for x in cfg("airport_countries").split(",") if x.strip()])
    return f"{c['airports']} airports, {c['frequencies']} frequencies, {c['runways']} runways"


def _places():
    from . import places
    c = places.import_places()
    return f"{c['place']} cities and towns, {c['zip']} ZIP areas, {c['county']} counties"


def _counties():
    from . import counties
    c = counties.import_counties()
    return f"{c['counties']} county outlines"


def _sondesites():
    from . import sondes
    c = sondes.import_sites()
    return f"{c['sites']} launch sites, {c['region']} in the area"


def _hospitals():
    from . import hospitals
    c = hospitals.import_hospitals()
    return f"{c['added']} added, {c['updated']} refreshed, {c['removed']} removed by the type filter, {c['total']} in all"


def _repeaters():
    from . import repeaters
    if cfg("repeater_hearham_enabled") != "1":
        return "Hearham's listing is switched off in Settings > Repeaters."
    c = repeaters.import_hearham()
    out = f"{c['added']} added, {c['updated']} refreshed, {c['skipped']} left alone (verified or edited), {c['removed']} no longer listed, {c['total']} in the directory"
    if cfg("repeater_aprs_enabled") == "1" and cfg("aprs_enabled") == "1":
        from . import aprs
        try:
            s, w, n, e = [float(x) for x in cfg("repeater_map_area").split(",")]
            rows = aprs.stations(kind="repeater", hours=720, bbox=f"{s - .3},{w - .3},{n + .3},{e + .3}", limit=5000)
            a = repeaters.import_aprs(rows)
            out += f"; from APRS beacons: {a['added']} new, {a['known']} already listed, {a['unreadable']} not readable"
        except (ValueError, KeyError):
            out += "; APRS beacons could not be read"
    return out


def _hospital_rings():
    from . import driveranges
    c = driveranges.compute_all()
    return f"{c['ok']} of {c['hospitals']} emergency hospitals worked out" + (f", {c['failed']} failed" if c["failed"] else "")


def _roads():
    from . import roads
    c = roads.import_roads()
    return f"{c['routes']} routes ({c['interstates']} Interstates, {c['us']} US highways, {c['state']} state highways)"


def _hems():
    from . import hems
    c = hems.import_registry()
    return f"{c['registry']} found by owner name, {c['listed']} from the tail number list"


def _hospverify():
    from . import addrlookup
    return addrlookup.check_all()


def _trauma():
    from . import hospitals
    url = (cfg("trauma_source_url") or "").strip()
    if not url:
        return None   # nothing to do: no source address
    req = urllib.request.Request(url, headers={"User-Agent": cfg("lookup_user_agent")})
    with urllib.request.urlopen(req, timeout=60) as r:
        text = r.read(2_000_000).decode("utf-8-sig", "replace")
    r = hospitals.import_trauma(text, True)
    return f"{r['matched']} matched, {r['added']} added" + (f", {len(r['unreadable'])} skipped" if r["unreadable"] else "")


def _traumasystem():
    from . import trauma
    return trauma.run_system_job()


def _placemedia():
    from . import trauma
    return trauma.run_media_job()


def _interesting():
    from . import interesting
    c = interesting.import_planealert()
    return f"{c['added']} from Plane-Alert ({c['total']} in its list), {c['left']} left alone"


def _sonde_coverage():
    from . import sondes
    return f"{sondes.rebuild_reach_sectors()} receiving stations with a coverage outline from the flights they heard"


def _satpass():
    from . import satpass
    return f"{satpass.refresh()} satellites' orbit data"


def _dmrlists():
    from . import dmrlists
    return dmrlists.refresh()


JOBS = [
    Job("places", "Places", "Cities, ZIP areas and counties for map search (US Census Bureau)", _places),
    Job("counties", "County Outlines", "The shape of every county of the configured states (US Census Bureau TIGERweb), for the County Map and the county pages", _counties),
    Job("sondesites", "Balloon Launch Sites", "Where radiosondes are launched, from SondeHub (National Weather Service sites are recognized by their station number); used to name a flight's launch site", _sondesites),
    Job("repeaters", "Repeaters", "Amateur radio repeaters in the chosen states from Hearham's open listing (credited); records people verified or edited are never overwritten", _repeaters),
    Job("airports", "Airports", "Airports, runways and radio frequencies (OurAirports)", _airports),
    Job("hospitals", "Hospitals", "The hospital list and where each is (CMS, Census locator), with the type filter", _hospitals, "places"),
    Job("trauma", "Trauma Levels and Services", "The list of trauma-designated hospitals and their services, read from the address in Settings", _trauma, "hospitals"),
    Job("traumasystem", "Arkansas Trauma System", "Applies the Department of Health's list of designated trauma centers to the hospitals and works out which hospitals the Trauma Map shows (in the state, designated anywhere, and near the state line by road)", _traumasystem, "hospitals"),
    Job("placemedia", "Hospital and County Websites and Pictures", "The website and a picture of each county and of each hospital on the Trauma Map, from Wikidata and Wikimedia Commons (credited); what an administrator typed in is kept", _placemedia, "traumasystem"),
    Job("hospverify", "Hospital Positions Check", "Looks up every hospital's address with the Census and our own Photon, confirms the position where they agree and lists the rest for review (takes several minutes)", _hospverify, "hospitals"),
    Job("roads", "Highways", "Interstates, US highways and state highways for the map layer (US Census Bureau TIGERweb, public domain)", _roads),
    Job("hospital_rings", "Hospital Drive-Time Rings", "How far a vehicle gets in 15, 30 and 60 minutes by road around every emergency hospital, worked out with the network's routing server (our own estimate, not an ambulance's route)", _hospital_rings, "hospitals"),
    Job("dmrlists", "DMR Talkgroups and Contacts", "The talkgroup and contact lists behind the names repeaters and radio code plugs use, read from the addresses in Settings > Repeaters (blank until you have permission from the list's owner)", _dmrlists),
    Job("sondecoverage", "Radiosonde Station Coverage", "How far each receiving station heard balloons in every direction, worked out again from the flights each really heard (live reception keeps adding to it)", _sonde_coverage, first_now=True),
    Job("interesting", "Interesting Aircraft", "Interesting aircraft from the Plane-Alert database (Open Database License); your own entries are never touched", _interesting),
    Job("satpass", "Satellite Orbits", "The orbit data (CelesTrak two-line elements) of the space station, weather satellites and amateur radio satellites chosen in Settings > Satellites, for Radio > Satellite Passes", _satpass, first_now=True),
    Job("bridgeverify", "Amateur Licenses for the APRS Bridge", "Looks up the FCC amateur license of every member who has been checked for the radio bridge, so an expired or cancelled license stops their APRS sending (Callook.info, the FCC's public license data)", _bridgeverify),
    Job("hems", "Air Ambulances", "Air ambulance aircraft from the aircraft database and your tail number list", _hems),
]
BY_KEY = {j.key: j for j in JOBS}


STALE_RUNNING_HOURS = 3   # a "running" mark older than this is a leftover of a crash, not a running update


def related(key):
    """The sources that must not run together with `key`: itself, the sources it depends on (`needs`, all the way back) and the sources that depend on it."""
    out = {key}
    k = key
    while BY_KEY[k].needs:   # what it needs, and what that needs
        k = BY_KEY[k].needs
        out.add(k)
    grew = True
    while grew:   # what needs it, and what needs those
        grew = False
        for j in JOBS:
            if j.needs in out and j.key not in out:
                out.add(j.key)
                grew = True
    return out


def running_jobs():
    """Every update running now (a mark older than a few hours is a leftover of a crash and does not count): a list of Jobs. Different sources may run at the same time."""
    now = datetime.now(timezone.utc)
    out = []
    for j in JOBS:
        v = get_setting(f"{j.key}_running")
        if not v:
            continue
        try:
            t = datetime.fromisoformat(v)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if now - t < timedelta(hours=STALE_RUNNING_HOURS):
            out.append(j)
    return out


def running_job(exclude=None):
    """One running update other than `exclude`, or None (kept for callers that only want to know if anything is running)."""
    return next((j for j in running_jobs() if j.key != exclude), None)


def blocking_job(key):
    """The running update that stops `key` from starting: the same source, or one it depends on or that depends on it. None when `key` may start. Unrelated sources run side by side."""
    rel = related(key)
    return next((j for j in running_jobs() if j.key in rel), None)


def days_of(key):
    try:
        return max(0, int(cfg(f"{key}_update_days")))
    except (TypeError, ValueError):
        return 0


def last_ok(key):
    return get_setting(f"{key}_last_import") or None


def state(key, tz):
    """Everything the admin page shows for one source."""
    from . import airports as ap
    j = BY_KEY[key]
    last, attempt = last_ok(key), get_setting(f"{key}_last_attempt") or None
    days = days_of(key)
    hhmm = cfg("airports_update_time")
    if days <= 0:
        nxt = "Automatic updates are off."
    else:
        nxt = ap.next_update(last, days, hhmm, tz).replace("Next automatic update: ", "")
    return {"key": key, "label": j.label, "what": j.what, "days": days, "last": last, "last_result": get_setting(f"{key}_last_result"), "error": get_setting(f"{key}_last_error"),
            "running": any(x.key == key for x in running_jobs()), "progress": get_setting(f"{key}_progress") or "", "next": nxt, "needs": j.needs, "attempt": attempt,
            "configured": key != "trauma" or bool((cfg("trauma_source_url") or "").strip())}


def run_job(app, key):
    """Run one source now (in the calling thread). Returns (ok, message). Safe from any worker: a database lock keeps two from running it together."""
    import psycopg
    j = BY_KEY[key]
    with app.app_context():
        other = blocking_job(key)
        if other is not None:
            return False, "It is already running." if other.key == key else f"{other.label} is running and is linked to {j.label} (one uses the other's data): wait for it to finish."
        with psycopg.connect(app.config["DATABASE_URL"], autocommit=True) as lock_conn:
            if not lock_conn.execute("SELECT pg_try_advisory_lock(%s)", (727100 + JOBS.index(j),)).fetchone()[0]:
                return False, "It is already running."
            try:
                now = datetime.now(timezone.utc)
                set_setting(f"{key}_running", now.isoformat()); set_setting(f"{key}_last_attempt", now.isoformat())
                try:
                    result = j.run()
                    if result is None:
                        set_setting(f"{key}_last_result", "Nothing to do: no source address is set."); set_setting(f"{key}_last_error", "")
                        ok, msg = True, "Nothing to do: no source address is set."
                    else:
                        set_setting(f"{key}_last_import", datetime.now(timezone.utc).isoformat()); set_setting(f"{key}_last_attempt", "")
                        set_setting(f"{key}_last_result", result); set_setting(f"{key}_last_error", "")
                        ok, msg = True, result
                except Exception as e:
                    get_db().rollback()
                    msg = f"{type(e).__name__}: {e}"[:300]
                    set_setting(f"{key}_last_error", msg)
                    ok = False
                get_db().execute("INSERT INTO audit_log (actor_id, actor_name, action, target, detail) VALUES (NULL,'system','dataupdate.run',%s,%s)", (key, ("ok: " if ok else "failed: ") + msg))
                get_db().commit()
                return ok, msg
            finally:
                set_setting(f"{key}_running", "")
                lock_conn.execute("SELECT pg_advisory_unlock(%s)", (727100 + JOBS.index(j),))


def run_in_background(app, key):
    threading.Thread(target=run_job, args=(app, key), name=f"update-{key}", daemon=True).start()


def due(key, now, tz):
    from . import airports as ap
    days = days_of(key)
    if days <= 0 or (key == "trauma" and not (cfg("trauma_source_url") or "").strip()):
        return False
    job = next((j for j in JOBS if j.key == key), None)
    hhmm = "00:00" if job and job.first_now and not last_ok(key) else cfg("airports_update_time")   # a job that has never run does not wait for the daily time
    return ap.update_due(now, last_ok(key), get_setting(f"{key}_last_attempt") or None, days, hhmm, tz)


def run_due(app):
    """One pass: run every source that is due, in order (places before hospitals, hospitals before trauma levels)."""
    from zoneinfo import ZoneInfo
    ran = []
    for j in JOBS:
        with app.app_context():
            now = datetime.now(timezone.utc)
            if not due(j.key, now, ZoneInfo(cfg("timezone"))):
                continue
            if j.first_now and not last_ok(j.key) and time.time() - STARTED < FIRST_RUN_AFTER:
                continue   # a job that starts itself the first time waits until the site has been up a while (a restore or an update in its first minutes is not disturbed)
        ok, msg = run_job(app, j.key)
        ran.append((j.key, ok))
    return ran


def start_scheduler(app):
    def loop():
        time.sleep(90)
        while True:
            try:
                run_due(app)
            except Exception:
                pass
            time.sleep(600)
    threading.Thread(target=loop, name="data-updates", daemon=True).start()


def failures():
    """Sources whose last automatic try failed and has not succeeded since: (label, message) pairs for the status page."""
    out = []
    for j in JOBS:
        err = get_setting(f"{j.key}_last_error")
        if err and days_of(j.key) > 0:
            out.append((j.label, err))
    return out
