"""Air ambulance helicopters (HEMS): which aircraft on the map are medical flights.

Found two ways, combined: (1) the aircraft database that tar1090 already uses (tar1090-db aircraft.csv.gz: hex; registration; ICAO type; flags; model;
year; owner), filtered to helicopter types, to the registration prefixes we care about and to owners whose name contains one of the operator names set in
Admin > Settings; (2) a list of tail numbers an administrator pastes in (for helicopters owned by a leasing company, which the owner filter cannot see).
Entries can be removed or excluded by hand, and an update never brings back an excluded one."""
import csv
import gzip
import io
import re
import urllib.request
from datetime import datetime, timezone

from flask import Blueprint, jsonify

from .cache import cached
from .db import get_db
from .settings import cfg, get_setting, set_setting

bp = Blueprint("hems", __name__)
TAIL = re.compile(r"^[A-Z0-9-]{2,9}$")


def _get(url, limit=40_000_000):
    req = urllib.request.Request(url, headers={"User-Agent": cfg("lookup_user_agent")})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read(limit)


def download_rows():
    raw = _get(cfg("hems_source_url"))
    text = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    return [ln.split(";") for ln in text.decode("utf-8", "replace").splitlines()]


def parse_tails(text):
    out = []
    for t in re.split(r"[\s,;]+", (text or "").upper()):
        if t and TAIL.match(t) and t not in out:
            out.append(t)
    return out


def _list(setting):
    return [x.strip().upper() for x in cfg(setting).split(",") if x.strip()]


def add_tails(text, note="", kind="heli"):
    """Tail numbers pasted by an administrator, as helicopters or as fixed-wing aircraft. Returns (added, ignored)."""
    kind = "fixed" if kind == "fixed" else "heli"
    tails = parse_tails(text)
    d = get_db()
    added = 0
    for t in tails:
        if d.execute("SELECT 1 FROM hems_aircraft WHERE upper(reg)=%s", (t,)).fetchone():
            d.execute("UPDATE hems_aircraft SET excluded=FALSE, kind=%s, source=CASE WHEN source='registry' THEN 'list' ELSE source END WHERE upper(reg)=%s", (kind, t))
            continue
        d.execute("INSERT INTO hems_aircraft (ident, reg, source, note, kind) VALUES (%s,%s,'list',%s,%s)", ("reg:" + t, t, note[:200], kind))
        added += 1
    d.commit()
    return added, len(tails) - added


def import_registry(rows=None):
    """Refresh the registry entries from the aircraft database. Listed tail numbers get their hex code and details filled in. Hand entries and exclusions stay."""
    rows = rows if rows is not None else download_rows()
    types = set(_list("hems_types"))
    ops = _list("hems_operators")
    ftypes = set(_list("hems_fixed_types"))
    fops = _list("hems_fixed_operators")
    prefixes = tuple(_list("hems_registrations")) or ("",)
    d = get_db()
    listed = {r["reg"].upper(): r for r in d.execute("SELECT * FROM hems_aircraft WHERE source IN ('list','manual')").fetchall()}
    found = {}
    for r in rows:
        if len(r) < 7 or not r[1]:
            continue
        hexid, reg, typ, desc, owner = r[0].lower(), r[1].upper(), r[2].upper(), r[4], r[6]
        if reg in listed:
            found[hexid] = (hexid, reg, typ, desc, owner, "list", listed[reg]["kind"])
        elif typ in types and reg.startswith(prefixes) and any(o in owner.upper() for o in ops):
            found[hexid] = (hexid, reg, typ, desc, owner, "registry", "heli")
        elif typ in ftypes and reg.startswith(prefixes) and any(o in owner.upper() for o in fops):
            found[hexid] = (hexid, reg, typ, desc, owner, "registry", "fixed")
    excluded = {r["ident"] for r in d.execute("SELECT ident FROM hems_aircraft WHERE excluded").fetchall()}
    d.execute("DELETE FROM hems_aircraft WHERE source='registry' AND NOT excluded")
    n_reg = n_list = 0
    for hexid, reg, typ, desc, owner, src, kind in found.values():
        if hexid in excluded or ("reg:" + reg) in excluded:
            continue
        if src == "list":
            d.execute("DELETE FROM hems_aircraft WHERE ident=%s", ("reg:" + reg,))
            d.execute("INSERT INTO hems_aircraft (ident, hex, reg, type, model, owner, source, kind) VALUES (%s,%s,%s,%s,%s,%s,'list',%s) "
                      "ON CONFLICT (ident) DO UPDATE SET reg=EXCLUDED.reg, type=EXCLUDED.type, model=EXCLUDED.model, owner=EXCLUDED.owner", (hexid, hexid, reg, typ, desc, owner, kind))
            n_list += 1
        else:
            d.execute("INSERT INTO hems_aircraft (ident, hex, reg, type, model, owner, source, kind) VALUES (%s,%s,%s,%s,%s,%s,'registry',%s) ON CONFLICT (ident) DO NOTHING",
                      (hexid, hexid, reg, typ, desc, owner, kind))
            n_reg += 1
    d.commit()
    set_setting("hems_last_import", datetime.now(timezone.utc).isoformat())
    set_setting("hems_last_counts", f"{n_reg} found by owner name, {n_list} from the tail number list")
    return {"registry": n_reg, "listed": n_list, "unresolved": counts()["unresolved"]}


def counts():
    r = get_db().execute("SELECT COUNT(*) FILTER (WHERE NOT excluded) AS total, COUNT(*) FILTER (WHERE source='registry' AND NOT excluded) AS registry, "
                         "COUNT(*) FILTER (WHERE source<>'registry' AND NOT excluded) AS listed, COUNT(*) FILTER (WHERE excluded) AS excluded, "
                         "COUNT(*) FILTER (WHERE hex IS NULL AND NOT excluded) AS unresolved, COUNT(*) FILTER (WHERE kind='fixed' AND NOT excluded) AS fixed, "
                         "COUNT(*) FILTER (WHERE kind='heli' AND NOT excluded) AS heli FROM hems_aircraft").fetchone()
    return dict(r)


def status():
    return {"counts": counts(), "last": get_setting("hems_last_import"), "summary": get_setting("hems_last_counts")}


def listing(q="", limit=300):
    like = f"%{q}%"
    where, args = ("WHERE reg ILIKE %s OR owner ILIKE %s OR hex ILIKE %s OR type ILIKE %s", [like] * 4) if q else ("", [])
    d = get_db()
    total = d.execute(f"SELECT COUNT(*) AS n FROM hems_aircraft {where}", args).fetchone()["n"]
    return d.execute(f"SELECT * FROM hems_aircraft {where} ORDER BY excluded, source, owner, reg LIMIT %s", args + [limit]).fetchall(), total


def set_excluded(ident, excluded):
    d = get_db()
    d.execute("UPDATE hems_aircraft SET excluded=%s WHERE ident=%s", (excluded, ident))
    d.commit()


def remove(ident):
    d = get_db()
    row = d.execute("SELECT source FROM hems_aircraft WHERE ident=%s", (ident,)).fetchone()
    if row and row["source"] != "registry":
        d.execute("DELETE FROM hems_aircraft WHERE ident=%s", (ident,))
        d.commit()
        return True
    return False


def label(owner):
    o = re.sub(r"\s+(LLC|INC|CORP|CORPORATION|CO)\.?$", "", (owner or "").strip(), flags=re.I)
    return o.title() if o.isupper() else o


def lookup_table():
    """{hex: operator} and {registration: operator} for the live map."""
    hx, rg, fx, fr = {}, {}, {}, {}
    for r in get_db().execute("SELECT hex, reg, owner, note, kind FROM hems_aircraft WHERE NOT excluded").fetchall():
        who = label(r["owner"]) or r["note"] or ""
        h, g = (fx, fr) if r["kind"] == "fixed" else (hx, rg)
        if r["hex"]:
            h[r["hex"]] = who
        if r["reg"]:
            g[r["reg"].upper()] = who
    return {"hex": hx, "reg": rg, "fw": {"hex": fx, "reg": fr}}


@bp.route("/api/hems.json")
def api_hems():
    resp = jsonify(cached(("hems",), lookup_table))
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp
