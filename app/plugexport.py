"""Radio code plugs out of the repeater directory: the Anytone-style DM32 CPS channel list (the format `repeater_digital.read_channels` reads in), its talkgroup (digital contact) list, a CHIRP CSV and
a generic RT Systems style CSV. The repeaters come from the same list the pages show (approved, with the filters below); a DMR repeater becomes one channel for each of its talkgroups.
Nothing here is a bulk copy of an outside list: it is the portal's own directory, in the shape a radio's programming program reads."""
import csv
import io
import re

from .db import get_db
from .repeaters import band_of, mhz
from .settings import cfg_int

# the DM32 CPS channel columns, in the order its export has them (checked against a real export)
DM32_COLUMNS = ["No.", "Channel Name", "Channel Type", "RX Frequency[MHz]", "TX Frequency[MHz]", "Power", "Band Width", "Scan List", "TX Admit", "Emergency System", "Squelch Level", "APRS Report Type",
                "Forbid TX", "APRS Receive", "Forbid Talkaround", "Auto Scan", "Lone Work", "Emergency Indicator", "Emergency ACK", "Analog APRS PTT Mode", "Digital APRS PTT Mode", "TX Contact",
                "RX Group List", "Color Code", "Time Slot", "Encryption", "Encryption ID", "APRS Report Channel", "Direct Dual Mode", "Private Confirm", "Short Data Confirm", "DMR ID", "CTC/DCS Decode",
                "CTC/DCS Encode", "Scramble", "RX Squelch Mode", "Signaling Type", "PTT ID", "VOX Function", "PTT ID Display"]
TG_COLUMNS = ["No.", "Name", "ID", "Type"]
CHIRP_COLUMNS = ["Location", "Name", "Frequency", "Duplex", "Offset", "Tone", "rToneFreq", "cToneFreq", "DtcsCode", "DtcsPolarity", "RxDtcsCode", "CrossMode", "Mode", "TStep", "Skip", "Power", "Comment",
                 "URCALL", "RPT1CALL", "RPT2CALL", "DVCODE"]
RT_COLUMNS = ["Receive Frequency", "Transmit Frequency", "Offset Frequency", "Offset Direction", "Operating Mode", "Name", "Show Name", "Tone Mode", "CTCSS", "Rx CTCSS", "DCS", "Rx DCS", "DCS Polarity",
              "Tx Power", "Skip", "Step", "Comment"]
FORMATS = {"dm32": "Anytone DM32 (channel list, talkgroups)", "chirp": "CHIRP (any radio it supports)", "rt": "RT Systems style CSV (check the column mapping when you import)"}

# what each DM32 column gets: from the directory, a fixed choice, or the same as a typical analog or digital channel in a real export (this is the field-by-field comparison shown on the page)
COMPARISON = [
    ("Channel Name", "The call sign and the city, cut to 16 characters; a DMR channel adds the slot and the talkgroup", "directory"),
    ("Channel Type", "Analog for FM, Digital for DMR", "directory"),
    ("RX Frequency[MHz]", "The repeater's output frequency", "directory"),
    ("TX Frequency[MHz]", "The input frequency: the output plus the offset (the same as the output for simplex)", "directory"),
    ("Power", "High (Low if you choose it on the page)", "your choice"),
    ("Band Width", "25KHz for FM (12.5KHz if you choose it); 25KHz for DMR, which is what the radio's own export carries for a digital channel", "your choice"),
    ("Scan List", "None", "fixed"),
    ("TX Admit", "Allow TX for FM and Always for DMR (the values a real export carries)", "fixed"),
    ("Emergency System, Squelch Level, APRS columns, Forbid TX, Auto Scan, Lone Work, Encryption", "The values a normal channel carries in a real export", "fixed"),
    ("TX Contact", "The talkgroup's name (it matches the name in the talkgroup list below); None for FM", "directory"),
    ("RX Group List", "None, or the name you give on the page (the group list itself is made in the programming program)", "your choice"),
    ("Color Code", "The repeater's color code (0 for FM)", "directory"),
    ("Time Slot", "Slot 1 or Slot 2 from the talkgroup's slot", "directory"),
    ("DMR ID", "The radio ID name you give on the page (blank for FM)", "your choice"),
    ("CTC/DCS Encode", "The repeater's CTCSS tone (00.0 for none or DMR); a DCS code as D023N", "directory"),
    ("CTC/DCS Decode", "00.0 (carrier squelch: the radio opens on any signal)", "fixed"),
    ("Not in this export", "Contacts (the DMR user list, whose source needs permission), scan lists, zones, roaming and the radio's own settings", "left to you"),
]


def select(f):
    """Approved repeaters for the filters in the dict f: bands (list), modes (list), state, city (text), active (bool), mapped (bool), ids (list), near (lat, lon, miles), digital_only."""
    where, args = ["approved", "kind = 'repeater'"], []
    if f.get("state"):
        where.append("upper(state) = upper(%s)")
        args.append(f["state"])
    if f.get("city"):
        where.append("city ILIKE %s")
        args.append("%" + f["city"] + "%")
    if f.get("active"):
        where.append("status = 'active'")
    if f.get("mapped"):
        where.append("lat IS NOT NULL")
    if f.get("ids"):
        where.append("id = ANY(%s)")
        args.append(list(f["ids"]))
    rows = get_db().execute("SELECT * FROM repeaters WHERE " + " AND ".join(where) + " ORDER BY freq_hz, callsign", args).fetchall()
    out = []
    bands, modes = set(f.get("bands") or []), set(f.get("modes") or [])
    near = f.get("near")
    for r in rows:
        if bands and band_of(r["freq_hz"]) not in bands:
            continue
        have = set((r["modes"] or "FM").split(","))
        if modes and not (have & modes):
            continue
        if near and near[2]:
            if r["lat"] is None:
                continue
            import math
            dy, dx = (r["lat"] - near[0]) * 69.0, (r["lon"] - near[1]) * 69.0 * math.cos(math.radians(near[0]))
            if math.hypot(dx, dy) > near[2]:
                continue
        out.append(r)
    return out[: cfg_int("plug_max_repeaters")]


def talkgroups_of(ids):
    if not ids:
        return {}
    d = {}
    for t in get_db().execute("SELECT * FROM repeater_talkgroups WHERE repeater_id = ANY(%s) ORDER BY slot, tg_id", (list(ids),)).fetchall():
        d.setdefault(t["repeater_id"], []).append(t)
    return d


def _ascii(s, n):
    return re.sub(r"[^A-Za-z0-9 .&'/+-]", "", s or "").strip()[:n]


def _name(call, city, extra=""):
    base = _ascii(call, 8)
    room = 16 - len(base) - len(extra) - (1 if extra else 0) - 1
    c = _ascii(city, max(0, room)).strip() if room > 2 else ""
    return (base + (" " + c if c else "") + (" " + extra if extra else ""))[:16].strip()


def _tone(t):
    """('ctcss', '88.5'), ('dcs', 'D023N') or (None, '')."""
    t = (t or "").strip().upper()
    if re.fullmatch(r"\d{2,3}(\.\d)?", t) and float(t) > 0:
        return "ctcss", f"{float(t):.1f}"
    m = re.fullmatch(r"D?(\d{3})([NI]?)", t)
    if m and t.startswith("D"):
        return "dcs", "D" + m.group(1) + (m.group(2) or "N")
    return None, ""


def _tg_name(t):
    n = _ascii(t["name"], 16) or (str(t["tg_id"]) if t["tg_id"] is not None else "")
    return n


def dm32_rows(reps, opts=None):
    """(channel dicts in the DM32 columns, talkgroup dicts used): one analog channel per FM repeater, one digital channel per talkgroup of a DMR repeater."""
    opts = opts or {}
    tgs = talkgroups_of([r["id"] for r in reps])
    chans, used = [], {}
    power = "Low" if opts.get("power") == "low" else "High"
    bw_fm = "12.5KHz" if opts.get("bw") == "12.5" else "25KHz"
    rxlist = _ascii(opts.get("rxgroup", ""), 16) or "None"
    radio_id = _ascii(opts.get("radio_id", ""), 16)

    def base(name, kind, rx, tx):
        return {"No.": "", "Channel Name": name, "Channel Type": kind, "RX Frequency[MHz]": f"{rx / 1e6:.5f}", "TX Frequency[MHz]": f"{tx / 1e6:.5f}", "Power": power, "Band Width": "25KHz" if kind == "Digital" else bw_fm,
                "Scan List": "None", "TX Admit": "Always" if kind == "Digital" else "Allow TX", "Emergency System": "None", "Squelch Level": "3", "APRS Report Type": "Off", "Forbid TX": "0", "APRS Receive": "0",
                "Forbid Talkaround": "0", "Auto Scan": "1", "Lone Work": "0", "Emergency Indicator": "0", "Emergency ACK": "0", "Analog APRS PTT Mode": "0", "Digital APRS PTT Mode": "0", "TX Contact": "None",
                "RX Group List": "None", "Color Code": "0", "Time Slot": "Slot 1", "Encryption": "0", "Encryption ID": "", "APRS Report Channel": "256", "Direct Dual Mode": "0", "Private Confirm": "0",
                "Short Data Confirm": "0", "DMR ID": "", "CTC/DCS Decode": "00.0", "CTC/DCS Encode": "00.0", "Scramble": "None", "RX Squelch Mode": "Carrier/CTC", "Signaling Type": "None", "PTT ID": "OFF",
                "VOX Function": "0", "PTT ID Display": "0"}
    for r in reps:
        rx, tx = r["freq_hz"], r["freq_hz"] + (r["offset_hz"] or 0)
        modes = set((r["modes"] or "FM").split(","))
        if "FM" in modes:
            c = base(_name(r["callsign"], r["city"]), "Analog", rx, tx)
            kind, val = _tone(r["tone"])
            if kind:
                c["CTC/DCS Encode"] = val
            chans.append(c)
        if "DMR" in modes and not opts.get("no_dmr"):
            rows = tgs.get(r["id"]) or [None]
            for t in rows:
                slot = int(t["slot"]) if t else 1
                tgn = _tg_name(t) if t else ""
                c = base(_name(r["callsign"], r["city"], (str(slot) + tgn[:6]) if tgn else ""), "Digital", rx, tx)
                c.update({"Color Code": (r["color_code"] if (r["color_code"] or "").isdigit() else "1"), "Time Slot": f"Slot {slot}", "DMR ID": radio_id, "RX Group List": rxlist})
                if tgn:
                    c["TX Contact"] = tgn
                    used.setdefault((tgn, t["tg_id"]), t["call_type"] if t["call_type"] in ("group", "private") else "group")
                chans.append(c)
    limit = cfg_int("plug_max_channels")
    chans = chans[:limit]
    for i, c in enumerate(chans, 1):
        c["No."] = str(i)
    tgrows = [{"No.": str(i), "Name": n, "ID": str(tid) if tid is not None else "", "Type": "Private Call" if ct == "private" else "Group Call"} for i, ((n, tid), ct) in enumerate(used.items(), 1) if tid is not None]
    return chans, tgrows


def _csv(columns, rows):
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=columns, extrasaction="ignore", lineterminator="\r\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return out.getvalue()


def dm32_csv(reps, opts=None):
    chans, tgs = dm32_rows(reps, opts)
    return _csv(DM32_COLUMNS, chans), _csv(TG_COLUMNS, tgs), len(chans), len(tgs)


def _duplex(r):
    off = r["offset_hz"] or 0
    return ("+" if off > 0 else "-" if off < 0 else ""), abs(off) / 1e6


def chirp_csv(reps):
    """FM repeaters in CHIRP's generic CSV (Location, Name, Frequency, Duplex, Offset, Tone ...); other modes are not channels a CHIRP radio can hold."""
    rows, n = [], 0
    for r in reps:
        if "FM" not in set((r["modes"] or "FM").split(",")):
            continue
        dup, off = _duplex(r)
        kind, val = _tone(r["tone"])
        row = {"Location": n, "Name": _ascii(_name(r["callsign"], r["city"]), 8), "Frequency": f"{r['freq_hz'] / 1e6:.6f}", "Duplex": dup, "Offset": f"{off:.6f}", "Tone": "Tone" if kind == "ctcss" else "DTCS" if kind == "dcs" else "",
               "rToneFreq": val if kind == "ctcss" else "88.5", "cToneFreq": val if kind == "ctcss" else "88.5", "DtcsCode": val[1:4] if kind == "dcs" else "023", "DtcsPolarity": "NN", "RxDtcsCode": val[1:4] if kind == "dcs" else "023",
               "CrossMode": "Tone->Tone", "Mode": "FM", "TStep": "5.00", "Skip": "", "Power": "5.0W", "Comment": _ascii((r["city"] or "") + " " + (r["network"] or ""), 60), "URCALL": "", "RPT1CALL": "", "RPT2CALL": "", "DVCODE": ""}
        rows.append(row)
        n += 1
    return _csv(CHIRP_COLUMNS, rows), n


def rt_csv(reps):
    rows = []
    for r in reps:
        if "FM" not in set((r["modes"] or "FM").split(",")):
            continue
        dup, off = _duplex(r)
        kind, val = _tone(r["tone"])
        rows.append({"Receive Frequency": f"{r['freq_hz'] / 1e6:.5f}", "Transmit Frequency": f"{(r['freq_hz'] + (r['offset_hz'] or 0)) / 1e6:.5f}", "Offset Frequency": f"{off:.5f}", "Offset Direction": {"+": "Plus", "-": "Minus"}.get(dup, "Simplex"),
                     "Operating Mode": "FM", "Name": _name(r["callsign"], r["city"]), "Show Name": "On", "Tone Mode": "Tone" if kind == "ctcss" else "DCS" if kind == "dcs" else "None", "CTCSS": val if kind == "ctcss" else "88.5",
                     "Rx CTCSS": "88.5", "DCS": val[1:4] if kind == "dcs" else "023", "Rx DCS": "023", "DCS Polarity": "Both N", "Tx Power": "High", "Skip": "Off", "Step": "5 kHz", "Comment": _ascii(r["city"], 40)})
    return _csv(RT_COLUMNS, rows), len(rows)
