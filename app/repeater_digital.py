"""Digital-voice details of a repeater: its talkgroups per time slot, its DMR ID and the other settings a radio needs for DMR, D-STAR, System Fusion, P25 and NXDN (kept in `attrs`), the import of a radio's code plug
(the channel list a programming program exports, for example the DM32's), and the call sign lookup (the FCC's own public license search, kept for a while).

RadioID and RepeaterBook data are NOT copied (their terms, see DATA_SOURCES_TERMS.md); IDs are typed in or come from the files people bring, and the pages link out to RadioID for an ID lookup."""
import csv
import io
import json
import re
import urllib.parse
import urllib.request

from .db import get_db
from .settings import cfg, cfg_int

# key, label, hint: the settings of the other digital modes and the details a radio's code plug needs. Kept together in `attrs` so a new mode needs no new column.
ATTR_KEYS = [
    ("bandwidth", "Bandwidth", "12.5 kHz or 25 kHz"),
    ("tone_rx", "Receive tone", "the tone the repeater needs to hear, when it differs from the one it sends"),
    ("slots", "Time slots in use", "1, 2 or both"),
    ("ipsc", "Network or master", "for example BrandMeister 3101, TGIF, DMR-MARC"),
    ("nac", "P25 NAC", "three hex digits, for example 293"),
    ("ran", "NXDN RAN", "0 to 63"),
    ("dstar_module", "D-STAR module", "A, B or C"),
    ("reflector", "Linked reflector", "for example REF001C or XLX123 A"),
    ("wires_room", "WIRES-X room", "number and name"),
    ("echolink", "EchoLink or AllStar node", "the node number"),
    ("website", "Website", "https://..."),
]
TG_MODES = [("static", "Static (always linked)"), ("dynamic", "Dynamic (key up to link)"), ("timed", "Timed (stays linked for a while)"), ("unknown", "Not known")]
CALLSIGN_RE = re.compile(r"^[A-Z]{1,2}[0-9][A-Z]{1,3}$")


def attrs_from_form(form):
    """JSON text of the filled-in attrs from a submitted form (None for a field the form did not send, so an old form leaves them alone)."""
    out = {}
    for k, _l, _h in ATTR_KEYS:
        v = (form.get("a_" + k) or "").strip()[:120]
        if v:
            if k == "website" and not re.match(r"^https?://", v):
                v = "https://" + v
            out[k] = v
    return json.dumps(out, sort_keys=True)


def attrs_of(r):
    try:
        return json.loads(r["attrs"] or "{}")
    except (ValueError, TypeError, KeyError):
        return {}


# ------------------------------------------------------------------ talkgroups

def talkgroups(rid):
    return get_db().execute("SELECT * FROM repeater_talkgroups WHERE repeater_id=%s ORDER BY slot, tg_id NULLS LAST, name", (rid,)).fetchall()


def parse_talkgroups(text):
    """Lines of 'slot, ID, name, static|dynamic|timed' (the slot may be 1, 2, TS1 or Slot 2 and may be left out; the mode may be left out) -> ([(slot, id, name, mode)], [problems])."""
    out, problems = [], []
    for n, line in enumerate((text or "").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in re.split(r"[,;\t]+", line) if p.strip()]
        slot, mode = 1, "static"
        m = re.fullmatch(r"(?i)(?:ts|slot)\s*([12])|([12])", parts[0]) if len(parts) >= 2 else None
        if m and (len(parts) >= 3 or not parts[1].isdigit()):   # "2, 3105, Name" and "TS2, Name" carry a slot; "3105, Name" does not
            slot = int(m.group(1) or m.group(2)); parts = parts[1:]
        if parts and parts[-1].lower() in dict(TG_MODES) and len(parts) >= 2:
            mode = parts[-1].lower(); parts = parts[:-1]
        tg = None
        if parts and re.fullmatch(r"\d{1,9}", parts[0]):
            tg = int(parts[0]); parts = parts[1:]
        name = " ".join(parts).strip()[:60]
        if tg is None and not name:
            problems.append(f"Line {n}: '{line[:40]}' has no talkgroup number or name.")
            continue
        out.append((slot, tg, name, mode))
    return out, problems


def talkgroups_text(rid):
    return "\n".join(f"{t['slot']}, {t['tg_id'] if t['tg_id'] is not None else ''}, {t['name']}, {t['mode']}" for t in talkgroups(rid))


def set_talkgroups(rid, text, actor):
    """Replace a repeater's talkgroups with the lines of `text`. Returns (count, problems)."""
    from .repeaters import log_edit
    rows, problems = parse_talkgroups(text)
    d = get_db()
    before = talkgroups_text(rid)
    d.execute("DELETE FROM repeater_talkgroups WHERE repeater_id=%s", (rid,))
    seen = set()
    for slot, tg, name, mode in rows:
        key = (slot, tg, name.lower())
        if key in seen:
            continue
        seen.add(key)
        d.execute("INSERT INTO repeater_talkgroups (repeater_id, slot, tg_id, name, mode) VALUES (%s,%s,%s,%s,%s)", (rid, slot, tg, name, mode))
    after = talkgroups_text(rid)
    if before != after:
        log_edit(rid, actor, "talkgroups", f"{len(seen)} talkgroups")
    d.commit()
    return len(seen), problems


# ------------------------------------------------------------------ the radio's code plug

def _num(text):
    try:
        return float(str(text).replace(",", "").strip())
    except ValueError:
        return None


def _band_offset(rx_hz):
    """The usual offset for a repeater whose output is rx_hz (when the file does not say: a radio's channel list often holds the same frequency twice)."""
    mhz = rx_hz / 1e6
    if 144 <= mhz < 148:
        return 600_000 if (mhz >= 147.0 or 146.0 <= mhz < 146.4 or mhz < 145.1) else -600_000
    if 420 <= mhz < 450:
        return 5_000_000
    if 222 <= mhz < 225:
        return -1_600_000
    if 902 <= mhz < 928:
        return -25_000_000
    return 0


def read_channels(text):
    """A radio's channel export (the DM32's CPS columns or the common alternatives) -> a list of dicts: name, rx_hz, tx_hz, digital, slot, cc, contact, rxgroup, tone_tx, tone_rx, bw, power."""
    rdr = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    cols = {re.sub(r"[^a-z0-9]", "", (c or "").lower()): c for c in (rdr.fieldnames or [])}

    def col(row, *names):
        for n in names:
            c = cols.get(re.sub(r"[^a-z0-9]", "", n.lower()))
            if c is not None and row.get(c) is not None:
                return (row[c] or "").strip()
        return ""
    out = []
    for row in rdr:
        name = col(row, "Channel Name", "Name")
        rx = _num(col(row, "RX Frequency[MHz]", "RX Frequency", "Receive Frequency", "Frequency", "Rx Freq"))
        if not name or not rx:
            continue
        tx = _num(col(row, "TX Frequency[MHz]", "TX Frequency", "Transmit Frequency", "Tx Freq")) or rx
        rx_hz = round(rx * 1e6) if rx < 100000 else round(rx)
        tx_hz = round(tx * 1e6) if tx < 100000 else round(tx)
        kind = col(row, "Channel Type", "Mode", "Type").lower()
        slot = re.search(r"([12])", col(row, "Time Slot", "Slot Operation", "Slot")) or None
        out.append({"name": name, "rx_hz": rx_hz, "tx_hz": tx_hz, "digital": kind.startswith(("digital", "dmr", "mixed")) or "dmr" in kind,
                    "slot": int(slot.group(1)) if slot else 1, "cc": col(row, "Color Code", "TX Color Code"), "contact": col(row, "TX Contact", "Contact", "Contact Name"),
                    "rxgroup": col(row, "RX Group List", "Receive Group List"), "tone_tx": col(row, "CTC/DCS Encode", "TX CTCSS/DCS Value", "Tone"), "tone_rx": col(row, "CTC/DCS Decode", "RX CTCSS/DCS Value"),
                    "bw": col(row, "Band Width", "Bandwidth"), "power": col(row, "Power")})
    return out


def _tone(v):
    v = (v or "").strip().upper()
    if re.fullmatch(r"\d{2,3}(\.\d)?", v) and float(v) > 0:
        return v
    return v if re.fullmatch(r"D\d{3}[NI]?", v) else ""


def import_plug(text, contacts_text, label, actor):
    """Bring in what a radio's channel list knows about repeaters: the DMR color code and time slots, the talkgroups (a channel's contact), the tones. A channel on the frequency of a repeater already listed
    adds to that repeater (only what it did not have); a repeater not listed is added (approved: an administrator brought it) without a position, so it is in the list but not on the map until an editor places it. Simplex channels are skipped.
    `contacts_text` (optional): the radio's contact list (name and DMR ID columns), so talkgroup names get their numbers. Returns ({added, updated, skipped, talkgroups}, problems)."""
    from .repeaters import band_of, log_edit, mhz
    label = re.sub(r"[^A-Za-z0-9 ._,'()-]", "", label).strip()[:60] or "radio code plug"
    chans = read_channels(text)
    ids = {}
    if contacts_text:
        crd = csv.DictReader(io.StringIO(contacts_text.lstrip("﻿")))
        cc = {re.sub(r"[^a-z0-9]", "", (c or "").lower()): c for c in (crd.fieldnames or [])}
        ncol = next((cc[k] for k in ("name", "contactname", "contact", "alias") if k in cc), None)
        icol = next((cc[k] for k in ("callid", "dmrid", "id", "tgid", "talkgroup", "radioid", "number") if k in cc), None)
        for r in crd:
            if ncol and icol and (r.get(icol) or "").strip().isdigit():
                ids[(r[ncol] or "").strip().lower()] = int(r[icol].strip())
    d = get_db()
    groups = {}
    problems = []
    skipped = 0
    for c in chans:
        if band_of(c["rx_hz"]) == "Other":
            skipped += 1
            continue
        call = next((t for t in re.split(r"[ _/-]+", c["name"].upper()) if CALLSIGN_RE.match(t)), "")
        if not call:
            skipped += 1   # a channel not named after a repeater's call sign (simplex, a calling channel, a service) is not taken
            continue
        groups.setdefault((c["rx_hz"] // 5000, call), []).append(c)
    counts = {"added": 0, "updated": 0, "skipped": skipped, "talkgroups": 0}
    for (_k, call), rows in groups.items():
        rx = rows[0]["rx_hz"]
        digital = any(r["digital"] for r in rows)
        match = d.execute("SELECT * FROM repeaters WHERE abs(freq_hz - %s) < 5000 AND (callsign = %s OR callsign = '') ORDER BY (callsign = %s) DESC LIMIT 1", (rx, call, call)).fetchone()
        tones = [t for t in (_tone(r["tone_tx"]) for r in rows if not r["digital"]) if t]
        ccode = next((r["cc"] for r in rows if r["digital"] and r["cc"].isdigit()), "")
        if match is None:
            off = rows[0]["tx_hz"] - rx if abs(rows[0]["tx_hz"] - rx) >= 100_000 else _band_offset(rx)
            rid = d.execute("INSERT INTO repeaters (callsign, freq_hz, offset_hz, tone, modes, color_code, kind, description, status, source, source_id, approved, edited, submitted_by) "
                            "VALUES (%s,%s,%s,%s,%s,%s,'repeater',%s,'unverified',%s,%s,TRUE,TRUE,NULL) RETURNING id",
                            (call, rx, off, tones[0] if tones else "", "DMR" if digital else "FM", ccode,
                             "From a radio code plug (" + label + "): the position is not known yet" + ("; the offset is the usual one for the band" if abs(rows[0]["tx_hz"] - rx) < 100_000 else ""), "plug:" + label, f"{label}:{call}:{rx}")).fetchone()["id"]
            log_edit(rid, actor, "imported", f"{mhz(rx)} MHz {call} from {label}")
            counts["added"] += 1
        else:
            rid = match["id"]
            sets, changes = {}, []
            if digital and "DMR" not in (match["modes"] or "").split(","):
                sets["modes"] = ",".join(sorted(set((match["modes"] or "FM").split(",")) | {"DMR"})); changes.append("DMR added")
            if ccode and not match["color_code"]:
                sets["color_code"] = ccode; changes.append("color code " + ccode)
            if tones and not match["tone"]:
                sets["tone"] = tones[0]; changes.append("tone " + tones[0])
            if not match["callsign"]:
                sets["callsign"] = call
            if sets:
                d.execute("UPDATE repeaters SET " + ",".join(f"{k}=%({k})s" for k in sets) + ", updated=now() WHERE id=%(id)s", dict(sets, id=rid))
                log_edit(rid, actor, "imported", f"from {label}: " + ", ".join(changes))
                counts["updated"] += 1
        have = {(t["slot"], (t["name"] or "").lower()) for t in talkgroups(rid)}
        for r in rows:
            if not r["digital"] or not r["contact"] or r["contact"].lower() in ("none", ""):
                continue
            tg = ids.get(r["contact"].lower())
            if tg is None:
                from . import dmrlists
                tg = dmrlists.id_for_name(r["contact"])   # the kept talkgroup list (Admin > Repeaters, or the scheduled update)
            if tg is None and r["contact"].isdigit():
                tg = int(r["contact"])
            if (r["slot"], r["contact"].lower()) in have:
                continue
            have.add((r["slot"], r["contact"].lower()))
            d.execute("INSERT INTO repeater_talkgroups (repeater_id, slot, tg_id, name, mode) VALUES (%s,%s,%s,%s,'unknown')", (rid, r["slot"], tg, r["contact"][:60]))
            counts["talkgroups"] += 1
    d.commit()
    return counts, problems


# ------------------------------------------------------------------ call sign lookup (Callook.info, which republishes the FCC's public license data)

def callsign_info(call):
    """What the FCC's public license data says about a US call sign (through Callook.info): {call, name, status, kind, oper_class, place, expires, link}; None when none is found. Only the licensee's name, class,
    status, expiry and city and state are kept and shown, never the street address. Answers are kept for Settings > Keep Call Sign Answers, so the service is asked once."""
    call = re.sub(r"[^A-Za-z0-9]", "", call or "").upper()
    if not re.fullmatch(r"[A-Z0-9]{3,7}", call) or cfg("callsign_lookup_enabled") != "1":
        return None
    d = get_db()
    row = d.execute("SELECT data FROM callsign_cache WHERE call=%s AND fetched > now() - make_interval(days => %s)", (call, cfg_int("callsign_cache_days"))).fetchone()
    if row:
        return json.loads(row["data"]) or None
    out = {}
    try:
        url = cfg("callsign_lookup_url").rstrip("/") + "/" + urllib.parse.quote(call) + "/json"
        req = urllib.request.Request(url, headers={"User-Agent": cfg("lookup_user_agent"), "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=cfg_int("lookup_timeout_seconds") + 4) as r:
            j = json.loads(r.read(200_000).decode("utf-8", "replace"))
        if j.get("status") in ("VALID", "UPDATING") and j.get("name"):
            line2 = ((j.get("address") or {}).get("line2") or "")
            m = re.match(r"^(.*?),\s*([A-Z]{2})", line2)
            out = {"call": call, "name": j.get("name", "").title() if j.get("name", "").isupper() else j.get("name", ""), "status": j.get("status", "").title(), "kind": (j.get("type") or "").title(),
                   "oper_class": ((j.get("current") or {}).get("operClass") or "").title(), "place": (m.group(1).title() + ", " + m.group(2)) if m else "",
                   "expires": (j.get("otherInfo") or {}).get("expiryDate", ""), "link": (j.get("otherInfo") or {}).get("ulsUrl", ""),
                   "granted": (j.get("otherInfo") or {}).get("grantDate", ""), "previous": ((j.get("previous") or {}).get("callsign") or "").upper(), "trustee": ((j.get("trustee") or {}).get("callsign") or "").upper(),
                   "grid": ((j.get("location") or {}).get("gridsquare") or "")[:4].upper()}   # a four character grid square only (about 100 by 70 miles): the exact spot is a home address
    except Exception:
        return None   # the service did not answer: nothing is kept, so the next look tries again
    d.execute("INSERT INTO callsign_cache (call, data, fetched) VALUES (%s,%s,now()) ON CONFLICT (call) DO UPDATE SET data=EXCLUDED.data, fetched=now()", (call, json.dumps(out)))
    d.commit()
    return out or None


def portal_matches(call, signed_in=False):
    """Everything this site knows about a call sign, as [{kind, label, href, sub}]: repeaters (and their DMR IDs and color codes), digital IDs from the contact list (signed-in members only), APRS stations
    (the call sign with any -SSID), the owner's public ADS-B feeders, weather stations and sonde receiving stations."""
    call = re.sub(r"[^A-Za-z0-9]", "", call or "").upper()
    if not call:
        return []
    d, out = get_db(), []
    for r in d.execute("SELECT id, freq_hz, city, modes, dmr_id, color_code, network FROM repeaters WHERE upper(callsign)=%s AND approved ORDER BY freq_hz", (call,)).fetchall():
        sub = ", ".join(x for x in (r["modes"].replace(",", ", ") if r["modes"] else "", ("color code " + str(r["color_code"])) if r["color_code"] else "", r["network"] or "") if x)
        out.append({"kind": "Repeater", "label": "%.4f MHz%s" % (r["freq_hz"] / 1e6, (", " + r["city"]) if r["city"] else ""), "href": "/repeaters/%d" % r["id"], "sub": sub})
        if r["dmr_id"]:
            out.append({"kind": "Digital ID", "label": "DMR repeater ID %s" % r["dmr_id"], "href": "/repeaters/%d#rp-dmr" % r["id"], "sub": "the repeater on %.4f MHz" % (r["freq_hz"] / 1e6)})
    if signed_in:
        for c in d.execute("SELECT id, name, city, state FROM dmr_contacts WHERE upper(call) = %s OR upper(name) = %s OR upper(name) LIKE %s ORDER BY id LIMIT 12", (call, call, call + " %")).fetchall():
            out.append({"kind": "Digital ID", "label": "DMR ID %s" % c["id"], "href": "/callsign?tab=contacts&q=%d" % c["id"], "sub": ", ".join(x for x in (c["name"], c["city"], c["state"]) if x) + " (contact list)"})
    try:
        from . import aprs as ap
        if ap.enabled():
            for st in ap.stations(q=call, limit=40):
                c = (st.get("call") or "").upper()
                if c == call or c.startswith(call + "-"):
                    out.append({"kind": "APRS station", "label": c, "href": "/aprs/" + c.replace("/", "~"), "sub": str(st.get("kind") or "")})
    except Exception:
        pass
    for r in d.execute("SELECT f.station_id, f.name FROM feeders f JOIN users u ON u.id = f.owner_id WHERE upper(u.callsign) = %s AND f.station_id IS NOT NULL AND f.station_id <> '' AND NOT f.banned ORDER BY f.station_id", (call,)).fetchall():
        out.append({"kind": "ADS-B feeder", "label": r["station_id"], "href": "/feeders/" + r["station_id"], "sub": r["name"] or ""})
    try:
        from . import weather as wx
        for r in d.execute(wx.SELECT + "WHERE " + wx.PUBLIC_WHERE + " AND upper(u.callsign) = %s ORDER BY 1", (call,)).fetchall():
            out.append({"kind": "Weather station", "label": r["sid"], "href": "/weather/" + r["sid"], "sub": r["name"] or r["fname"] or ""})
    except Exception:
        pass
    for r in d.execute("SELECT callsign, last_seen FROM sonde_stations WHERE public AND NOT disabled AND upper(split_part(callsign, '-', 1))=%s ORDER BY callsign", (call,)).fetchall():
        out.append({"kind": "Sonde receiving station", "label": r["callsign"], "href": "/sondes/stations/" + r["callsign"].replace("/", "~"), "sub": ""})
    return out


def portal_prefix(prefix, signed_in=False, limit=80):
    """Every call sign on this site that starts with what was typed (KD5 finds KD5VCF, KD5VC, KD5NDU-15...), as [{call, kinds}] in call sign order: repeaters, APRS stations, owners of public feeders and weather
    stations, sonde receiving stations, and the DMR contact list for signed-in members. The text is a start of a call sign: letters and digits only, two characters or more."""
    p = re.sub(r"[^A-Za-z0-9]", "", prefix or "").upper()
    if len(p) < 2:
        return []
    d, found = get_db(), {}
    def add(call, kind):
        base = (call or "").upper().strip()
        if base and base.split("-")[0].startswith(p):
            found.setdefault(base, set()).add(kind)
    for r in d.execute("SELECT DISTINCT upper(callsign) AS c FROM repeaters WHERE approved AND upper(callsign) LIKE %s LIMIT 200", (p + "%",)).fetchall():
        add(r["c"], "Repeater")
    try:
        from . import aprs as ap
        if ap.enabled():
            for st in ap.stations(q=p, limit=200):
                add(st.get("call"), "APRS station")
    except Exception:
        pass
    for r in d.execute("SELECT DISTINCT upper(u.callsign) AS c FROM users u WHERE upper(u.callsign) LIKE %s AND (EXISTS (SELECT 1 FROM feeders f WHERE f.owner_id = u.id AND NOT f.banned AND f.station_id <> '') OR EXISTS (SELECT 1 FROM wx_stations w WHERE w.owner_id = u.id)) LIMIT 200", (p + "%",)).fetchall():
        add(r["c"], "Station owner")
    for r in d.execute("SELECT DISTINCT upper(split_part(callsign, '-', 1)) AS c FROM sonde_stations WHERE public AND NOT disabled AND upper(callsign) LIKE %s LIMIT 200", (p + "%",)).fetchall():
        add(r["c"], "Sonde receiving station")
    if signed_in:
        for r in d.execute("SELECT DISTINCT upper(call) AS c FROM dmr_contacts WHERE upper(call) LIKE %s LIMIT 200", (p + "%",)).fetchall():
            add(r["c"], "DMR contact")
    return [{"call": c, "kinds": sorted(k)} for c, k in sorted(found.items())[:limit]]
