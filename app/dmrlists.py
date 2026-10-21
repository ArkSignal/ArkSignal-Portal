"""DMR talkgroup and contact lists: the numbers behind the names a repeater, a radio's code plug and a member's talkgroup list use.

Lists come from a file an administrator brings (a radio's exported Talkgroups and Digital Contacts CSV, or a RadioID-style user list, or a BrandMeister-style talkgroup list) and, only when an
administrator has put an address in Settings > Repeaters and switched the schedule on, from that address on a schedule (Data Updates). Nothing is fetched from RadioID or BrandMeister until
then: their terms ask for written permission first (DATA_SOURCES_TERMS.md). Tables: dmr_talkgroups (id, name, kind) and dmr_contacts (id, name, city, state, country, kind)."""
import csv
import io
import json
import re
import urllib.request

from .db import get_db
from .settings import cfg, cfg_int

MAX_BYTES = 40_000_000
TG_MODES = [("static", "Static"), ("dynamic", "Dynamic"), ("timed", "Timed"), ("unknown", "Not stated")]


def _rows(text):
    return list(csv.DictReader(io.StringIO((text or "").lstrip("﻿"))))


def _pick(row, *names):
    low = {(k or "").strip().lower().replace(" ", "_"): (v or "").strip() for k, v in row.items() if k}
    for n in names:
        if low.get(n):
            return low[n]
    return ""


def parse_talkgroups(text):
    """A talkgroup list -> [(id, name, kind)]. Reads a radio's export (columns No., Name, ID, Type), a plain CSV with ID and Name, a BrandMeister-style JSON ({"310": "US Nationwide"} or a list of {id, name})."""
    text = (text or "").strip()
    out = {}
    if text[:1] in "{[":
        try:
            j = json.loads(text)
        except ValueError:
            return []
        items = j.items() if isinstance(j, dict) else [(x.get("id") or x.get("ID") or x.get("tg"), x.get("name") or x.get("Name")) for x in j if isinstance(x, dict)]
        for k, v in items:
            if str(k).isdigit() and v:
                out[int(k)] = (str(v).strip()[:60], "Group Call")
    else:
        for r in _rows(text):
            i = _pick(r, "id", "radio_id", "tg", "talkgroup", "talkgroup_id")
            n = _pick(r, "name", "talkgroup_name", "description")
            if i.isdigit() and n:
                out[int(i)] = (n[:60], _pick(r, "type", "kind") or "Group Call")
    return [(i, n, k) for i, (n, k) in sorted(out.items())]


CALL_RE = re.compile(r"^[A-Z0-9]{1,3}[0-9][A-Z0-9]{0,3}[A-Z]$")


def parse_contacts(text):
    """A contact list -> [(id, name, city, state, country, kind, call, remark)]. Reads a radio's export (ID, Repeater, Name, City, Province, Country, Remark, Type: RadioID-made lists put the call sign in
    Repeater, and sometimes in Remark) or a RadioID-style user list (RADIO_ID, CALLSIGN, FIRST_NAME, LAST_NAME, CITY, STATE, COUNTRY). The call sign is kept apart from the name."""
    out = {}
    for r in _rows(text):
        i = _pick(r, "dmr_id", "radio_id", "call_id", "id")   # "DMR ID" first: in a list with a row number column called ID, that column is not the DMR ID
        if not i.isdigit():
            continue
        name = _pick(r, "name", "first_name", "firstname")
        remark = _pick(r, "remark", "last_name", "lastname")
        call = ""
        for cand in (_pick(r, "callsign", "call_sign", "call"), _pick(r, "repeater"), remark, name):
            c = re.sub(r"[^A-Za-z0-9]", "", cand.split(" ")[0] if cand else "").upper()
            if c and CALL_RE.match(c) and any(ch.isalpha() for ch in c):
                call = c
                break
        if call and remark.upper().split(" ")[0].replace("-", "") == call:
            remark = ""
        if not name and not call:
            continue
        out[int(i)] = ((name or call)[:60], _pick(r, "city")[:40], (_pick(r, "state", "province"))[:30], _pick(r, "country")[:40], _pick(r, "type", "kind") or "Private Call", call[:10], remark[:60])
    return [(i,) + v for i, v in sorted(out.items())]


def import_talkgroups(text, source="file"):
    rows = parse_talkgroups(text)
    d = get_db()
    for i, n, k in rows:
        d.execute("INSERT INTO dmr_talkgroups (id, name, kind, source, updated) VALUES (%s,%s,%s,%s,now()) ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, kind=EXCLUDED.kind, source=EXCLUDED.source, updated=now()", (i, n, k, source))
    d.commit()
    return len(rows)


def import_contacts(text, source="file", replace=False):
    rows = parse_contacts(text)
    d = get_db()
    if replace:
        d.execute("DELETE FROM dmr_contacts")
    with d.cursor() as cur:
        cur.executemany("INSERT INTO dmr_contacts (id, name, city, state, country, kind, call, remark, source, updated) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,now()) ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, city=EXCLUDED.city, "
                        "state=EXCLUDED.state, country=EXCLUDED.country, kind=EXCLUDED.kind, call=EXCLUDED.call, remark=EXCLUDED.remark, source=EXCLUDED.source, updated=now()", [r + (source,) for r in rows])
    d.commit()
    return len(rows)


def counts():
    d = get_db()
    return {"talkgroups": d.execute("SELECT count(*) AS n FROM dmr_talkgroups").fetchone()["n"], "contacts": d.execute("SELECT count(*) AS n FROM dmr_contacts").fetchone()["n"]}


def id_for_name(name):
    """The talkgroup number a name stands for in the kept list (an exact name, case ignored), or None."""
    r = get_db().execute("SELECT id FROM dmr_talkgroups WHERE lower(name)=lower(%s) ORDER BY id LIMIT 1", ((name or "").strip(),)).fetchone()
    return r["id"] if r else None


def name_for_id(tg):
    r = get_db().execute("SELECT name FROM dmr_talkgroups WHERE id=%s", (tg,)).fetchone()
    return r["name"] if r else ""


def _fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": cfg("lookup_user_agent"), "Accept": "text/csv, application/json, text/plain"})
    with urllib.request.urlopen(req, timeout=cfg_int("lookup_timeout_seconds") + 60) as r:
        return r.read(MAX_BYTES).decode("utf-8-sig", "replace")


def refresh():
    """The scheduled update: reads the addresses in Settings > Repeaters (a blank address is skipped). Returns a one-line result."""
    parts = []
    tu, cu = cfg("dmr_talkgroups_url").strip(), cfg("dmr_contacts_url").strip()
    if not tu and not cu:
        return "No address is set under Settings > Repeaters, nothing to fetch (lists can be imported from a file on the Repeaters admin page)"
    if tu:
        parts.append(f"{import_talkgroups(_fetch(tu), 'url')} talkgroups")
    if cu:
        parts.append(f"{import_contacts(_fetch(cu), 'url')} contacts")
    return ", ".join(parts)


# The widely used, pre-defined talkgroups every DMR network shares (BrandMeister and DMR-MARC numbering). They are always offered when talkgroups are added to a repeater; a list an administrator
# imports adds to them (and its name wins when both carry a number).
STANDARD = [
    (9, "Local", "Stays on this repeater only (the usual local talkgroup, usually time slot 2)"),
    (91, "Worldwide", "All networks, worldwide"),
    (92, "Europe", "Europe"),
    (93, "North America", "North America"),
    (310, "TAC 310", "Tactical talkgroup 310 (a general purpose, linked on demand)"),
    (311, "TAC 311", "Tactical talkgroup 311"),
    (312, "TAC 312", "Tactical talkgroup 312"),
    (3100, "USA Nationwide", "The nationwide United States talkgroup"),
    (3105, "Arkansas", "Arkansas statewide (state talkgroups are 31 plus the state's number)"),
    (4000, "Disconnect", "Key up to unlink the dynamic talkgroups on this repeater"),
    (5000, "Status", "Key up to hear the repeater's linked talkgroups read out"),
    (9990, "Parrot", "Echo test: it plays back what you said"),
]


def catalog():
    """Every talkgroup that can be picked: [{id, name, kind: Standard|Imported, note}], standard ones first, an imported name replacing the standard one of the same number."""
    got = {r["id"]: r for r in get_db().execute("SELECT id, name, kind FROM dmr_talkgroups ORDER BY id").fetchall()}
    out, seen = [], set()
    for i, n, note in STANDARD:
        out.append({"id": i, "name": got[i]["name"] if i in got else n, "kind": "Standard", "note": note}); seen.add(i)
    for i, r in got.items():
        if i not in seen:
            out.append({"id": i, "name": r["name"], "kind": "Imported", "note": ""})
    return out


def carried_by():
    """{talkgroup id: [(repeater id, label)]} for the approved repeaters that list it."""
    out = {}
    for r in get_db().execute("SELECT t.tg_id, r.id, r.callsign, r.freq_hz, t.slot FROM repeater_talkgroups t JOIN repeaters r ON r.id = t.repeater_id WHERE r.approved AND t.tg_id IS NOT NULL ORDER BY r.callsign").fetchall():
        out.setdefault(r["tg_id"], []).append((r["id"], f"{r['callsign'] or 'Repeater'} {r['freq_hz'] / 1e6:.3f} (slot {r['slot']})"))
    return out


def add_talkgroup(rid, slot, tg_id, name, mode, actor):
    """One talkgroup on a repeater's time slot; a number already there on that slot is updated. Returns an error text or ''."""
    from .repeaters import log_edit
    if slot not in (1, 2):
        return "Choose time slot 1 or 2."
    if mode not in dict(TG_MODES):
        mode = "static"
    name = (name or "").strip()[:60]
    if tg_id is not None:
        name = name or name_for_id(tg_id) or next((n for i, n, _x in STANDARD if i == tg_id), "")
    if tg_id is None and not name:
        return "Choose a talkgroup or give a name."
    d = get_db()
    row = d.execute("SELECT id FROM repeater_talkgroups WHERE repeater_id=%s AND slot=%s AND tg_id IS NOT DISTINCT FROM %s AND (tg_id IS NOT NULL OR lower(name)=lower(%s))", (rid, slot, tg_id, name)).fetchone()
    if row:
        d.execute("UPDATE repeater_talkgroups SET name=%s, mode=%s WHERE id=%s", (name, mode, row["id"]))
    else:
        d.execute("INSERT INTO repeater_talkgroups (repeater_id, slot, tg_id, name, mode) VALUES (%s,%s,%s,%s,%s)", (rid, slot, tg_id, name, mode))
    log_edit(rid, actor, "talkgroups", f"slot {slot}: {tg_id or ''} {name}")
    d.commit()
    return ""


def remove_talkgroup(rid, row_id, actor):
    from .repeaters import log_edit
    d = get_db()
    r = d.execute("DELETE FROM repeater_talkgroups WHERE id=%s AND repeater_id=%s RETURNING slot, tg_id, name", (row_id, rid)).fetchone()
    if r:
        log_edit(rid, actor, "talkgroups", f"removed slot {r['slot']}: {r['tg_id'] or ''} {r['name']}")
    d.commit()
    return bool(r)
