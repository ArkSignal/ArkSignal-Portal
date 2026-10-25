"""Service status: is everything working? A plain-words page for everyone (/status) and an in-depth one for administrators (/admin/status).

Every check returns {key, name, state, summary, detail}: state is ok, warn, down or off; `summary` is safe for anyone, `detail` (administrators only)
says why and what to look at. Nothing here ever raises: a check that cannot run reports itself as a problem. Results are cached for a few seconds
so a busy status page never adds load to the hub.
"""
import json
import os
import shutil
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from flask import Blueprint, jsonify, render_template

from .db import get_db
from .admin import permission_required
from .settings import cfg, cfg_int, get_setting

bp = Blueprint("status", __name__)

PUBLIC_KEYS = {"site", "hub", "feeders", "history", "mlat", "uat", "stats", "photon", "planefence", "weather", "sondes", "predict", "aprs", "acars", "mesh", "satellites", "ais", "drones", "osrm"}
ORDER = {"ok": 0, "off": 0, "warn": 1, "down": 2}
WORDS = {"ok": "Working", "warn": "Degraded", "down": "Not working", "off": "Not in use"}
_cache = {}


def _c(key, name, state, summary, detail="", group="services"):
    return {"key": key, "name": name, "state": state, "word": WORDS[state], "summary": summary, "detail": detail, "group": group}


def _age(iso):
    try:
        t = datetime.fromisoformat(iso)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds()
    except (TypeError, ValueError):
        return None


def _ago(sec):
    if sec is None:
        return "never"
    sec = int(sec)
    return f"{sec} s ago" if sec < 90 else f"{sec // 60} min ago" if sec < 5400 else f"{sec // 3600} h ago" if sec < 172800 else f"{sec // 86400} days ago"


def _hub_check():
    from . import live
    stale = cfg_int("status_stale_seconds")
    data, err = live.hub_aircraft()
    if data is None:
        return _c("hub", "Aircraft Data", "down", "Live aircraft are not available right now.", f"The portal could not read the hub's aircraft list ({err}).")
    t = data.get("totals", {})
    age = time.time() - float(data.get("now") or time.time())
    if age > stale:
        return _c("hub", "Aircraft Data", "warn", "Aircraft data is arriving late.", f"The hub's aircraft list is {int(age)} s old (limit {stale} s).")
    return _c("hub", "Aircraft Data", "ok", f"Tracking {t.get('aircraft', 0)} aircraft right now.",
              f"{t.get('with_position', 0)} with a position, {t.get('messages_per_second', 0)} messages/s, list is {int(max(age, 0))} s old.")


def _feeders_check():
    d = get_db()
    row = d.execute("SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE online) AS online FROM feeders WHERE NOT banned").fetchone()
    ok_age = _age(get_setting("collector_last_ok"))
    err = get_setting("collector_last_error")
    stale = cfg_int("status_stale_seconds")
    if ok_age is None or ok_age > stale:
        return _c("feeders", "Station Tracking", "warn", "Station status may be out of date.",
                  f"The last successful poll of the hub's client list was {_ago(ok_age)} (limit {stale} s). Last error: {err or 'none recorded'}.")
    return _c("feeders", "Station Tracking", "ok", f"{row['online']} of {row['total']} stations online.", f"Hub client list polled {_ago(ok_age)}.")


def _mlat_check():
    from . import mlat
    if cfg("mlat_enabled") != "1":
        return _c("mlat", "MLAT", "off", "MLAT (position calculation for aircraft that do not broadcast one) is not switched on.", "Settings > Feeders > MLAT Available is off.")
    rows = mlat.read_clients(5)
    if rows is None:
        return _c("mlat", "MLAT", "down", "MLAT is not working right now.", "The MLAT server's status folder is missing or unreadable; check the adsb-mlat container (Admin > MLAT).")
    if any(r["stale"] for r in rows) or not os.path.exists(os.path.join(os.environ.get("MLAT_WORK_DIR", "/mlat"), "clients.json")):
        return _c("mlat", "MLAT", "warn", "MLAT is slow to report.", "The MLAT server has not refreshed its status file recently.")
    working = len(mlat.working_uuids())
    bad = sum(1 for r in rows if r["bad_sync_timeout"])
    state = "ok" if rows else "warn"
    summary = f"{len(rows)} station{'s' if len(rows) != 1 else ''} connected, {working} working together." if rows else "No station has joined MLAT yet."
    return _c("mlat", "MLAT", state, summary, f"{len(rows)} clients, {working} with peers, {bad} timed out for bad sync.")


def _uat_check():
    from . import live
    if cfg("uat_enabled") != "1":
        return _c("uat", "UAT (978 MHz)", "off", "UAT (the 978 MHz general-aviation datalink) setup is not shown on the feed page.", "Settings > Feeders > UAT Setup Shown is off.")
    data, err = live.hub_aircraft()
    if data is None:
        return _c("uat", "UAT (978 MHz)", "down", "UAT data is not available right now.", f"The hub could not be read ({err}).")
    n = sum(1 for a in data["aircraft"] if str(a.get("src") or "").startswith(("uat", "adsr")))
    return _c("uat", "UAT (978 MHz)", "ok", f"{n} aircraft arriving by UAT or ADS-R right now.", "UAT aircraft reach the hub through stations' normal feed and are reported by readsb as ADS-R; the count is aircraft whose source is ADS-R or UAT.")


def _weather_check():
    if cfg("wx_enabled") != "1":
        return _c("weather", "Weather Stations", "off", "Weather stations are not switched on.", "Settings > Weather Stations > Weather Stations is off.")
    from . import weather
    n = weather.counts()
    if not n["total"]:
        return _c("weather", "Weather Stations", "off", "No weather stations have been added yet.", "Members add them under My Weather Stations.")
    if not n["online"]:
        return _c("weather", "Weather Stations", "warn", f"None of the {n['total']} weather station{'s' if n['total'] != 1 else ''} has reported lately.", f"No station has sent a reading in the last {cfg_int('wx_stale_minutes')} minutes.")
    bad = get_db().execute("SELECT COUNT(*) AS n FROM wx_stations WHERE fail_count >= 3 AND NOT disabled").fetchone()["n"]
    return _c("weather", "Weather Stations", "warn" if bad else "ok", (f"{bad} station account{'s' if bad != 1 else ''} could not be read." if bad else f"{n['online']} of {n['total']} stations are reporting."),
              f"{n['online']} of {n['total']} stations reported within {cfg_int('wx_stale_minutes')} minutes; {n['public']} shown publicly." + (f" {bad} of the stations read from owners' accounts keep failing (see Admin > Weather Stations)." if bad else ""))


def _sondes_check():
    if cfg("sonde_enabled") != "1":
        return _c("sondes", "Radiosondes", "off", "Radiosondes are not switched on.", "Settings > Radiosondes > Radiosondes and Balloons is off.")
    from . import sondes
    if cfg("sonde_live_enabled") != "1":
        return _c("sondes", "Radiosondes", "off", "The live feed is switched off.", "Settings > Radiosondes > Read the Live Feed is off; stored flights are still shown.")
    st = sondes.feed_stats()
    n = sondes.counts()["flights"]
    if not sondes.feed_alive():
        why = st.get("error") or "no report from the feed yet (it starts about a minute after the portal does)"
        return _c("sondes", "Radiosondes", "warn", "The live feed is not connected.", f"SondeHub's live feed: {why}.")
    return _c("sondes", "Radiosondes", "ok", f"{n['aloft']} balloon{'s' if n['aloft'] != 1 else ''} in the air, {n['day']} flown in the last day.",
              f"Connected to SondeHub's live feed ({st.get('per_min', 0)} messages per minute worldwide, filtered to the area in Settings); {n['total']} flights recorded, {n['nws']} from the National Weather Service.")


def _grafana_check():
    url = cfg("status_grafana_url")
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            ok = r.status == 200
        return _c("stats", "Statistics", "ok" if ok else "warn", "Statistics and graphs are working." if ok else "Statistics are responding oddly.", f"{url} answered {r.status}.")
    except Exception as e:
        return _c("stats", "Statistics", "down", "Statistics and graphs are not available right now.", f"{url} did not answer ({type(e).__name__}).")


def _site_check():
    t0 = time.time()
    try:
        get_db().execute("SELECT 1").fetchone()
        ms = (time.time() - t0) * 1000
        size = get_db().execute("SELECT pg_database_size(current_database()) AS s").fetchone()["s"]
        return _c("site", "Website and Accounts", "ok", "Sign-in, accounts and pages are working.", f"Database answered in {ms:.0f} ms, size {size / 1048576:.0f} MB.")
    except Exception as e:
        return _c("site", "Website and Accounts", "down", "The database is not answering.", f"{type(e).__name__}: {e}")


def _history_check():
    row = get_db().execute("SELECT max(last_seen) AS t, count(*) AS n FROM flight_log").fetchone()
    age = (datetime.now(timezone.utc) - row["t"]).total_seconds() if row["t"] else None
    if age is None:
        return _c("history", "Flight History", "warn", "No flights have been recorded yet.", "flight_log is empty.")
    if age > 1800:
        return _c("history", "Flight History", "warn", "Flight history may have stopped recording.", f"The newest flight record was {_ago(age)} (hub reachable but quiet overnight is normal).")
    return _c("history", "Flight History", "ok", "Flights are being recorded.", f"{row['n']:,} flights on record; newest {_ago(age)}.")


# ---- administrator-only checks

def _stack_check():
    base, token = os.environ.get("STACK_URL"), os.environ.get("STACK_TOKEN")
    if not (base and token):
        return _c("stack", "Stack Updater", "off", "", "Not set up: the portal has no STACK_URL/STACK_TOKEN. Run the one-time switch (PRODUCTION_DEPLOY.md section 0).")
    try:
        req = urllib.request.Request(base.rstrip("/") + "/status", headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(req, timeout=4) as r:
            st = json.load(r)
    except Exception as e:
        return _c("stack", "Stack Updater", "down", "", f"adsb-stack did not answer ({type(e).__name__}).")
    res = st.get("results", {})
    bad = [k for k, v in res.items() if not v.get("ok")]
    skipped = [k for k, v in res.items() if v.get("skipped")]
    detail = "Stacks: " + ", ".join(f"{k} {'FAILED' if k in bad else 'skipped' if k in skipped else 'ok'}" for k in res) + f". Last applied {_ago(time.time() - st['last_apply']) if st.get('last_apply') else 'never'}."
    for k in bad:   # say why, in the administrator's own page
        why = (res[k].get("error") or res[k].get("output") or "no message").strip().splitlines()[-6:]
        detail += f" {k} said: " + " | ".join(l.strip() for l in why if l.strip())[:600]
    return _c("stack", "Stack Updater", "warn" if bad else "ok", "", detail)


def _updates_check():
    from .update_check import read_state
    from . import build_info
    s = read_state()
    v = build_info.read().get("version")
    host = (os.environ.get("WATCHTOWER_URL") or "").split("//")[-1].split("/")[0]
    wt = "not set up"
    if host:
        h, _, p = host.partition(":")
        try:
            socket.create_connection((h, int(p or 80)), timeout=3).close()
            wt = "reachable"
        except OSError:
            wt = "NOT reachable"
    newer = s.get("update_available")
    state = "warn" if wt == "NOT reachable" else "ok"
    return _c("updates", "Updates", state, "", f"Running {v}; {'a newer release is available (' + str(s.get('latest_stable_version')) + ')' if newer else 'latest stable' if newer is False else 'release check not done'}. Update service (Watchtower) {wt}.")


def _backup_check():
    status = get_setting("backup_last_status") or ""
    age = _age(get_setting("backup_last_run_at"))
    if not status:
        return _c("backup", "Backups", "warn", "", "No automatic backup has run yet.")
    if status.startswith("failed"):
        return _c("backup", "Backups", "down", "", f"The last backup failed {_ago(age)}: {status}")
    return _c("backup", "Backups", "ok" if age is not None and age < 3 * 86400 else "warn", "", f"Last backup {_ago(age)}: {status}.")


def _disk_check():
    try:
        d = shutil.disk_usage(os.environ.get("BACKUP_DIR", "/backups"))
        pct = 100 * d.used / d.total
        return _c("disk", "Disk Space", "warn" if pct > 90 else "ok", "", f"Backup volume {pct:.0f}% full ({d.free / 2**30:.0f} GB free).")
    except OSError as e:
        return _c("disk", "Disk Space", "warn", "", f"Could not read it ({e}).")


def _data_updates_check():
    from . import dataupdates
    bad = dataupdates.failures()
    return _c("dataupdates", "Data Updates", "warn" if bad else "ok", "", ("Failed: " + "; ".join(f"{a} ({b[:80]})" for a, b in bad) + ".") if bad else "Every scheduled data update is working.")


def _airports_check():
    when = get_setting("airports_last_import")
    return _c("airports", "Airport Data", "ok" if when else "warn", "", f"Last imported {_ago(_age(when))}: {get_setting('airports_last_counts') or 'no data'}.")


# ---- the address search, Planefence and the metrics database

def _fetch_ms(url, timeout=4, headers=None, method="GET", agent=None):
    """(HTTP status or 0, milliseconds, error text). Any HTTP answer counts as an answer; only a failure to connect is an error."""
    t0 = time.time()
    try:
        req = urllib.request.Request(url, method=method, headers={"User-Agent": agent or cfg("lookup_user_agent"), **(headers or {})})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(2048)
            return r.status, (time.time() - t0) * 1000, ""
    except urllib.error.HTTPError as e:
        return e.code, (time.time() - t0) * 1000, ""
    except Exception as e:
        return 0, (time.time() - t0) * 1000, f"{type(e).__name__}: {str(e)[:100]}"


def _osrm_check():
    """The routing server behind the hospital drive-time rings: one short real route asked of it (Little Rock to Jacksonville)."""
    if cfg("osrm_enabled") != "1":
        return _c("osrm", "Routing (OSRM)", "off", "Drive-time rings are switched off.")
    url = cfg("osrm_url").rstrip("/") + "/route/v1/driving/-92.29,34.75;-92.11,34.87?overview=false"
    status, ms, err = _fetch_ms(url, timeout=6)
    if status == 200:
        slow = ms > cfg_int("status_slow_ms")
        return _c("osrm", "Routing (OSRM)", "warn" if slow else "ok", "Drive-time rings are slow." if slow else "Drive-time rings are working.", f"A test route answered in {ms:.0f} ms at {cfg('osrm_url')}.")
    return _c("osrm", "Routing (OSRM)", "down", "Drive-time rings are not available right now.",
              f"{cfg('osrm_url')} did not answer a route ({err or 'HTTP ' + str(status)}). Production and the hub reach the routing server at http://osrm.example.net:5000 (osrm-tunnel.example.net is only the WireGuard tunnel address for the working PC and the sandbox); rings already worked out are still shown.")


def _aprs_check():
    if cfg("aprs_enabled") != "1":
        return _c("aprs", "APRS", "off", "APRS is switched off.")
    from . import aprs
    status, ms, err = _fetch_ms(cfg("aprs_url").rstrip("/") + "/health")
    if status != 200:
        return _c("aprs", "APRS", "down", "APRS stations are not available right now.", f"{cfg('aprs_url')} did not answer ({err or 'HTTP ' + str(status)}). The aprs container (with aprs-db) may be stopped or still starting.")
    d = (aprs.stats().get("data") or {})
    feed = d.get("feed") or {}
    if not feed.get("connected"):
        return _c("aprs", "APRS", "warn", "APRS is not receiving right now.", f"The service answers but is not connected to APRS-IS ({feed.get('error') or 'reconnecting'}).")
    if not d.get("heard_hour"):
        return _c("aprs", "APRS", "warn", "APRS is connected but has heard nothing yet.", "It fills within a few minutes of starting.")
    return _c("aprs", "APRS", "ok", "APRS is working.", f"Answered in {ms:.0f} ms: {d['heard_hour']} stations heard this hour, {d.get('weather_now', 0)} weather stations reporting, {feed.get('per_min')} packets a minute.")


def _matrix_check():
    if cfg("matrix_enabled") != "1":
        return _c("matrix", "Matrix Chat", "off", "Matrix chat is switched off.")
    from . import matrix
    status, ms, err = _fetch_ms(cfg("matrix_url").rstrip("/") + "/health")
    if status != 200:
        return _c("matrix", "Matrix Chat", "down", "Matrix chat is not available right now.", f"{cfg('matrix_url')} did not answer ({err or 'HTTP ' + str(status)}). The matrix container (with matrix-db) may be stopped or still starting.")
    d = (matrix.status().get("data") or {})
    if not d.get("applied"):
        return _c("matrix", "Matrix Chat", "warn", "The Matrix service has not been given its settings.", "Open Admin > Matrix Chat and press Apply.")
    if not d.get("synapse_answering"):
        return _c("matrix", "Matrix Chat", "warn", "The Matrix server is not answering.", d.get("error") or "Synapse is starting or has stopped; it is started again by itself.")
    return _c("matrix", "Matrix Chat", "ok", "Matrix chat is working.", f"Answered in {ms:.0f} ms: Synapse {d.get('synapse_version', '')} has been running {int(d.get('uptime', 0)) // 60} minutes.")


def _mesh_check():
    if cfg("mesh_enabled") != "1":
        return _c("mesh", "Mesh Nodes", "off", "Mesh nodes are switched off.")
    from . import mesh
    status, ms, err = _fetch_ms(cfg("mesh_url").rstrip("/") + "/health")
    if status != 200:
        return _c("mesh", "Mesh Nodes", "down", "Mesh nodes are not available right now.", f"{cfg('mesh_url')} did not answer ({err or 'HTTP ' + str(status)}). The mesh container (with mesh-db and mesh-mqtt) may be stopped or still starting.")
    d = (mesh.stats().get("data") or {})
    feed = d.get("feed") or {}
    if not feed.get("connected"):
        return _c("mesh", "Mesh Nodes", "warn", "The mesh service is not connected to the broker.", f"The service answers but cannot reach the MQTT broker ({feed.get('error') or 'reconnecting'}).")
    if not d.get("nodes"):
        return _c("mesh", "Mesh Nodes", "ok", "The mesh service is ready, with no node heard yet.", f"Answered in {ms:.0f} ms. Nodes appear when they publish to the broker.")
    fwd = ""
    if cfg("mesh_forward_enabled") == "1":   # communities that chose to forward to the public Meshtastic server: how many packets went and how many its rules stopped
        try:
            n = get_db().execute("SELECT COUNT(*) AS n FROM communities WHERE feed_public").fetchone()["n"]
        except Exception:
            get_db().rollback()
            n = 0
        if n and feed.get("fwd_error"):
            return _c("mesh", "Mesh Nodes", "warn", "Forwarding to the public Meshtastic server is failing.", f"{n} communities forward there, and the last attempt failed ({feed.get('fwd_error')}). Mesh nodes themselves are working.")
        if n:
            fwd = f" Forwarding to the public Meshtastic server for {n} communit{'y' if n == 1 else 'ies'}: {feed.get('fwd_sent', 0)} packets sent, {feed.get('fwd_dropped', 0)} held back by its rules."
    return _c("mesh", "Mesh Nodes", "ok", "Mesh nodes are working.", f"Answered in {ms:.0f} ms: {d['heard_hour']} nodes heard this hour, {d['nodes']} known, {feed.get('per_min')} packets a minute." + fwd)


def _acars_check():
    if cfg("acars_enabled") != "1":
        return _c("acars", "ACARS", "off", "ACARS is switched off.")
    from . import acars
    status, ms, err = _fetch_ms(cfg("acars_url").rstrip("/") + "/health")
    if status != 200:
        return _c("acars", "ACARS", "down", "ACARS messages are not available right now.", f"{cfg('acars_url')} did not answer ({err or 'HTTP ' + str(status)}). The acars container (with acars-db) may be stopped or still starting.")
    d = (acars.stats().get("data") or {})
    stations = get_db().execute("SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE last_report > now() - make_interval(mins => %s)) AS live FROM acars_stations WHERE NOT disabled", (cfg_int("acars_stale_minutes"),)).fetchone()
    if not stations["n"]:
        return _c("acars", "ACARS", "ok", "ACARS is ready, with no station yet.", f"Answered in {ms:.0f} ms. Members add their receivers on My ACARS Stations.")
    if not stations["live"]:
        return _c("acars", "ACARS", "warn", "No ACARS station has sent a message lately.", f"{stations['n']} station(s) are set up; none has reported in the last {cfg_int('acars_stale_minutes')} minutes.")
    return _c("acars", "ACARS", "ok", "ACARS is working.", f"Answered in {ms:.0f} ms: {d.get('hour', 0)} messages this hour from {stations['live']} of {stations['n']} stations.")


def _satellites_check():
    if cfg("satimg_enabled") != "1":
        return _c("satellites", "Satellites", "off", "Satellite pictures are switched off.", "Settings > Satellites > Satellite Images from Members is off.")
    from . import satimages
    c = satimages.counts()
    goes = ""
    if cfg("goes_enabled") == "1":
        row = get_db().execute("SELECT max(fetched_at) AS t, count(*) AS n FROM goes_images").fetchone()
        goes = f" NOAA GOES pictures: {row['n']} stored" + (f", the newest checked {_ago((datetime.now(timezone.utc) - row['t']).total_seconds())} ago." if row["t"] else " (none fetched yet: they are fetched when somebody looks).")
        fr = get_db().execute("SELECT count(*) AS n FROM goes_frames").fetchone()["n"]
        goes += f" {fr} timed picture{'s' if fr != 1 else ''} held for animation loops."
    if c["receivers"] == 0:
        return _c("satellites", "Satellites", "ok", "Satellite pictures are ready, with no receiver yet.", "Members add their receivers on My Account > My Satellite Receivers." + goes)
    if c["active"] == 0:
        return _c("satellites", "Satellites", "warn", "No satellite receiver has sent a picture lately.", f"{c['receivers']} receiver(s) are set up; none has sent a picture in the last 48 hours." + goes)
    return _c("satellites", "Satellites", "ok", f"{c['pictures']} picture{'s' if c['pictures'] != 1 else ''} kept, {c['active']} receiver{'s' if c['active'] != 1 else ''} sending.", f"{c['day']} received in the last 24 hours, {c['bytes'] / 1048576:,.0f} MB kept." + goes)


def _bridge_check():
    if cfg("bridge_aprs_enabled") != "1" and cfg("bridge_mesh_enabled") != "1":
        return _c("bridge", "Radio Bridges", "off", "The radio bridges are switched off.", "Settings > Radio Bridges.")
    from . import bridge_verify as bv
    c = bv.counts()
    err = get_setting("bridge_verify_last_error", "")
    ok = get_setting("bridge_verify_last_ok", "")
    detail = f"{c['verified']} verified, {c['review']} waiting for an administrator's review, {c['lapsed']} lapsed, {c['revoked']} revoked."
    if err and (not ok or err.split("|")[0] > ok):
        return _c("bridge", "Radio Bridges", "warn", "The amateur license lookup is not answering.", detail + " The last attempt failed (" + err.split("|", 1)[-1] + "); members keep their last status until it answers.")
    if c["review"]:
        return _c("bridge", "Radio Bridges", "warn", f"{c['review']} member{'s are' if c['review'] != 1 else ' is'} waiting for a license review.", detail + " Admin > Radio Bridges.")
    return _c("bridge", "Radio Bridges", "ok", "The radio bridges are working.", detail)


def _ais_check():
    if cfg("ais_enabled") != "1":
        return _c("ais", "River Traffic", "off", "River Traffic is switched off.", "Settings > River Traffic > River Traffic (AIS) is off.")
    from . import ais
    c = ais.counts()
    if c["stations"] == 0:
        return _c("ais", "River Traffic", "ok", "River Traffic is ready, with no station yet.", "Members add their AIS receivers on My Account > My AIS Stations.")
    if c["active"] == 0:
        return _c("ais", "River Traffic", "warn", "No AIS station has reported lately.", f"{c['stations']} station(s) are set up; none has sent anything in the last 24 hours.")
    return _c("ais", "River Traffic", "ok", f"{c['now']} vessel{'s' if c['now'] != 1 else ''} heard lately, {c['active']} station{'s' if c['active'] != 1 else ''} reporting.", f"{c['day']} vessels in the last 24 hours, {c['total']} known.")


def _drones_check():
    if cfg("rid_enabled") != "1":
        return _c("drones", "Drones", "off", "Drone Remote ID is switched off.", "Settings > Drones > Drone Remote ID is off.")
    from . import drones
    c = drones.counts()
    if c["stations"] == 0:
        return _c("drones", "Drones", "ok", "Drones are ready, with no station yet.", "Members add their Remote ID receivers on My Account > My Remote ID Stations.")
    if c["active"] == 0:
        return _c("drones", "Drones", "warn", "No Remote ID station has reported lately.", f"{c['stations']} station(s) are set up; none has sent anything in the last 24 hours.")
    return _c("drones", "Drones", "ok", f"{c['now']} drone{'s' if c['now'] != 1 else ''} heard lately, {c['active']} station{'s' if c['active'] != 1 else ''} reporting.", f"{c['day']} drones in the last 24 hours, {c['total']} known.")


def _predict_check():
    if cfg("predict_enabled") != "1":
        return _c("predict", "Balloon Forecasts", "off", "Balloon landing forecasts are switched off.")
    from . import sonde_predict as sp
    url = cfg("predict_url").rstrip("/") + "/health"
    status, ms, err = _fetch_ms(url)
    if status != 200:
        return _c("predict", "Balloon Forecasts", "down", "Balloon landing forecasts are not available right now.",
                  f"{cfg('predict_url')} did not answer ({err or 'HTTP ' + str(status)}). The prediction service container (predict, with its database predict-db) may be stopped or still starting.")
    st = sp.stats()
    d = st.get("data") or {}
    imp = d.get("import") or {}
    if not d.get("flights"):
        return _c("predict", "Balloon Forecasts", "warn", "Balloon landing forecasts are waiting for history.", "The service is running but holds no flights yet; start the archive import on Admin > Radiosondes.")
    state = "ok"
    note = f"{d['flights']:,} flights from {d.get('sites', 0)} launch sites" + (f"; importing {imp.get('current')}" if imp.get("running") else "") + (f". Last import problem: {imp['error']}" if imp.get("error") and imp.get("error") != "stopped" else ".")
    return _c("predict", "Balloon Forecasts", state, "Balloon landing forecasts are working.", f"Answered in {ms:.0f} ms at {cfg('predict_url')}: {note}")


def _photon_check():
    url = cfg("photon_url").rstrip("/") + "/api?q=Little+Rock&limit=1"
    status, ms, err = _fetch_ms(url)
    if status == 200:
        slow = ms > cfg_int("status_slow_ms")
        return _c("photon", "Address Search (Photon)", "warn" if slow else "ok", "Address search is slow." if slow else "Address search is working.", f"Answered in {ms:.0f} ms at {cfg('photon_url')}.")
    return _c("photon", "Address Search (Photon)", "down", "Address search is not available right now.",
              f"{cfg('photon_url')} did not answer ({err or 'HTTP ' + str(status)}). On a new setup the index (about 10 GB) is still downloading; the container logs say how far it is.")


def _planefence_check():
    from . import planefence
    status, ms, err = _fetch_ms(planefence._base("public") + "/")
    if 200 <= status < 400:
        return _c("planefence", "Planefence", "ok", "Planefence and Plane-Alert are working.", f"Answered in {ms:.0f} ms.")
    return _c("planefence", "Planefence", "down", "Planefence is not available right now.", f"{planefence._base('public')} did not answer ({err or 'HTTP ' + str(status)}).")


def _prometheus_check():
    url = cfg("status_prometheus_url")
    if not url:
        return None
    status, ms, err = _fetch_ms(url)
    if status == 200:
        return _c("prometheus", "Metrics Database", "ok", "", f"Prometheus answered in {ms:.0f} ms.")
    return _c("prometheus", "Metrics Database", "down", "", f"{url} did not answer ({err or 'HTTP ' + str(status)}); graphs have no new data.")


# ---- outside data sources: is each one reachable, and how fresh is our own copy of it?

def _tile(url):
    return url.replace("{z}", "0").replace("{x}", "0").replace("{y}", "0").replace("{s}", "a")


def _sources():
    """[(key, name, what it supplies, address to ask, job whose last refresh to show)]. Addresses come from Settings, never from code."""
    from .hospitals import CATALOG
    s = [
        ("src_airports", "OurAirports", "airports, runways and radio frequencies", cfg("airports_source_url"), "airports"),
        ("src_places", "US Census Gazetteer", "cities, ZIP areas and counties for place search", cfg("places_base_url"), "places"),
        ("src_census", "US Census Locator", "where an address is (hospitals, address lookup)", cfg("census_oneline_url") + "?address=1600+Pennsylvania+Ave+NW+Washington+DC&benchmark=Public_AR_Current&format=json", ""),
        ("src_cms", "CMS Hospital List (data.cms.gov)", "the list of hospitals", CATALOG + cfg("hospitals_dataset_id"), "hospitals"),
        ("src_tar1090db", "tar1090 Aircraft Database (GitHub)", "aircraft types, owners and air ambulances", cfg("hems_source_url"), "hems"),
        ("src_planealert", "Plane-Alert Database (GitHub)", "interesting aircraft", cfg("plane_alert_url"), "interesting"),
        ("src_metar", "Aviation Weather (aviationweather.gov)", "airport weather reports", cfg("metar_api_url") + "?ids=KLIT&format=json", ""),
        ("src_elevation", "Open-Meteo Elevation", "ground height for Sky View", cfg("elevation_api_url") + "?latitude=34.7&longitude=-92.3", ""),
        ("src_photos", "Planespotters Photos", "aircraft photos", cfg("photo_api_url"), ""),
        ("src_adsbdb", "adsbdb Aircraft and Callsign Data", "aircraft, airline and callsign details (the lists, My Flights and the flight pages)", cfg("aircraft_db_url"), ""),
        ("src_routes", "adsb.im Route Service", "flight routes", cfg("route_api_url"), ""),
        ("src_meshproto", "Meshtastic Protocol Definitions (GitHub)", "the names of mesh hardware", "https://raw.githubusercontent.com/meshtastic/protobufs/master/meshtastic/mesh.proto", ""),
        ("src_osmtiles", "OpenStreetMap Map Tiles", "the base map", _tile(cfg("map_tile_url")), ""),
        ("src_leaflet", "Map Library (cdnjs)", "the map code every map page loads", cfg("leaflet_base_url") + "/leaflet.js", ""),
        ("src_terrain", "Terrain Tiles (AWS)", "Sky View terrain", _tile(cfg("skyview_dem_url")), ""),
        ("src_imagery", "USGS Imagery", "Sky View satellite imagery", _tile(cfg("skyview_imagery_url")).replace("{z}/{y}/{x}", "0/0/0"), ""),
    ]
    from . import schedules
    if schedules.configured():   # asked only when an administrator has switched it on with a key
        s.append(("src_schedules", "AeroDataBox Airline Schedules", "scheduled times of a flight number (My Flights, Look It Up)", schedules.base_url(), ""))
    try:   # the outside weather services are asked only when a member's station is read through them
        kinds = {r["kind"] for r in get_db().execute("SELECT DISTINCT kind FROM wx_stations WHERE NOT disabled").fetchall()}
        if "ecowitt_cloud" in kinds and cfg("wx_ecowitt_pull_enabled") == "1":
            s.append(("src_ecowitt", "Ecowitt.net", "weather stations read from members' own Ecowitt accounts", cfg("wx_ecowitt_api_url"), ""))
        if "tempest_cloud" in kinds and cfg("wx_tempest_pull_enabled") == "1":
            s.append(("src_tempest", "Tempest (WeatherFlow)", "weather stations read from members' own Tempest accounts", cfg("wx_tempest_api_url"), ""))
    except Exception:
        get_db().rollback()
    out = [x for x in s if x[3] and x[3].startswith("http")]
    # the map layers an administrator switched on: one probe per provider host
    seen = {u.split("/")[2] for _k, _n, _w, u, _j in out if u.count("/") >= 2}
    try:
        for r in get_db().execute("SELECT name, url FROM map_layers WHERE enabled AND kind = 'tile' ORDER BY sort_order, id").fetchall():
            host = r["url"].split("/")[2] if r["url"].count("/") >= 2 else ""
            if host and host not in seen:
                seen.add(host)
                out.append((f"layer_{host}", f"Map Layers from {host}", f"map layers such as {r['name']}", _tile(r["url"]).replace("{q}", "0"), ""))
    except Exception:
        get_db().rollback()
    return out


_probe_cache = {"at": 0, "sig": None, "rows": {}}


def _probe_all(sources):
    """Ask every source (in parallel, a few seconds at most) and keep the answers for the minutes set in Settings: a busy status page never hammers anyone's server."""
    from concurrent.futures import ThreadPoolExecutor
    ttl = max(1, cfg_int("status_probe_minutes")) * 60
    sig = tuple((s[0], s[3]) for s in sources)   # a changed address is asked again at once
    if time.time() - _probe_cache["at"] < ttl and _probe_cache["sig"] == sig:
        return _probe_cache["rows"]

    agent = cfg("lookup_user_agent")   # read here: the worker threads have no application context

    def one(s):
        status, ms, err = _fetch_ms(s[3], timeout=5, method="HEAD", agent=agent)
        if status in (403, 405, 501):   # some servers refuse HEAD
            status, ms, err = _fetch_ms(s[3], timeout=5, agent=agent)
        return s[0], (status, ms, err)
    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = dict(ex.map(one, sources))
    _probe_cache.update(at=time.time(), sig=sig, rows=rows)
    return rows


def _source_checks():
    sources = _sources()
    rows = _probe_all(sources)
    slow = cfg_int("status_slow_ms")
    out = []
    for key, name, what, url, job in sources:
        status, ms, err = rows.get(key, (0, 0, "not checked"))
        fresh = ""
        if job:
            when = get_setting(f"{job}_last_import")
            fresh = f" Our copy was last refreshed {_ago(_age(when))}." if when else " Our copy has not been loaded yet."
        host = url.split("/")[2] if url.count("/") >= 2 else url
        if status == 0:
            summary = (f"Not answering right now; we keep using the copy we already have for {what}." if job else f"Not answering right now, so {what} is unavailable.")
            out.append(_c(key, name, "down", summary, f"{host} did not answer ({err}).{fresh}", "sources"))
        elif status >= 500:
            out.append(_c(key, name, "warn", f"Having problems on its side (HTTP {status}).{fresh}", f"{url} answered HTTP {status} in {ms:.0f} ms.{fresh}", "sources"))
        else:
            state = "warn" if ms > slow else "ok"
            out.append(_c(key, name, state, ("Reachable but slow." if state == "warn" else "Reachable.") + fresh, f"{host} answered in {ms:.0f} ms (HTTP {status}); supplies {what}.{fresh}", "sources"))
    out.append(_c("src_osm", "OpenStreetMap Place Finder (Nominatim)", "off", "Used only when an administrator asks about one hospital address.", "Not checked on purpose: its usage policy forbids automatic requests.", "sources"))
    return out


PUBLIC = (_site_check, _hub_check, _feeders_check, _history_check, _mlat_check, _uat_check, _weather_check, _sondes_check, _predict_check, _aprs_check, _acars_check, _mesh_check, _matrix_check, _satellites_check, _bridge_check, _ais_check, _drones_check, _osrm_check, _photon_check, _planefence_check, _grafana_check, _source_checks)
ADMIN = (_prometheus_check, _updates_check, _stack_check, _backup_check, _disk_check, _airports_check, _data_updates_check)


def sources_summary():
    """Counts from the last time the outside sources were asked (None when they have not been asked yet): the overview never triggers the slow part itself."""
    rows = _probe_cache["rows"]
    if not rows:
        return None
    slow = cfg_int("status_slow_ms")
    down = sum(1 for s, _ms, _e in rows.values() if s == 0 or s >= 500)
    warn = sum(1 for s, ms, _e in rows.values() if 0 < s < 500 and ms > slow)
    return {"down": down, "warn": warn, "total": len(rows)}


_snap = {"at": 0, "checks": None, "read": 0}   # this worker's copy of the last snapshot (re-read from the database every few seconds)


def _execute():
    """Runs every check once (the administrator's full set) and returns the list. Only the background collector and an administrator's Check Now call this."""
    out = []
    for fn in PUBLIC + ADMIN:
        try:
            r = fn()
        except Exception as e:  # a broken check must not break the page
            r = _c(fn.__name__.strip("_").replace("_check", ""), fn.__name__.strip("_").replace("_check", "").title(), "warn", "This check could not run.", f"{type(e).__name__}: {e}")
        if isinstance(r, list):
            out.extend(r)
        elif r:
            out.append(r)
    return out


def _snap_path():
    import tempfile
    return os.path.join(tempfile.gettempdir(), "arksignal_status.json")


def store(out):
    """Keeps the result where every web worker of this container can read it: a small file replaced in one step (not the database: a backup restore must never wait for, or deadlock with, the checks)."""
    at = time.time()
    path = _snap_path()
    try:
        tmp = path + "." + str(os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"at": at, "checks": out}, f)
        os.replace(tmp, path)
    except OSError:
        pass
    _snap.update(at=at, checks=out, read=time.time())


def snapshot():
    """(checks, when) from the last background run, or (None, 0) when none has run yet. Never runs a check."""
    if time.time() - _snap["read"] > 3:
        try:
            with open(_snap_path(), encoding="utf-8") as f:
                d = json.load(f)
            _snap.update(at=d["at"], checks=d["checks"])
        except Exception:
            pass
        _snap["read"] = time.time()
    return _snap["checks"], _snap["at"]


def check_now():
    """An administrator forces a fresh run: the checks run now and the snapshot is replaced."""
    out = _execute()
    store(out)
    return out


def shape(out, admin=False, sources=True, at=None):
    pub = out if admin else [c for c in out if c["key"] in PUBLIC_KEYS or c["group"] == "sources"]
    if not sources:
        out = [c for c in out if c["group"] != "sources"]; pub = [c for c in pub if c["group"] != "sources"]
    services = [c for c in pub if c["group"] == "services"]
    srcs = [c for c in pub if c["group"] == "sources"]
    worst = max((ORDER[c["state"]] for c in services), default=0)
    worst_src = min(1, max((ORDER[c["state"]] for c in srcs), default=0))   # an outside source can degrade the picture but never makes our own site "not working"
    overall = {0: ("ok", "All systems are working."), 1: ("warn", "Some services are degraded."), 2: ("down", "Some services are not working.")}[worst]
    if worst == 0 and worst_src:
        overall = ("warn", "The site is working. Some outside data sources are not answering; we keep using the data we already have.")
    return {"overall": overall[0], "message": overall[1], "checks": out, "at": at or time.time()}


def run(admin=False, max_age=10, sources=True):
    """The current picture from the last background run. Only when no run has happened yet (first minutes after a start) are the checks run here, once."""
    out, at = snapshot()
    if out is None:
        out = _execute(); store(out); at = time.time()
    return shape(out, admin, sources, at)


def start_collector(app):
    """The checks run in the background on a schedule (Settings > Service Status > Check Every), one web worker at a time; pages only ever read the result."""
    import threading
    import psycopg

    def loop():
        time.sleep(6)
        last = 0.0
        while True:
            with app.app_context():
                wait = max(15, cfg_int("status_check_seconds"))   # read again every few seconds, so a changed setting takes effect at once
            if time.time() - last >= wait:
                last = time.time()
                try:
                    with psycopg.connect(app.config["DATABASE_URL"], autocommit=True) as lock:
                        if lock.execute("SELECT pg_try_advisory_lock(727012)").fetchone()[0]:
                            try:
                                with app.app_context():
                                    store(_execute())
                            finally:
                                lock.execute("SELECT pg_advisory_unlock(727012)")
                except Exception:
                    pass
            time.sleep(5)
    threading.Thread(target=loop, name="status-checks", daemon=True).start()


def _page_data(admin):
    out, at = snapshot()
    if out is None:
        return None
    s = shape(out, admin, True, at)
    return {"overall": s["overall"], "message": s["message"], "at": at, "every": cfg_int("status_check_seconds"),
            "checks": [{"key": c["key"], "name": c["name"], "state": c["state"], "word": c["word"], "summary": c["summary"], "group": c["group"], **({"detail": c["detail"]} if admin else {})} for c in s["checks"] if admin or c["summary"]]}


@bp.route("/status")
def public_status():
    return render_template("status.html", admin=False)   # the page opens at once; the states are filled in from /api/status.json


@bp.route("/api/status.json")
def api_status():
    d = _page_data(False)
    if d is None:
        return jsonify({"pending": True, "message": "The first check is running..."})
    d["services"] = [{"name": c["name"], "state": c["state"], "summary": c["summary"], "group": c["group"]} for c in d["checks"]]
    resp = jsonify(d)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/admin/status")
@permission_required("manage_settings")
def admin_status():
    return render_template("status.html", admin=True, active="status")


@bp.route("/admin/status/data.json")
@permission_required("manage_settings")
def admin_status_data():
    d = _page_data(True)
    return jsonify(d if d is not None else {"pending": True, "message": "The first check is running..."})


@bp.route("/admin/status/run", methods=["POST"])
@permission_required("manage_settings")
def admin_status_run():
    """Only an administrator forces the checks to run now."""
    from .audit import audit
    check_now()
    audit("status.check_now")
    return jsonify({"ok": True, "message": "The checks were run just now."})
