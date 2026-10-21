"""The portal's own Replay and Heatmap: the hub's heatmap files (histchunks.py), passed on whole for replay and added up into a grid for the heat map."""
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, Response, abort, jsonify, request

from . import histchunks as hc
from .settings import cfg_int

bp = Blueprint("histmap", __name__)


def _day(text):
    try:
        d = date.fromisoformat(text)
    except ValueError:
        abort(400)
    today = datetime.now(timezone.utc).date()
    if d > today + timedelta(days=1) or d < today - timedelta(days=3650):
        abort(400)
    return d


@bp.route("/api/history/chunk/<day>/<int:index>.bin")
def chunk(day, index):
    """One half hour of positions, exactly as the hub keeps it (decompressed), for the replay to play."""
    if not 0 <= index <= 47:
        abort(404)
    d = _day(day)
    data = hc.fetch(d, index)
    if data is None:
        abort(404)
    resp = Response(data, mimetype="application/octet-stream")
    resp.headers["Cache-Control"] = "public, max-age=1209600" if hc._finished(d, index) else "no-store"
    return resp


def _num(name):
    v = request.args.get(name, "")
    try:
        return float(v) if v != "" else None
    except ValueError:
        abort(400)


@bp.route("/api/history/heat.json")
def heat():
    """The positions of one UTC day (or part of it) added up on a grid for the heat map: cells = [[lat, lon, count], ...] at cell centers."""
    d = _day(request.args.get("day", ""))
    z = max(2, min(14, int(_num("z") or 8)))
    amin, amax = _num("amin"), _num("amax")
    since, until = _num("since"), _num("until")
    lo = datetime.fromtimestamp(since, timezone.utc) if since is not None else None
    hi = datetime.fromtimestamp(until, timezone.utc) if until is not None else None
    grid, found, asked = hc.day_heat(d, z, amin, amax, lo, hi)
    cell = hc.cell_degrees(z)
    s, w, n, e = (_num(k) for k in ("south", "west", "north", "east"))
    out = []
    for (row, col), cnt in grid.items():
        lat, lon = (row + .5) * cell - 90, (col + .5) * cell - 180
        if s is not None and (lat < s - cell or lat > n + cell or lon < w - cell or lon > e + cell):
            continue
        out.append([round(lat, 5), round(lon, 5), cnt])
    limit = max(1000, cfg_int("heat_max_cells"))
    truncated = len(out) > limit
    if truncated:
        out.sort(key=lambda c: -c[2])
        out = out[:limit]
    resp = jsonify({"day": d.isoformat(), "z": z, "cell": cell, "found": found, "asked": asked, "truncated": truncated, "cells": out})
    resp.headers["Cache-Control"] = "no-store"
    return resp
