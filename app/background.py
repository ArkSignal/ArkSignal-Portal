"""The site background: a faint chart of Arkansas in the site's own colors, drawn as one SVG (served at /background.svg).
The state outline is public-domain boundary data (US Census, generalized) kept in ar_outline.json next to this file;
the map center (range rings) comes from Admin > Settings > Map Defaults so the picture follows the configuration."""
import json
import math
import os

from flask import Blueprint, Response, request

from .db import get_db
from .settings import cfg, cfg_int

bp = Blueprint("background", __name__)

W, H = 1600, 1000
# Arkansas spans about 33.0-36.5 N and 94.6-89.6 W; this centers it a little right of middle so it frames, not covers, the content.
CENTER_LON, CENTER_LAT = -92.12, 34.75
SCALE = 215.0          # pixels per degree of latitude
CX, CY = 1010, 500     # where the center lands in the picture

_outline = None
_neighbors = None


def _load():
    global _outline
    if _outline is None:
        with open(os.path.join(os.path.dirname(__file__), "ar_outline.json"), encoding="utf-8") as f:
            _outline = json.load(f)
    return _outline


def _load_neighbors():
    global _neighbors
    if _neighbors is None:
        with open(os.path.join(os.path.dirname(__file__), "neighbor_states.json"), encoding="utf-8") as f:
            _neighbors = json.load(f)
    return _neighbors


def _xy(lon, lat):
    k = math.cos(math.radians(CENTER_LAT))
    return CX + (lon - CENTER_LON) * k * SCALE, CY - (lat - CENTER_LAT) * SCALE


def _airports():
    """Arkansas airports with their longest runway heading and length, for the faint chart inside the outline."""
    rows = get_db().execute(
        "SELECT a.ident, a.type, a.lat, a.lon, a.name, r.length_ft, r.le_heading FROM airports a "
        "LEFT JOIN LATERAL (SELECT length_ft, le_heading FROM runways WHERE airport_id=a.id AND NOT closed AND length_ft IS NOT NULL "
        "ORDER BY length_ft DESC LIMIT 1) r ON TRUE "
        "WHERE a.region='US-AR' AND a.type IN ('large_airport','medium_airport','small_airport')").fetchall()
    return rows


def build_svg(map_lat, map_lon, airports=(), radar_range_nm=0, units="imperial"):
    ring = _load()
    pts = [_xy(lon, lat) for lon, lat in ring]
    outline = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + " Z"
    mx, my = _xy(map_lon, map_lat)
    if radar_range_nm:   # square view centered on the map center whose edge is the home page radar's range, so the two line up
        half = radar_range_nm / 60 * SCALE / 0.97
        view = f"{mx - half:.1f} {my - half:.1f} {2 * half:.1f} {2 * half:.1f}"
    else:
        view = f"0 0 {W} {H}"
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}" preserveAspectRatio="xMidYMid slice">',
             '<defs><filter id="g" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="7"/></filter>'
             f'<clipPath id="st"><path d="{outline}"/></clipPath><radialGradient id="f" cx="50%" cy="50%" r="60%"><stop offset="0" stop-color="#4fb3ff" stop-opacity=".06"/><stop offset="1" stop-color="#4fb3ff" stop-opacity=".015"/></radialGradient></defs>']
    if radar_range_nm:   # the states around Arkansas, drawn like Arkansas but quieter, with their postal codes
        for code, rings in _load_neighbors().items():
            path = " ".join("M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in (_xy(lon, lat) for lon, lat in ring)) + " Z" for ring in rings)
            parts.append(f'<path d="{path}" fill="#4fb3ff" fill-opacity=".03" stroke="#3ee6a8" stroke-opacity=".3" stroke-width="1.2" stroke-linejoin="round"/>')
            # the postal code goes in the middle of whatever part of the state is inside the picture
            inside = [q for q in (_xy(lon, lat) for ring in rings for lon, lat in ring)
                      if mx - half + 40 < q[0] < mx + half - 40 and my - half + 40 < q[1] < my + half - 40]
            if len(inside) > 3:
                lx, ly = sum(q[0] for q in inside) / len(inside), sum(q[1] for q in inside) / len(inside)
                parts.append(f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" fill="#3ee6a8" fill-opacity=".4" font-family="JetBrains Mono,monospace" font-size="34" font-weight="700">{code}</text>')
    # graticule every degree, labelled on the edges
    for lat in range(30, 40):
        x0, y = _xy(-100, lat)
        x1, _ = _xy(-85, lat)
        parts.append(f'<line x1="{x0:.0f}" y1="{y:.1f}" x2="{x1:.0f}" y2="{y:.1f}" stroke="#4fb3ff" stroke-opacity=".04" stroke-width="1"/>')
        parts.append(f'<text x="14" y="{y - 4:.1f}" fill="#4fb3ff" fill-opacity=".14" font-family="JetBrains Mono,monospace" font-size="13">{lat}°N</text>')
    for lon in range(-100, -84):
        x, y0 = _xy(lon, 40)
        _, y1 = _xy(lon, 30)
        parts.append(f'<line x1="{x:.1f}" y1="{y0:.0f}" x2="{x:.1f}" y2="{y1:.0f}" stroke="#4fb3ff" stroke-opacity=".07" stroke-width="1"/>')
        parts.append(f'<text x="{x + 4:.1f}" y="{H - 12}" fill="#4fb3ff" fill-opacity=".14" font-family="JetBrains Mono,monospace" font-size="13">{abs(lon)}°W</text>')
    # rings every 50 of the viewer's own unit (miles, nautical miles or kilometers); the labeled ring is every second one
    per_nm, word, step = {"metric": (1.852, "km", 100), "aero": (1.0, "NM", 50)}.get(units, (1.150779, "mi", 50))
    for n in range(step, int(500 * per_nm) + 1, step):
        nm = n / per_nm
        r = nm / 60 * SCALE
        parts.append(f'<circle cx="{mx:.1f}" cy="{my:.1f}" r="{r:.1f}" fill="none" stroke="#3ee6a8" stroke-opacity="{0.09 if n % (2 * step) == 0 else 0.045}" stroke-width="1" stroke-dasharray="{"2 7" if n % (2 * step) else "none"}"/>')
        if n % (2 * step) == 0:
            parts.append(f'<text x="{mx + r * 0.7071 + 4:.1f}" y="{my - r * 0.7071:.1f}" fill="#3ee6a8" fill-opacity=".18" font-family="JetBrains Mono,monospace" font-size="12">{n} {word}</text>')
    parts.append(f'<line x1="{mx:.1f}" y1="0" x2="{mx:.1f}" y2="{H}" stroke="#3ee6a8" stroke-opacity=".04"/><line x1="0" y1="{my:.1f}" x2="{W}" y2="{my:.1f}" stroke="#3ee6a8" stroke-opacity=".07"/>')
    # the state: soft glow under a crisp line
    parts.append(f'<path d="{outline}" fill="url(#f)" stroke="#3ee6a8" stroke-opacity=".28" stroke-width="5" filter="url(#g)"/>')
    parts.append(f'<path d="{outline}" fill="none" stroke="#3ee6a8" stroke-opacity=".34" stroke-width="1.4" stroke-linejoin="round"/>')
    parts.append(f'<circle cx="{mx:.1f}" cy="{my:.1f}" r="3.5" fill="#3ee6a8" fill-opacity=".4"/>')
    # a faint aeronautical chart inside the state: airports, runways and the controlled-airspace rings around the big ones
    chart = []
    for a in airports:
        x, y = _xy(a["lon"], a["lat"])
        big = a["type"] in ("large_airport", "medium_airport")
        if big:
            r = (10 if a["type"] == "large_airport" else 5) / 60 * SCALE
            chart.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="none" stroke="#4fb3ff" stroke-opacity=".5" stroke-width="1.2" stroke-dasharray="5 4"/>')
        if a["length_ft"] and a["le_heading"] is not None and a["length_ft"] >= 3000:
            half = min(max(a["length_ft"] / 6076 * SCALE / 60 * 4, 4), 16)
            dx, dy = math.sin(math.radians(a["le_heading"])) * half, -math.cos(math.radians(a["le_heading"])) * half
            chart.append(f'<line x1="{x - dx:.1f}" y1="{y - dy:.1f}" x2="{x + dx:.1f}" y2="{y + dy:.1f}" stroke="#3ee6a8" stroke-opacity=".7" stroke-width="{2.2 if big else 1.4}" stroke-linecap="round"/>')
        else:
            chart.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.8" fill="#3ee6a8" fill-opacity=".6"/>')
        if big:
            chart.append(f'<text x="{x + 9:.1f}" y="{y - 7:.1f}" fill="#4fb3ff" fill-opacity=".8" font-family="JetBrains Mono,monospace" font-size="10">{a["ident"]}</text>')
    parts.append(f'<g clip-path="url(#st)" opacity=".26">{"".join(chart)}</g>')
    parts.append("</svg>")
    return "".join(parts)


@bp.route("/background.svg")
def background():
    radar = request.args.get("radar", type=int) or 0
    radar = min(max(radar, 0), 1000)
    strength = cfg_int("radar_map_strength" if radar else "background_strength")
    u = request.args.get("u", "")
    svg = build_svg(float(cfg("map_lat")), float(cfg("map_lon")), _airports(), radar, u if u in ("imperial", "aero", "metric") else "imperial")
    if strength < 100:   # one master fade over the whole picture
        svg = svg.replace("<svg ", f'<svg opacity="{strength / 100:.2f}" ', 1)
    return Response(svg, mimetype="image/svg+xml", headers={"Cache-Control": "public, max-age=60"})
