"""Satellite passes: when the satellites people listen to (the space station, weather satellites, amateur radio satellites) rise above the horizon of a chosen place, how high they get and which way to look.

Orbits come from CelesTrak's public element sets (two-line elements, derived from US Space Force tracking; see DATA_SOURCES_TERMS.md), kept in the database and refreshed by Data Updates. A satellite's position is
worked out with the `sgp4` library (the standard propagator those elements are made for) and turned into the direction and height above the horizon for the observer with plain WGS-84 geometry. Nothing is
drawn from the network when a page opens: only the stored elements are read."""
import math
import re
import urllib.request
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, render_template, request

from .db import get_db
from .settings import cfg, cfg_int

bp = Blueprint("satpass", __name__)

WGS_A = 6378.137            # km
WGS_F = 1 / 298.257223563
E2 = WGS_F * (2 - WGS_F)
STEP = 20                   # seconds between positions while looking for passes

# how each satellite is worked: kind, uplink and downlink in MHz (None = receive only), mode, an access tone, whether a license is needed to transmit, what equipment it takes, and a note
# (a guide, not a promise: satellites and their radios change, see amsat.org/status)
FM_NEED = "A dual-band handheld or mobile radio (5 W is plenty) and, ideally, a handheld directional antenna (an Arrow or small Yagi). Retune the receiver during the pass for Doppler."
LIN_NEED = "An all-mode radio for 2 m and 70 cm (SSB and CW), a beam antenna for each band, and a steady hand tuning for Doppler. You hear your own signal in the downlink."
WX_NEED = "Receive only: an RTL-SDR dongle, a V-dipole or turnstile antenna pointed at the sky and decoding software. No license is needed."
RADIO = {
    25544: {"kind": "Space station: APRS, voice, SSTV", "up": 145.825, "down": 145.825, "mode": "FM, 1200 baud APRS packets", "tone": "", "lic": True, "need": FM_NEED,
            "note": "International Space Station: the APRS digipeater is on 145.825 MHz FM both ways, crew voice contacts and SSTV picture events (145.800 MHz) happen on announced days."},
    25338: {"kind": "Weather pictures (APT)", "up": None, "down": 137.620, "mode": "APT, FM", "tone": "", "lic": False, "need": WX_NEED, "note": "NOAA 15: weather pictures near 137.620 MHz while it is still in service."},
    28654: {"kind": "Weather pictures (APT)", "up": None, "down": 137.9125, "mode": "APT, FM", "tone": "", "lic": False, "need": WX_NEED, "note": "NOAA 18: weather pictures near 137.9125 MHz while it is still in service."},
    33591: {"kind": "Weather pictures (APT)", "up": None, "down": 137.100, "mode": "APT, FM", "tone": "", "lic": False, "need": WX_NEED, "note": "NOAA 19: weather pictures near 137.100 MHz while it is still in service."},
    57166: {"kind": "Weather pictures (LRPT)", "up": None, "down": 137.900, "mode": "LRPT, digital QPSK", "tone": "", "lic": False, "need": WX_NEED, "note": "METEOR-M2 3: digital weather pictures around 137.9 MHz."},
    59051: {"kind": "Weather pictures (LRPT)", "up": None, "down": 137.100, "mode": "LRPT, digital QPSK", "tone": "", "lic": False, "need": WX_NEED, "note": "METEOR-M2 4: digital weather pictures around 137.1 MHz."},
    27607: {"kind": "FM voice repeater", "up": 145.850, "down": 436.795, "mode": "FM", "tone": "67.0 Hz", "lic": True, "need": FM_NEED, "note": "SO-50: FM voice repeater; the uplink needs a 67 Hz tone to switch it on."},
    43017: {"kind": "FM voice repeater", "up": 435.250, "down": 145.960, "mode": "FM", "tone": "67.0 Hz", "lic": True, "need": FM_NEED, "note": "AO-91 (Fox-1B): FM voice repeater; uplink 435.250 MHz, downlink 145.960 MHz."},
    43137: {"kind": "FM voice repeater", "up": 435.350, "down": 145.880, "mode": "FM", "tone": "67.0 Hz", "lic": True, "need": FM_NEED, "note": "AO-92 (Fox-1D): FM voice repeater; uplink 435.350 MHz, downlink 145.880 MHz."},
    44909: {"kind": "Linear transponder", "up": 435.640, "down": 145.935, "mode": "SSB and CW, inverting", "tone": "", "lic": True, "need": LIN_NEED, "note": "RS-44: linear transponder; the band is about 20 kHz wide around these frequencies, and tuning up on the downlink means tuning down on the uplink."},
    24278: {"kind": "Linear transponder", "up": 145.900, "down": 435.800, "mode": "SSB and CW, inverting", "tone": "", "lic": True, "need": LIN_NEED, "note": "FO-29: linear transponder, often switched to digital mode; check AMSAT's status page."},
    7530: {"kind": "Linear transponder", "up": 432.125, "down": 145.925, "mode": "SSB and CW (mode B), inverting", "tone": "", "lic": True, "need": LIN_NEED, "note": "AO-7: linear transponder; works only in sunlight, and mode B is on certain days."},
}
INFO = {k: v["note"] for k, v in RADIO.items()}
COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def compass(deg):
    return COMPASS[int((deg % 360) / 22.5 + 0.5) % 16]


# ------------------------------------------------------------------ the geometry

def observer(lat, lon, alt_m=0.0):
    """An observer's ECEF position (km) and the matrices' ingredients."""
    la, lo = math.radians(lat), math.radians(lon)
    n = WGS_A / math.sqrt(1 - E2 * math.sin(la) ** 2)
    h = alt_m / 1000.0
    x = (n + h) * math.cos(la) * math.cos(lo)
    y = (n + h) * math.cos(la) * math.sin(lo)
    z = (n * (1 - E2) + h) * math.sin(la)
    return {"lat": la, "lon": lo, "ecef": (x, y, z)}


def gmst(jd_ut1):
    """Greenwich mean sidereal time in radians (the IAU 1982 formula, from a Julian date)."""
    t = (jd_ut1 - 2451545.0) / 36525.0
    sec = 67310.54841 + (876600 * 3600 + 8640184.812866) * t + 0.093104 * t * t - 6.2e-6 * t ** 3
    return math.radians((sec % 86400.0) / 240.0) % (2 * math.pi)


def _jd(t):
    from sgp4.api import jday
    return jday(t.year, t.month, t.day, t.hour, t.minute, t.second + t.microsecond / 1e6)


def look(sat, obs, t):
    """(azimuth degrees, elevation degrees, range km, sub-satellite latitude, longitude, height km) of a satellite for an observer at time t (UTC); None when the propagator gives up."""
    jd, fr = _jd(t)
    err, r, _v = sat.sgp4(jd, fr)
    if err != 0:
        return None
    th = gmst(jd + fr)
    c, s = math.cos(th), math.sin(th)
    x, y, z = c * r[0] + s * r[1], -s * r[0] + c * r[1], r[2]   # TEME to ECEF (the small polar motion is ignored)
    ox, oy, oz = obs["ecef"]
    dx, dy, dz = x - ox, y - oy, z - oz
    la, lo = obs["lat"], obs["lon"]
    east = -math.sin(lo) * dx + math.cos(lo) * dy
    north = -math.sin(la) * math.cos(lo) * dx - math.sin(la) * math.sin(lo) * dy + math.cos(la) * dz
    up = math.cos(la) * math.cos(lo) * dx + math.cos(la) * math.sin(lo) * dy + math.sin(la) * dz
    rng = math.sqrt(dx * dx + dy * dy + dz * dz)
    el = math.degrees(math.asin(max(-1.0, min(1.0, up / rng))))
    az = math.degrees(math.atan2(east, north)) % 360
    p = math.sqrt(x * x + y * y)
    glat = math.atan2(z, p * (1 - E2))
    for _ in range(4):   # geodetic latitude of the point under the satellite
        n = WGS_A / math.sqrt(1 - E2 * math.sin(glat) ** 2)
        glat = math.atan2(z + E2 * n * math.sin(glat), p)
    n = WGS_A / math.sqrt(1 - E2 * math.sin(glat) ** 2)
    height = p / math.cos(glat) - n if abs(math.cos(glat)) > 1e-6 else abs(z) / math.sin(glat) - n * (1 - E2)
    return az, el, rng, math.degrees(glat), (math.degrees(math.atan2(y, x)) + 180) % 360 - 180, height


def satrec(line1, line2):
    from sgp4.api import Satrec
    return Satrec.twoline2rv(line1, line2)


def _refine(sat, obs, t_lo, t_hi, rising):
    """The time between t_lo and t_hi when the elevation crosses zero (bisection to about a second)."""
    for _ in range(7):
        mid = t_lo + (t_hi - t_lo) / 2
        st = look(sat, obs, mid)
        up = st is not None and st[1] > 0
        if up == rising:
            t_hi = mid
        else:
            t_lo = mid
    return t_hi if rising else t_lo


def passes(sat, obs, start, hours, min_elev):
    """The passes of one satellite over the next `hours`: [{aos, los, max_el, tca, az_aos, az_los, az_max, track}] with the track as [(seconds from rise, azimuth, elevation)]. A pass counts when it climbs to min_elev."""
    out, end = [], start + timedelta(hours=hours)
    t = start
    prev = look(sat, obs, t)
    cur = None
    while t < end:
        nt = t + timedelta(seconds=STEP)
        st = look(sat, obs, nt)
        if prev is not None and st is not None:
            if cur is None and prev[1] <= 0 < st[1]:
                aos = _refine(sat, obs, t, nt, True)
                a = look(sat, obs, aos)
                cur = {"aos": aos, "az_aos": a[0] if a else st[0], "max_el": st[1], "tca": nt, "az_max": st[0], "pts": [(aos, a[0] if a else st[0], 0.0)]}
            if cur is not None:
                cur["pts"].append((nt, st[0], st[1]))
                if st[1] > cur["max_el"]:
                    cur["max_el"], cur["tca"], cur["az_max"] = st[1], nt, st[0]
                if st[1] <= 0:
                    los = _refine(sat, obs, t, nt, False)
                    b = look(sat, obs, los)
                    cur["los"], cur["az_los"] = los, b[0] if b else st[0]
                    if cur["max_el"] >= min_elev:
                        out.append(_finish(cur))
                    cur = None
        elif cur is not None and st is None:
            cur = None
        prev, t = st, nt
    if cur is not None and cur["max_el"] >= min_elev:   # still up when the window ends
        cur["los"], cur["az_los"] = end, cur["pts"][-1][1]
        out.append(_finish(cur))
    return out


def doppler(sat, obs, t, mhz):
    """The shift of a downlink on mhz as heard at time t, in kHz (positive while the satellite comes toward you), from how fast the range is changing."""
    a, b = look(sat, obs, t - timedelta(seconds=5)), look(sat, obs, t + timedelta(seconds=5))
    if a is None or b is None:
        return None
    return -mhz * 1000.0 * ((b[2] - a[2]) / 10.0) / 299792.458


def _finish(c):
    pts = c.pop("pts")
    t0 = c["aos"]
    step = max(1, len(pts) // 24)   # about two dozen points are enough for the sky chart
    c["track"] = [(round((p[0] - t0).total_seconds()), round(p[1], 1), round(p[2], 1)) for p in pts[::step]]
    c["duration"] = round((c["los"] - c["aos"]).total_seconds())
    return c


def polar_svg(track, size=150):
    """A sky chart (zenith in the middle, horizon at the rim, north up) of one pass as an inline SVG string."""
    r = size / 2 - 14
    cx = cy = size / 2

    def xy(az, el):
        rad = (90 - max(0, el)) / 90 * r
        a = math.radians(az)
        return cx + rad * math.sin(a), cy - rad * math.cos(a)
    pts = [xy(az, el) for _t, az, el in track]
    if not pts:
        return ""
    d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    ring = "".join(f'<circle cx="{cx}" cy="{cy}" r="{r * k / 3:.1f}" fill="none" stroke="var(--line)" stroke-width="1"/>' for k in (1, 2, 3))
    lab = "".join(f'<text x="{cx + (r + 8) * math.sin(math.radians(a)):.1f}" y="{cy - (r + 8) * math.cos(math.radians(a)) + 3:.1f}" text-anchor="middle" font-size="9" fill="var(--dim)">{n}</text>' for n, a in (("N", 0), ("E", 90), ("S", 180), ("W", 270)))
    return (f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}" role="img" aria-label="Where the satellite crosses the sky: from {compass(track[0][1])} to {compass(track[-1][1])}">{ring}{lab}'
            f'<path d="{d}" fill="none" stroke="var(--acc)" stroke-width="2"/><circle cx="{pts[0][0]:.1f}" cy="{pts[0][1]:.1f}" r="3.5" fill="var(--acc2)"/><circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="3.5" fill="var(--dim)"/></svg>')


# ------------------------------------------------------------------ the element sets

def tracked():
    return [int(x) for x in re.split(r"[,\s]+", cfg("satpass_tracked").strip()) if x.isdigit()]


def refresh():
    """Read the element sets of the chosen groups from CelesTrak and keep those of the tracked satellites. Returns the number kept."""
    want = set(tracked())
    base = cfg("satpass_source_url").rstrip("/")
    d = get_db()
    kept = 0
    for g in [x.strip() for x in cfg("satpass_groups").split(",") if x.strip()]:
        req = urllib.request.Request(f"{base}?GROUP={g}&FORMAT=tle", headers={"User-Agent": cfg("lookup_user_agent")})
        with urllib.request.urlopen(req, timeout=30) as r:
            lines = [ln.rstrip() for ln in r.read().decode("utf-8", "replace").splitlines() if ln.strip()]
        for i in range(0, len(lines) - 2, 3):
            name, l1, l2 = lines[i].strip(), lines[i + 1], lines[i + 2]
            if not (l1.startswith("1 ") and l2.startswith("2 ")):
                continue
            try:
                norad = int(l1[2:7])
                yy, day = int(l1[18:20]), float(l1[20:32])
            except ValueError:
                continue
            if norad not in want:
                continue
            epoch = datetime(2000 + yy if yy < 57 else 1900 + yy, 1, 1, tzinfo=timezone.utc) + timedelta(days=day - 1)
            d.execute("INSERT INTO sat_tle (norad, name, line1, line2, epoch, fetched, group_name) VALUES (%s,%s,%s,%s,%s,now(),%s) ON CONFLICT (norad) DO UPDATE SET name=EXCLUDED.name, line1=EXCLUDED.line1, "
                      "line2=EXCLUDED.line2, epoch=EXCLUDED.epoch, fetched=now(), group_name=EXCLUDED.group_name", (norad, name, l1, l2, epoch, g))
            kept += 1
    d.execute("DELETE FROM sat_tle WHERE NOT (norad = ANY(%s))", (sorted(want) or [0],))
    d.commit()
    return kept


def elements():
    return get_db().execute("SELECT * FROM sat_tle ORDER BY name").fetchall()


# ------------------------------------------------------------------ the map layer

R_EARTH = 6371.0


def _dest(lat, lon, bearing, ang):
    """The point `ang` radians away from (lat, lon) degrees along `bearing` radians (a sphere is exact enough for a drawn circle). Longitude is not wrapped, so a circle across the date line stays one piece."""
    la, lo = math.radians(lat), math.radians(lon)
    la2 = math.asin(math.sin(la) * math.cos(ang) + math.cos(la) * math.sin(ang) * math.cos(bearing))
    lo2 = lo + math.atan2(math.sin(bearing) * math.sin(ang) * math.cos(la), math.cos(ang) - math.sin(la) * math.sin(la2))
    return math.degrees(la2), math.degrees(lo2)


def _kind(norad):
    r = RADIO.get(norad) or {}
    if r.get("up") is None and r.get("down"):
        return "wx"
    return "iss" if norad == 25544 else "ham"


def _next_pass(e, obs, now):
    from .cache import cached

    def load():
        try:
            ps = passes(satrec(e["line1"], e["line2"]), obs, now, cfg_int("satpass_hours"), cfg_int("satpass_min_elev"))
        except Exception:
            return None
        return {"aos": ps[0]["aos"].isoformat(), "max_el": ps[0]["max_el"], "az": compass(ps[0]["az_aos"])} if ps else None
    return cached(("satmap_pass", e["norad"]), load, 600)


def map_geojson():
    """The Satellites map layer: each tracked satellite's place right now with a card of what it is and how to work it, the path it takes over the next minutes and (when on) the part of the earth it can see."""
    from .cache import cached
    return cached(("satmap",), _build_map, max(5, cfg_int("satmap_cache_seconds")))


def _build_map():
    feats = []
    now = datetime.now(timezone.utc).replace(microsecond=0)
    obs = observer(float(cfg("map_lat")), float(cfg("map_lon")))
    minutes, foot = cfg_int("satmap_track_minutes"), cfg("satmap_footprints") == "1"
    for e in elements():
        try:
            sat = satrec(e["line1"], e["line2"])
        except Exception:
            continue
        st = look(sat, obs, now)
        if st is None:
            continue
        az, el, rng, la, lo, h = st
        kind = _kind(e["norad"])
        r = RADIO.get(e["norad"]) or {}
        speed = math.sqrt(398600.4418 / (WGS_A + h))
        rows = [["Height", f"{h:,.0f} km ({h * 0.621371:,.0f} miles) above the earth"], ["Speed", f"{speed:.1f} km/s ({speed * 2236.94:,.0f} mph)"], ["Over", f"{abs(la):.1f}\u00b0 {'N' if la >= 0 else 'S'}, {abs(lo):.1f}\u00b0 {'E' if lo >= 0 else 'W'}"],
                ["From the map's center", f"{el:.0f}\u00b0 above the horizon, to the {compass(az)}" if el > 0 else "below the horizon"]]
        np_ = _next_pass(e, obs, now)
        if np_:
            rows.append(["Next pass there", np_["aos"], "t"])
            rows.append(["It climbs to", f"{np_['max_el']:.0f}\u00b0, rising in the {np_['az']}"])
        if r:
            rows.append(["Type", r["kind"]])
            if r.get("up"):
                rows.append(["Uplink", f"{r['up']:.3f} MHz" + (f" with a {r['tone']} tone" if r.get("tone") else "")])
            rows.append(["Downlink", f"{r['down']:.3f} MHz, {r['mode']}"])
            rows.append(["To transmit", "A license is needed" if r["lic"] else "Receive only: no license needed"])
            rows.append(["What you need", r["need"]])
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lo, 3), round(la, 3)]}, "properties": {
            "KIND": kind, "size": 8, "event": e["name"], "headline": f"{h:,.0f} km up, over {abs(la):.0f}\u00b0{'N' if la >= 0 else 'S'} {abs(lo):.0f}\u00b0{'E' if lo >= 0 else 'W'}",
            "rows": rows, "link": f"/radio/satellites?sat={e['norad']}", "linkText": "Pass Times"}})
        if minutes > 0:
            line, last = [], lo
            for k in range(0, minutes + 1):
                p = look(sat, obs, now + timedelta(minutes=k))
                if p is None:
                    continue
                x = p[4]
                while x - last > 180:
                    x -= 360
                while x - last < -180:
                    x += 360
                last = x
                line.append([round(x, 3), round(p[3], 3)])
            if len(line) > 1:
                feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": line}, "properties": {"KIND": kind, "passive": True, "weight": 2, "lineOpacity": .75, "dash": "6 6"}})
        if foot:
            ang = math.acos(R_EARTH / (R_EARTH + h))
            ring = [[round(q[1], 3), round(q[0], 3)] for q in (_dest(la, lo, math.radians(b * 7.5), ang) for b in range(0, 49))]
            feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": {"KIND": kind, "passive": True, "weight": 1, "fillOpacity": .05, "lineOpacity": .5}})
    return {"type": "FeatureCollection", "features": feats}


@bp.route("/api/satellites.geojson")
def api_map():
    from flask import Response
    import json as _json
    if cfg("satpass_enabled") != "1" or cfg("satmap_enabled") != "1":
        return Response('{"type":"FeatureCollection","features":[]}', mimetype="application/geo+json")
    resp = Response(_json.dumps(map_geojson(), separators=(",", ":")), mimetype="application/geo+json")
    resp.headers["Cache-Control"] = "public, max-age=15"
    return resp


# ------------------------------------------------------------------ the page

def _place():
    """(lat, lon, label, choices) from the form: a typed place, or latitude and longitude, or the site's own map center."""
    from . import wxplace
    q = " ".join(request.args.get("q", "").split())[:120]
    try:
        la, lo = float(request.args.get("lat", "")), float(request.args.get("lon", ""))
        if -90 <= la <= 90 and -180 <= lo <= 180:
            return la, lo, q or f"{la:.3f}, {lo:.3f}", []
    except ValueError:
        pass
    if q:
        choices = wxplace.resolve(q)
        if choices:
            return choices[0]["lat"], choices[0]["lon"], choices[0]["label"], choices[1:4]
    return float(cfg("map_lat")), float(cfg("map_lon")), "the middle of this site's map", []


@bp.route("/radio/satellites")
def page():
    if cfg("satpass_enabled") != "1":
        abort(404)
    lat, lon, label, others = _place()
    try:
        hours = max(1, min(cfg_int("satpass_max_hours"), int(request.args.get("hours", cfg_int("satpass_hours")))))
        min_el = max(0, min(80, int(request.args.get("min", cfg_int("satpass_min_elev")))))
    except ValueError:
        hours, min_el = cfg_int("satpass_hours"), cfg_int("satpass_min_elev")
    els = elements()
    chosen = request.args.getlist("sat")
    picked = set(chosen) if (chosen or request.args.get("sel")) else {str(e["norad"]) for e in els}   # sel=1 comes from the form: nothing ticked means no satellites, not all of them
    narrowed = len(picked) < len(els)   # some satellites are left out: list how to work the ones that are in
    groups = {}
    for e in els:
        k = (RADIO.get(e["norad"]) or {}).get("kind", "")
        g = "Space Station" if k.startswith("Space station") else "Weather Satellites" if k.startswith("Weather") else "Amateur Radio Satellites" if k else "Other Satellites"
        groups.setdefault(g, []).append(e)
    order = [g for g in ("Space Station", "Weather Satellites", "Amateur Radio Satellites", "Other Satellites") if g in groups]
    now = datetime.now(timezone.utc).replace(microsecond=0)
    obs = observer(lat, lon)
    rows, up_now = [], []
    for e in els:
        if str(e["norad"]) not in picked:
            continue
        try:
            sat = satrec(e["line1"], e["line2"])
        except Exception:
            continue
        st = look(sat, obs, now)
        if st is not None and st[1] > 0:
            up_now.append({"name": e["name"], "norad": e["norad"], "el": st[1], "az": st[0], "range": st[2]})
        for p in passes(sat, obs, now, hours, min_el):
            r = RADIO.get(e["norad"]) or {}
            dn = r.get("down")
            rows.append(dict(p, name=e["name"], norad=e["norad"], svg=polar_svg(p["track"]), age=(now - e["epoch"]).days, down=dn, mode=r.get("mode", ""),
                             dop_aos=doppler(sat, obs, p["aos"], dn) if dn else None, dop_los=doppler(sat, obs, p["los"], dn) if dn else None))
    rows.sort(key=lambda r: r["aos"])
    return render_template("satellites.html", rows=rows, up_now=up_now, els=els, picked=picked, lat=lat, lon=lon, label=label, others=others, hours=hours, min_el=min_el, now=now, info=INFO, radio=RADIO, compass=compass,
                           max_hours=cfg_int("satpass_max_hours"), groups=[(g, groups[g]) for g in order], narrowed=narrowed)
