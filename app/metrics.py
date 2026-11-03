"""Prometheus metrics about the network, for the Grafana "Network Feeders" dashboard.

Only what the public feeders list already shows: station IDs, whether they are online, their message and position rates and
uptime. No locations, addresses, UUIDs or owners. Prometheus scrapes this over the Docker network.
"""
from flask import Blueprint, Response

from . import feeders as fd
from . import rangestats
from .live import hub_aircraft
from .settings import cfg

bp = Blueprint("metrics", __name__)


def _label(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


@bp.route("/metrics")
def metrics():
    tot = fd.network_totals()
    rows = fd.public_feeders("24h", "positions")
    hub, _err = hub_aircraft()
    lines = []

    def metric(name, help_, kind="gauge"):
        lines.append(f"# HELP {name} {help_}")
        lines.append(f"# TYPE {name} {kind}")

    metric("adsb_portal_feeders_seen", "Receivers that have ever connected to the hub")
    lines.append(f"adsb_portal_feeders_seen {tot['total']}")
    metric("adsb_portal_feeders_online", "Receivers connected to the hub right now")
    lines.append(f"adsb_portal_feeders_online {tot['online']}")
    metric("adsb_portal_stations_named", "Claimed and named stations")
    lines.append(f"adsb_portal_stations_named {tot['claimed']}")
    metric("adsb_portal_feeders_messages_per_second", "Messages per second, all connected receivers")
    lines.append(f"adsb_portal_feeders_messages_per_second {tot['msgs']:.2f}")
    metric("adsb_portal_feeders_positions_per_second", "Positions per second, all connected receivers")
    lines.append(f"adsb_portal_feeders_positions_per_second {tot['pos']:.2f}")
    if hub:
        metric("adsb_portal_aircraft_tracked", "Aircraft the hub is tracking")
        lines.append(f"adsb_portal_aircraft_tracked {hub['totals']['aircraft']}")
        metric("adsb_portal_aircraft_with_position", "Tracked aircraft with a position")
        lines.append(f"adsb_portal_aircraft_with_position {hub['totals']['with_position']}")
    metric("adsb_portal_station_online", "1 when the named station is connected")
    for r in rows:
        lines.append(f'adsb_portal_station_online{{station="{_label(r["station_id"])}"}} {1 if r["online"] else 0}')
    metric("adsb_portal_station_messages_per_second", "Latest message rate of a named station")
    for r in rows:
        lines.append(f'adsb_portal_station_messages_per_second{{station="{_label(r["station_id"])}"}} {(r["msg_per_s"] or 0):.2f}')
    metric("adsb_portal_station_positions_per_second", "Latest position rate of a named station")
    for r in rows:
        lines.append(f'adsb_portal_station_positions_per_second{{station="{_label(r["station_id"])}"}} {(r["pos_per_s"] or 0):.2f}')
    metric("adsb_portal_station_uptime_percent_24h", "Share of the last 24 hours a named station was connected")
    for r in rows:
        lines.append(f'adsb_portal_station_uptime_percent_24h{{station="{_label(r["station_id"])}"}} {r["uptime"]:.1f}')
    metric("adsb_portal_station_max_range_nm_30d", "Farthest range of a named station over the last 30 days, nautical miles")
    for r in rows:
        lines.append(f'adsb_portal_station_max_range_nm_30d{{station="{_label(r["station_id"])}"}} {r["max_range_30d"]:.0f}')
    metric("adsb_portal_station_max_range_nm_today", "Farthest range of a named station today (UTC), nautical miles")
    today = rangestats.today_by_station()
    for r in rows:
        lines.append(f'adsb_portal_station_max_range_nm_today{{station="{_label(r["station_id"])}"}} {today.get(r["uuid"], 0):.0f}')
    if cfg("uat_enabled") == "1":
        from . import uatfeed
        uat = uatfeed.aircraft_by_uuid()
        metric("adsb_portal_stations_sending_uat", "Stations that delivered UAT (978 MHz) lately")
        lines.append(f"adsb_portal_stations_sending_uat {uatfeed.network_count()}")
        metric("adsb_portal_station_uat_aircraft_hour", "UAT aircraft a named station delivered in the last hour (0 when none came through lately)")
        for r in rows:
            lines.append(f'adsb_portal_station_uat_aircraft_hour{{station="{_label(r["station_id"])}"}} {uat.get(r["uuid"], 0)}')
    if cfg("uat_enabled") == "1":
        metric("adsb_portal_station_uat_aircraft_total", "Running total of the different UAT aircraft a named station has delivered (take the increase over a time range)", "counter")
        tot = uatfeed.totals_by_uuid()
        for r in rows:
            lines.append(f'adsb_portal_station_uat_aircraft_total{{station="{_label(r["station_id"])}"}} {tot.get(r["uuid"], 0)}')
        metric("adsb_portal_station_uat_max_range_nm", "Farthest UAT aircraft a named station delivered in the last hour, nautical miles (0 when none)")
        far = _uat_far(hub)
        for r in rows:
            lines.append(f'adsb_portal_station_uat_max_range_nm{{station="{_label(r["station_id"])}"}} {far.get(r["uuid"], 0):.0f}')
    _weather_metrics(lines, metric)
    _sonde_metrics(lines, metric)
    return Response("\n".join(lines) + "\n", mimetype="text/plain; version=0.0.4")


def _uat_far(hub):
    """{station uuid: distance in nautical miles to the farthest UAT aircraft it delivered lately}. Needs the station's position, which is used here and never shown."""
    from math import asin, cos, radians, sin, sqrt
    from . import uatfeed
    from .db import get_db
    db = get_db()
    pos = {r["uuid"]: (r["lat"], r["lon"]) for r in db.execute("SELECT uuid, lat, lon FROM feeders WHERE lat IS NOT NULL AND lon IS NOT NULL").fetchall()}
    far = {}
    # the last position seen for each UAT aircraft of the last hour (kept by /metrics/dump978 while the hub tracks it: UAT aircraft are rare and soon gone)
    for r in db.execute("SELECT feeder_uuid, lat, lon FROM feeder_uat_aircraft WHERE lat IS NOT NULL AND lon IS NOT NULL AND last_seen > now() - interval '1 hour'").fetchall():
        uuid, p = r["feeder_uuid"], pos.get(r["feeder_uuid"])
        if not p:
            continue
        a = {"lat": r["lat"], "lon": r["lon"]}
        la1, lo1, la2, lo2 = map(radians, (p[0], p[1], a["lat"], a["lon"]))
        h = sin((la2 - la1) / 2) ** 2 + cos(la1) * cos(la2) * sin((lo2 - lo1) / 2) ** 2
        far[uuid] = max(far.get(uuid, 0), 2 * 3440.065 * asin(sqrt(h)))
    return far


@bp.route("/metrics/dump978")
def metrics_dump978():
    """The hub's UAT aircraft in the series names the Ultrafeeder dashboard's UAT panels read from a local dump978 (job "dump978"): the hub has no 978 MHz radio, so these come from what the stations
    delivered (feeder_uat_aircraft) joined with the hub's own aircraft list. Addresses, call signs, positions and signal levels of aircraft only (as the public map shows); nothing about stations."""
    from . import uatfeed
    hub, _err = hub_aircraft()
    lines = []
    if hub and cfg("uat_enabled") == "1":
        got = {icao.lower() for _u, icao, _t in uatfeed.aircraft_now()}
        planes = [a for a in hub.get("aircraft", []) if a.get("hex", "").lower() in got]
        from .db import get_db
        db = get_db()
        for a in planes:   # remember where it was, for the farthest-aircraft figure after the hub has dropped it
            if a.get("lat") is not None and a.get("lon") is not None:
                db.execute("UPDATE feeder_uat_aircraft SET lat=%s, lon=%s WHERE lower(icao)=%s", (a["lat"], a["lon"], a["hex"].lower()))
        db.commit()
        lines += ["# HELP aircraft_position_lat Latitude of a UAT aircraft the stations delivered lately", "# TYPE aircraft_position_lat gauge"]
        for a in planes:
            if a.get("lat") is not None and a.get("lon") is not None:
                lines.append(f'aircraft_position_lat{{address="{_label(a["hex"])}",callsign="{_label((a.get("flight") or "").strip())}",airground_state="{"ground" if a.get("gnd") else "airborne"}"}} {a["lat"]}')
        lines += ["# HELP aircraft_position_lon Longitude of a UAT aircraft the stations delivered lately", "# TYPE aircraft_position_lon gauge"]
        for a in planes:
            if a.get("lat") is not None and a.get("lon") is not None:
                lines.append(f'aircraft_position_lon{{address="{_label(a["hex"])}",callsign="{_label((a.get("flight") or "").strip())}",airground_state="{"ground" if a.get("gnd") else "airborne"}"}} {a["lon"]}')
        lines += ["# HELP aircraft_metadata_rssi Signal level the hub measured for a UAT aircraft (dBFS)", "# TYPE aircraft_metadata_rssi gauge"]
        for a in planes:
            if a.get("rssi") is not None:
                lines.append(f'aircraft_metadata_rssi{{address="{_label(a["hex"])}"}} {a["rssi"]}')
    return Response("\n".join(lines) + "\n", mimetype="text/plain; version=0.0.4")


WX_FIELDS = [("temperature_f", "temp_f", "Temperature, degrees Fahrenheit"), ("dew_point_f", "dew_f", "Dew point, degrees Fahrenheit"), ("humidity_percent", "hum", "Relative humidity, percent"),
             ("pressure_inhg", "press_in", "Pressure, inches of mercury"), ("wind_mph", "wind_mph", "Wind speed, miles per hour"), ("gust_mph", "gust_mph", "Wind gust, miles per hour"),
             ("rain_today_in", "rain_day_in", "Rain so far today, inches"), ("rain_rate_in", "rain_rate_in", "Rain rate, inches per hour"), ("solar_wm2", "solar", "Solar radiation, watts per square meter"),
             ("uv_index", "uv", "UV index")]


def _weather_metrics(lines, metric):
    """The public weather stations' latest readings, by station ID (no locations, owners or addresses). Feeds the Weather Statistics dashboard."""
    from . import weather as wx
    if cfg("wx_enabled") != "1":
        return
    try:
        stations = wx.public_stations()
    except Exception:
        return
    metric("adsb_portal_wx_stations_total", "Public weather stations")
    lines.append(f"adsb_portal_wx_stations_total {len(stations)}")
    metric("adsb_portal_wx_stations_online", "Public weather stations that reported lately")
    lines.append(f"adsb_portal_wx_stations_online {sum(1 for s in stations if s['online'])}")
    metric("adsb_portal_wx_station_online", "1 when the public weather station reported lately")
    for s in stations:
        lines.append(f'adsb_portal_wx_station_online{{station="{_label(s["sid"])}"}} {1 if s["online"] else 0}')
    for name, key, help_ in WX_FIELDS:
        metric(f"adsb_portal_wx_{name}", help_)
        for s in stations:
            v = s["latest"].get(key)
            if v is not None and s["online"]:
                lines.append(f'adsb_portal_wx_{name}{{station="{_label(s["sid"])}"}} {float(v):.3f}')


def _sonde_metrics(lines, metric):
    """Balloon counts and the balloons in the air now. Feeds the Balloon Statistics dashboard."""
    if cfg("sonde_enabled") != "1":
        return
    from . import sondes as sd
    from .db import get_db
    try:
        c = sd.counts()
        aloft = get_db().execute("SELECT serial, agency, kind, last_alt, last_vel_v, last_temp, max_alt, status, last_vel_v FROM sondes WHERE status = 'aloft' AND last_seen > now() - interval '30 minutes'").fetchall()
    except Exception:
        get_db().rollback()
        return
    phases = [sd.phase(r) for r in aloft]
    metric("adsb_portal_sondes_aloft", "Balloons in the air now")
    lines.append(f"adsb_portal_sondes_aloft {len(aloft)}")
    metric("adsb_portal_sondes_climbing", "Balloons climbing now (not yet burst)")
    lines.append(f"adsb_portal_sondes_climbing {phases.count('climbing')}")
    metric("adsb_portal_sondes_falling", "Balloons that have burst and are falling now")
    lines.append(f"adsb_portal_sondes_falling {phases.count('falling')}")
    metric("adsb_portal_sondes_flights_24h", "Flights heard in the last 24 hours")
    lines.append(f"adsb_portal_sondes_flights_24h {c['flights']['day']}")
    metric("adsb_portal_sondes_flights_total", "Flights recorded")
    lines.append(f"adsb_portal_sondes_flights_total {c['flights']['total']}")
    metric("adsb_portal_sondes_nws_flights_total", "National Weather Service flights recorded")
    lines.append(f"adsb_portal_sondes_nws_flights_total {c['flights']['nws']}")
    metric("adsb_portal_sonde_stations_active", "Receiving stations heard in the last 24 hours")
    lines.append(f"adsb_portal_sonde_stations_active {c['stations']['active']}")
    metric("adsb_portal_sonde_altitude_m", "Altitude of a balloon in the air, meters")
    for r in aloft:
        if r["last_alt"] is not None:
            lines.append(f'adsb_portal_sonde_altitude_m{{serial="{_label(r["serial"])}",source="{_label(r["agency"] or r["kind"])}"}} {r["last_alt"]:.0f}')
    metric("adsb_portal_sonde_vertical_speed_ms", "Vertical speed of a balloon in the air, meters per second")
    for r in aloft:
        if r["last_vel_v"] is not None:
            lines.append(f'adsb_portal_sonde_vertical_speed_ms{{serial="{_label(r["serial"])}",source="{_label(r["agency"] or r["kind"])}"}} {r["last_vel_v"]:.1f}')
    metric("adsb_portal_sonde_temperature_c", "Air temperature measured by a balloon in the air, degrees Celsius")
    for r in aloft:
        if r["last_temp"] is not None:
            lines.append(f'adsb_portal_sonde_temperature_c{{serial="{_label(r["serial"])}",source="{_label(r["agency"] or r["kind"])}"}} {r["last_temp"]:.1f}')
