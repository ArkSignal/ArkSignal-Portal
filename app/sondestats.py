"""Radiosonde statistics with filters: search by flight (serial), receiving station, launch site, source (National Weather Service, other radiosondes, amateur balloons), status and dates; what comes back is
the summary, flights per day, how high they burst, how long they flew, which stations heard them and the flights themselves. Read from the flights the portal recorded (and the history imported from SondeHub)."""
import re
from datetime import date, datetime, timedelta, timezone

from .db import get_db
from .settings import cfg

SOURCES = (("nws", "National Weather Service"), ("other", "Other radiosondes"), ("amateur", "Amateur balloons"))


def _list(v, n=40):
    return [x.strip() for x in (v or "").split("|") if x.strip()][:n]


def _where(a):
    """(sql, params) for the filters in `a`: q, flights, stations, sites, sources, status, from, to."""
    w, p = ["s.first_seen < %s", "s.last_seen >= %s"], []
    try:
        d1 = date.fromisoformat(a.get("from", ""))
    except ValueError:
        d1 = date.today() - timedelta(days=30)
    try:
        d2 = date.fromisoformat(a.get("to", ""))
    except ValueError:
        d2 = date.today()
    if d2 < d1:
        d1, d2 = d2, d1
    if (d2 - d1).days > 366:
        d1 = d2 - timedelta(days=366)
    t1 = datetime.combine(d1, datetime.min.time(), tzinfo=timezone.utc)
    t2 = datetime.combine(d2 + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    params = [t2, t1]
    q = re.sub(r"[^A-Za-z0-9 _.-]", "", a.get("q", ""))[:30]
    if q:
        w.append("(s.serial ILIKE %s OR s.type ILIKE %s OR s.site_name ILIKE %s OR s.manufacturer ILIKE %s)"); params += ["%" + q + "%"] * 4
    flights = _list(a.get("flights"))
    if flights:
        w.append("upper(s.serial) = ANY(%s)"); params.append([f.upper() for f in flights])
    stations = _list(a.get("stations"))
    if stations:
        w.append("EXISTS (SELECT 1 FROM sonde_heard h WHERE h.serial = s.serial AND upper(h.callsign) = ANY(%s))"); params.append([x.upper() for x in stations])
    sites = _list(a.get("sites"))
    if sites:
        w.append("s.site_id = ANY(%s)"); params.append(sites)
    srcs = [x for x in (a.get("sources") or "").split(",") if x in dict(SOURCES)]
    if srcs and len(srcs) < 3:
        parts = []
        if "nws" in srcs:
            parts.append("s.agency = 'NWS'")
        if "other" in srcs:
            parts.append("(s.agency = '' AND s.kind = 'radiosonde')")
        if "amateur" in srcs:
            parts.append("s.kind = 'amateur'")
        w.append("(" + " OR ".join(parts) + ")")
    st = a.get("status", "")
    if st in ("aloft", "landed", "lost"):
        w.append("s.status = %s"); params.append(st)
    return " AND ".join(w), params, d1, d2


def stats(a):
    w, p, d1, d2 = _where(a)
    d = get_db()
    summ = d.execute(f"SELECT count(*) AS n, count(*) FILTER (WHERE s.agency = 'NWS') AS nws, count(*) FILTER (WHERE s.kind = 'amateur') AS amateur, count(*) FILTER (WHERE s.status = 'landed') AS landed, "
                     f"avg(s.max_alt) AS burst, max(s.max_alt) AS burst_max, percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM s.last_seen - COALESCE(s.launch_time, s.first_seen)) / 60.0) FILTER (WHERE s.status <> 'aloft') AS minutes, "
                     f"avg((s.max_alt - COALESCE(s.launch_alt, 0)) / NULLIF(extract(epoch FROM s.max_alt_ts - s.launch_time), 0)) FILTER (WHERE s.launch_time IS NOT NULL AND s.max_alt_ts > s.launch_time) AS climb "
                     f"FROM sondes s WHERE {w}", p).fetchone()
    days = d.execute(f"SELECT date_trunc('day', COALESCE(s.launch_time, s.first_seen))::date AS day, count(*) AS n FROM sondes s WHERE {w} GROUP BY 1 ORDER BY 1", p).fetchall()
    hist = d.execute(f"SELECT least(floor(s.max_alt / 5000), 8)::int AS b, count(*) AS n FROM sondes s WHERE {w} AND s.max_alt IS NOT NULL GROUP BY 1 ORDER BY 1", p).fetchall()
    top = d.execute(f"SELECT h.callsign, count(*) AS n FROM sonde_heard h JOIN sondes s ON s.serial = h.serial WHERE {w} GROUP BY h.callsign ORDER BY n DESC, h.callsign LIMIT 12", p).fetchall()
    rows = d.execute(f"SELECT s.serial, s.type, s.kind, s.agency, s.site_id, s.site_name, s.status, s.max_alt, s.frames, COALESCE(s.launch_time, s.first_seen) AS launched, "
                     f"extract(epoch FROM s.last_seen - COALESCE(s.launch_time, s.first_seen)) / 60.0 AS minutes, (SELECT count(*) FROM sonde_heard h WHERE h.serial = s.serial) AS heard "
                     f"FROM sondes s WHERE {w} ORDER BY COALESCE(s.launch_time, s.first_seen) DESC LIMIT 500", p).fetchall()
    return {"from": d1.isoformat(), "to": d2.isoformat(),
            "summary": {"flights": summ["n"], "nws": summ["nws"], "amateur": summ["amateur"], "landed": summ["landed"], "burst_avg": summ["burst"], "burst_max": summ["burst_max"], "minutes_avg": summ["minutes"], "climb_avg": summ["climb"]},
            "per_day": [{"day": r["day"].isoformat(), "n": r["n"]} for r in days], "burst_hist": [{"from_m": r["b"] * 5000, "n": r["n"]} for r in hist], "top_stations": [{"callsign": r["callsign"], "flights": r["n"]} for r in top],
            "flights": [{"serial": r["serial"], "type": r["type"], "kind": r["kind"], "agency": r["agency"], "site_id": r["site_id"], "site": r["site_name"], "status": r["status"], "max_alt": r["max_alt"], "frames": r["frames"],
                         "launched": r["launched"].isoformat(), "minutes": r["minutes"], "heard": r["heard"]} for r in rows]}


def find(q, limit=8):
    """Flights, receiving stations and launch sites that match what was typed (for the pickers)."""
    q = re.sub(r"[^A-Za-z0-9 _.-]", "", q or "")[:30]
    d = get_db()
    return {"flights": [r["serial"] for r in d.execute("SELECT serial FROM sondes WHERE serial ILIKE %s ORDER BY last_seen DESC LIMIT %s", (q + "%", limit)).fetchall()],
            "stations": [r["callsign"] for r in d.execute("SELECT callsign FROM sonde_stations WHERE callsign ILIKE %s ORDER BY last_seen DESC NULLS LAST LIMIT %s", (q + "%", limit)).fetchall()],
            "sites": [{"id": r["site_id"], "name": r["name"]} for r in d.execute("SELECT site_id, name FROM sonde_sites WHERE site_id ILIKE %s OR name ILIKE %s ORDER BY name LIMIT %s", (q + "%", "%" + q + "%", limit)).fetchall()]}
