"""Interesting aircraft: our own editable list that feeds the live map's Interesting flag (value 2) next to the one tar1090's database carries.

Entries come three ways: added by hand (Admin > Interesting Aircraft), imported from a CSV an administrator pastes or uploads (hex, registration, type,
category, note), and imported from the Plane-Alert database (sdr-enthusiasts/plane-alert-db, ODbL: the same list Planefence's Plane-Alert uses).
A Plane-Alert refresh replaces only the entries it brought (source 'planealert'); hand and CSV entries are never touched by it, and an entry an
administrator excluded stays excluded. Settings can limit the Plane-Alert import to chosen categories."""
import csv
import io
import re
import urllib.request
from datetime import datetime, timezone

from flask import Blueprint, Response, jsonify

from .cache import cached
from .db import get_db
from .settings import cfg, get_setting, set_setting

bp = Blueprint("interesting", __name__)
HEX = re.compile(r"^[0-9a-f]{6}$")
SOURCES = {"manual": "added by hand", "csv": "CSV file", "planealert": "Plane-Alert"}
COLUMN_WORDS = {"hex": "hex", "icao": "hex", "$icao": "hex", "icao24": "hex", "reg": "reg", "registration": "reg", "$registration": "reg", "tail": "reg", "type": "type",
                "icao type": "type", "$icao type": "type", "typecode": "type", "category": "category", "cat": "category", "note": "note", "notes": "note", "owner": "owner",
                "operator": "owner", "$operator": "owner", "link": "link", "$#link": "link", "url": "link"}


def _get(url, limit=30_000_000):
    req = urllib.request.Request(url, headers={"User-Agent": cfg("lookup_user_agent")})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read(limit)


def _clean(row):
    hexid = (row.get("hex") or "").strip().lower().lstrip("~")
    if not HEX.match(hexid):
        return None
    return {"hex": hexid, "reg": (row.get("reg") or "").strip().upper()[:12], "type": (row.get("type") or "").strip().upper()[:8], "owner": (row.get("owner") or "").strip()[:120],
            "category": (row.get("category") or "").strip()[:60], "note": (row.get("note") or "").strip()[:300], "link": (row.get("link") or "").strip()[:300]}


def parse_csv(text):
    """CSV with a header line naming at least a hex column (hex, icao or icao24) -> (entries, skipped line numbers). Other columns are optional."""
    rows = list(csv.reader(io.StringIO((text or "").lstrip("﻿"))))
    if not rows:
        raise ValueError("The file is empty.")
    names = [COLUMN_WORDS.get(c.strip().lower()) for c in rows[0]]
    if "hex" not in names:
        raise ValueError("The first line must name the columns; one of them must be hex (or icao). Optional: registration, type, category, note, owner, link.")
    out, skipped = {}, []
    for n, r in enumerate(rows[1:], 2):
        d = {}
        for name, val in zip(names, r):
            if name and name not in d:
                d[name] = val
        e = _clean(d)
        if e is None:
            if any(c.strip() for c in r):
                skipped.append(n)
            continue
        out[e["hex"]] = e
    return list(out.values()), skipped


def csv_template():
    return "hex,registration,type,category,note,owner,link\nae0123,,C17,Military,Example entry,US Air Force,\n"


def import_entries(entries, source):
    """Add entries from a file or Plane-Alert. Existing hand entries win over a CSV and a CSV over Plane-Alert; excluded hexes stay out.
    Returns (added, updated, left_alone)."""
    d = get_db()
    excluded = {r["hex"] for r in d.execute("SELECT hex FROM interesting_aircraft WHERE excluded").fetchall()}
    rank = {"manual": 3, "csv": 2, "planealert": 1}
    have = {r["hex"]: r["source"] for r in d.execute("SELECT hex, source FROM interesting_aircraft").fetchall()}
    added = updated = left = 0
    for e in entries:
        if e["hex"] in excluded or rank.get(have.get(e["hex"], ""), 0) > rank[source]:
            left += 1
            continue
        d.execute("INSERT INTO interesting_aircraft (hex, reg, type, owner, category, note, link, source) VALUES (%(hex)s,%(reg)s,%(type)s,%(owner)s,%(category)s,%(note)s,%(link)s,%(source)s) "
                  "ON CONFLICT (hex) DO UPDATE SET reg=EXCLUDED.reg, type=EXCLUDED.type, owner=EXCLUDED.owner, category=EXCLUDED.category, note=EXCLUDED.note, link=EXCLUDED.link, "
                  "source=EXCLUDED.source", dict(e, source=source))
        if e["hex"] in have:
            updated += 1
        else:
            added += 1
    d.commit()
    return added, updated, left


def add_entry(hexid, reg, typ, category, note, owner=""):
    e = _clean({"hex": hexid, "reg": reg, "type": typ, "category": category, "note": note, "owner": owner})
    if e is None:
        return "A hex code is six letters or digits, for example ae0123."
    d = get_db()
    d.execute("INSERT INTO interesting_aircraft (hex, reg, type, owner, category, note, link, source) VALUES (%(hex)s,%(reg)s,%(type)s,%(owner)s,%(category)s,%(note)s,%(link)s,'manual') "
              "ON CONFLICT (hex) DO UPDATE SET reg=EXCLUDED.reg, type=EXCLUDED.type, owner=EXCLUDED.owner, category=EXCLUDED.category, note=EXCLUDED.note, source='manual', excluded=FALSE", e)
    d.commit()
    return ""


def planealert_rows(text):
    """Plane-Alert's CSV ($ICAO, $Registration, $Operator, $Type, $ICAO Type, #CMPG, tags, Category, $#Link) -> entries, limited to the categories in Settings."""
    wanted = {c.strip().lower() for c in cfg("interesting_pa_categories").split(",") if c.strip()}
    entries, _ = parse_csv(text)
    out = []
    for e in entries:
        if not wanted or e["category"].lower() in wanted:
            out.append(e)
    return out


def import_planealert(text=None):
    """Refresh the Plane-Alert entries: the ones it brought before are replaced, nothing else changes. Returns a counts dict."""
    text = text if text is not None else _get(cfg("plane_alert_url")).decode("utf-8", "replace")
    allrows, _ = parse_csv(text)
    cats = {}
    for e in allrows:
        cats[e["category"]] = cats.get(e["category"], 0) + 1
    set_setting("interesting_pa_seen", ";".join(f"{k}|{v}" for k, v in sorted(cats.items(), key=lambda x: -x[1]) if k))
    entries = planealert_rows(text)
    d = get_db()
    d.execute("DELETE FROM interesting_aircraft WHERE source='planealert' AND NOT excluded")
    added, updated, left = import_entries(entries, "planealert")
    set_setting("interesting_last_import", datetime.now(timezone.utc).isoformat())
    set_setting("interesting_last_counts", f"{added + updated} from Plane-Alert ({len(allrows)} in its list), {left} left alone")
    return {"added": added + updated, "total": len(allrows), "left": left}


def categories_seen():
    out = []
    for part in (get_setting("interesting_pa_seen") or "").split(";"):
        if "|" in part:
            k, v = part.rsplit("|", 1)
            out.append((k, int(v)))
    return out


def counts():
    r = get_db().execute("SELECT COUNT(*) FILTER (WHERE NOT excluded) AS total, COUNT(*) FILTER (WHERE source='planealert' AND NOT excluded) AS planealert, "
                         "COUNT(*) FILTER (WHERE source IN ('manual','csv') AND NOT excluded) AS yours, COUNT(*) FILTER (WHERE excluded) AS excluded FROM interesting_aircraft").fetchone()
    return dict(r)


def status():
    return {"counts": counts(), "last": get_setting("interesting_last_import"), "summary": get_setting("interesting_last_counts"), "categories": categories_seen()}


def listing(q="", category="", limit=300):
    where, args = [], []
    if q:
        where.append("(hex ILIKE %s OR reg ILIKE %s OR owner ILIKE %s OR type ILIKE %s OR note ILIKE %s)"); args += [f"%{q}%"] * 5
    if category:
        where.append("category = %s"); args.append(category)
    w = ("WHERE " + " AND ".join(where)) if where else ""
    d = get_db()
    total = d.execute(f"SELECT COUNT(*) AS n FROM interesting_aircraft {w}", args).fetchone()["n"]
    return d.execute(f"SELECT * FROM interesting_aircraft {w} ORDER BY excluded, (source='planealert'), category, reg, hex LIMIT %s", args + [limit]).fetchall(), total


def set_excluded(hexid, excluded):
    d = get_db()
    d.execute("UPDATE interesting_aircraft SET excluded=%s WHERE hex=%s", (excluded, hexid))
    d.commit()


def remove(hexid):
    d = get_db()
    row = d.execute("SELECT source FROM interesting_aircraft WHERE hex=%s", (hexid,)).fetchone()
    if row and row["source"] != "planealert":
        d.execute("DELETE FROM interesting_aircraft WHERE hex=%s", (hexid,))
        d.commit()
        return True
    return False


def lookup_table():
    """{hex: [category, note]} for the live map (excluded entries left out)."""
    out = {}
    for r in get_db().execute("SELECT hex, category, note, owner FROM interesting_aircraft WHERE NOT excluded").fetchall():
        out[r["hex"]] = [r["category"], r["note"] or r["owner"]]
    return {"hex": out}


PA_HEADER = "$ICAO,$Registration,$Operator,$Type,$ICAO Type,#CMPG,$Tag 1,$#Tag 2,$#Tag 3,Category,$#Link"


def plane_alert_csv():
    """Our Interesting Aircraft list in Plane-Alert's own file format, so the Plane-Alert service can read it as its alert list (its PF_ALERTLIST setting takes an address):
    the list is then kept in one place, Admin > Interesting Aircraft, with the categories chosen in Settings, instead of a second copy of the whole worldwide list."""
    import csv as _csv
    import io as _io
    out = _io.StringIO()
    w = _csv.writer(out, lineterminator="\n")
    out.write(PA_HEADER + "\n")
    for r in get_db().execute("SELECT hex, reg, owner, type, category, note, link FROM interesting_aircraft WHERE NOT excluded ORDER BY hex").fetchall():
        mil = "Mil" if "mil" in (r["category"] or "").lower() else ""
        w.writerow([r["hex"].upper(), r["reg"], r["owner"], r["note"], r["type"], mil, "", "", "", r["category"], r["link"]])
    return out.getvalue()


@bp.route("/api/plane-alert-list.csv")
def api_plane_alert_list():
    resp = Response(cached(("pa_csv",), plane_alert_csv), mimetype="text/csv")
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp


@bp.route("/api/interesting.json")
def api_interesting():
    resp = jsonify(cached(("interesting",), lookup_table))
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp
