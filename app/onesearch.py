"""One search for every map: a single box that finds airports, places (cities, ZIP codes, counties), hospitals, repeaters, APRS stations, weather balloons, their launch sites and receiving
stations, and counties, and flies the map to the one picked. This module gathers the suggestions; static/mapsearch.js draws them under the search box of every map page.

Only what the public pages already show is searched (hidden APRS call signs, private station positions and unapproved repeaters are never offered)."""
import re

from .settings import cfg

PER = 4          # suggestions from each kind of thing
TOTAL = 16       # suggestions in all


def _item(kind, label, sub="", lat=None, lon=None, zoom=None, href=""):
    return {"type": kind, "label": label, "sub": sub, "lat": None if lat is None else round(float(lat), 5), "lon": None if lon is None else round(float(lon), 5), "zoom": zoom, "href": href}


def _airports(q, per=PER):
    from . import airports as ap
    rows, _total = ap.search(q, "", "", per * 4, 0)
    rows = sorted(rows, key=lambda r: 0 if (r["region"] or "") == "US-AR" else 1)[:per]   # Arkansas first
    return [_item("Airport", f"{r['ident']} {r['name']}", (r["municipality"] or "") + (", " + r["region"].split("-")[-1] if r["region"] else ""), r["lat"], r["lon"], 13, "/airports/" + r["ident"]) for r in rows if r["lat"] is not None]


def _places(q, per=PER):
    from . import places
    return [_item("Place", r["label"], {"place": "City or town", "zip": "ZIP code", "county": "County"}.get(r["kind"], ""), r["lat"], r["lon"], r["zoom"]) for r in places.search(q, limit=per)]


def _hospitals(q, per=PER):
    from . import hospitals
    rows, _t = hospitals.listing(q=q, limit=per)
    return [_item("Hospital", r["name"], f"{r['city']}, {r['state']}" + (f", trauma level {r['trauma']}" if r["trauma"] else ""), r["lat"], r["lon"], 15, "/hospitals/" + r["facility_id"]) for r in rows if r["lat"] is not None]


def _repeaters(q, per=PER):
    from . import repeaters
    if cfg("repeater_enabled") != "1":
        return []
    _t, rows = repeaters.search(q=q, limit=per)
    return [_item("Repeater", f"{r['callsign'] or 'Repeater'} {repeaters.mhz(r['freq_hz'])} MHz", f"{r['modes'].replace(',', ', ')}" + (f", {r['city']}" if r["city"] else ""), r["lat"], r["lon"], 12, f"/repeaters/{r['id']}") for r in rows if r["lat"] is not None]


def _aprs(q, per=PER):
    from . import aprs
    if cfg("aprs_enabled") != "1" or not re.match(r"^[A-Za-z0-9-]{3,12}$", q):
        return []
    rows = aprs.stations_cached("", q.upper(), None)[:per]
    return [_item("APRS", r["call"], aprs.KIND_LABELS.get(r["kind"], r["kind"]), r["lat"], r["lon"], 12, "/aprs/" + r["call"]) for r in rows]


def _balloons(q, per=PER):
    from . import sondes
    if not sondes.enabled():
        return []
    out = []
    for r in sondes.search(q, per):
        out.append(_item({"flight": "Balloon", "station": "Balloon station", "site": "Launch site"}.get(r["kind"], "Balloon"), r["label"], "", r.get("lat"), r.get("lon"), 10, r["href"]))
    return out


def _counties(q, per=PER):
    from . import counties
    like = q.lower()
    out = []
    for s in counties.shapes():
        if s["base"].lower().startswith(like) or s["name"].lower().startswith(like):
            out.append(_item("County", s["name"], s.get("state", ""), s.get("lat"), s.get("lon"), 10, "/counties/" + s["slug"]))
            if len(out) >= per:
                break
    return out


def search(q):
    """[{type, label, sub, lat, lon, zoom, href}] for the text; each kind of thing that fails is skipped."""
    q = re.sub(r"\s+", " ", (q or "").strip())[:40]
    if len(q) < 2:
        return []
    out = []
    for fn in (_airports, _places, _hospitals, _repeaters, _aprs, _balloons, _counties):
        try:
            out += fn(q)
        except Exception:
            try:
                from .db import get_db
                get_db().rollback()
            except Exception:
                pass
    seen, uniq = set(), []
    for it in out:
        key = (it["type"], it["label"])
        if key not in seen:
            seen.add(key)
            uniq.append(it)
    return uniq[:TOTAL]


def _feeders(q, per=PER):
    from . import feeders as fd
    like = q.lower()
    out = []
    for f in fd.public_feeders("24h", "positions"):
        sid, name = str(f.get("station_id") or f.get("id") or ""), str(f.get("name") or "")
        if like in sid.lower() or like in name.lower():
            out.append(_item("Feeder", sid or name, name if sid else "", None, None, None, "/feeders/" + sid))
            if len(out) >= per:
                break
    return out


def _weather_stations(q, per=PER):
    from . import weather as wx
    if cfg("wx_enabled") != "1":
        return []
    like = q.lower()
    out = []
    for r in wx.map_points("imperial"):
        if like in str(r.get("id", "")).lower() or like in str(r.get("name", "")).lower():
            out.append(_item("Weather station", str(r.get("id")), str(r.get("name") or ""), r.get("lat"), r.get("lon"), 12, "/weather/" + str(r.get("id"))))
            if len(out) >= per:
                break
    return out


def _mesh_nodes(q, per=PER):
    from . import mesh
    if cfg("mesh_enabled") != "1":
        return []
    out = []
    for n in (mesh.nodes_cached(q=q) or [])[:per]:
        out.append(_item("Mesh node", str(n.get("long_name") or n.get("short_name") or n.get("id")), str(n.get("short_name") or ""), n.get("lat"), n.get("lon"), 12, "/mesh/" + str(n.get("slug") or n.get("id") or "")))
    return out


def _call_signs(q, per=PER):
    from . import repeater_digital as rd
    from flask_login import current_user
    if len(re.sub(r"[^A-Za-z0-9]", "", q)) < 2:
        return []
    return [_item("Call sign", c["call"], ", ".join(c["kinds"]), None, None, None, "/callsign?call=" + c["call"].split("-")[0]) for c in rd.portal_prefix(q, current_user.is_authenticated, limit=per)]


GROUPS = (("Airport", "Airports", _airports), ("Place", "Places", _places), ("County", "Counties", _counties), ("Hospital", "Hospitals", _hospitals), ("Repeater", "Repeaters", _repeaters),
          ("APRS", "APRS Stations", _aprs), ("Feeder", "Feeders", _feeders), ("Weather station", "Weather Stations", _weather_stations), ("Mesh node", "Mesh Nodes", _mesh_nodes),
          ("Call sign", "Call Signs", _call_signs))
BALLOON_LABELS = (("Balloon", "Balloons"), ("Balloon station", "Balloon Receiving Stations"), ("Launch site", "Balloon Launch Sites"))


def search_all(q, per=40):
    """Every hit for the site-wide search: [(heading, [items])] in a fixed order, each kind of thing with up to `per` hits; a kind that fails is left out. Places with no page of their own link to the County Map there."""
    q = re.sub(r"\s+", " ", (q or "").strip())[:40]
    if len(q) < 2:
        return []
    groups = []
    def run(fn):
        try:
            return fn(q, per)
        except Exception:
            try:
                from .db import get_db
                get_db().rollback()
            except Exception:
                pass
            return []
    for _kind, heading, fn in GROUPS:
        items = run(fn)
        for it in items:
            if not it["href"] and it["lat"] is not None:
                it["href"] = "/counties/?lat=%s&lon=%s&zoom=%s" % (it["lat"], it["lon"], it["zoom"] or 11)
        if items:
            groups.append((heading, items))
    balloons = run(_balloons)
    for kind, heading in BALLOON_LABELS:
        items = [i for i in balloons if i["type"] == kind]
        if items:
            groups.append((heading, items))
    return groups
