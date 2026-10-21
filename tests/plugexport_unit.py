"""In-container check of plugexport.py: repeaters are chosen by band, mode, place and status; the DM32 channel list has the real export's columns and values, a DMR repeater becomes one channel per
talkgroup with a matching talkgroup list, the file reads back through the portal's own plug import (read_channels) with the same frequencies, tones, color codes, slots and contacts; CHIRP and the
RT Systems style files carry the offsets and tones. docker cp to /tmp and run: docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/plugexport_unit.py"""
import csv, io, os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"; os.environ["HUB_URL"] = ""
from app import create_app
from app import plugexport as pe
from app import repeater_digital as rd
app = create_app()
ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)
with app.app_context():
    from app.db import get_db
    d = get_db()
    d.execute("DELETE FROM repeaters WHERE callsign LIKE 'ZZP%'")
    def add(call, hz, off, tone, modes, cc, city, status="active", lat=None, lon=None):
        return d.execute("INSERT INTO repeaters (callsign, city, state, lat, lon, freq_hz, offset_hz, tone, modes, color_code, kind, status, approved) VALUES (%s,%s,'AR',%s,%s,%s,%s,%s,%s,%s,'repeater',%s,TRUE) RETURNING id",
                         (call, city, lat, lon, hz, off, tone, modes, cc, status)).fetchone()["id"]
    a = add("ZZP1A", 146940000, -600000, "100.0", "FM", "", "Conway", lat=35.09, lon=-92.44)
    b = add("ZZP1B", 443750000, 5000000, "", "FM,DMR", "1", "Little Rock", lat=34.74, lon=-92.28)
    c = add("ZZP1C", 442100000, 5000000, "D023N", "FM", "", "Pocahontas", status="off")
    e = add("ZZP1D", 52525000, -500000, "", "FM", "", "Fayetteville")
    for slot, tid, name in ((1, 3105, "Arkansas"), (2, 31050, "ARWX"), (1, 91, "World Wide")):
        d.execute("INSERT INTO repeater_talkgroups (repeater_id, slot, tg_id, name, call_type, mode) VALUES (%s,%s,%s,%s,'group','static')", (b, slot, tid, name))
    d.commit()
    sel = lambda **k: [r for r in pe.select(dict(state="AR", **k)) if r["callsign"].startswith("ZZP")]
    check("all four are selected with no filter", len(sel()) == 4)
    check("a band filter", {r["callsign"] for r in sel(bands=["2 m"])} == {"ZZP1A"} and {r["callsign"] for r in sel(bands=["70 cm", "6 m"])} == {"ZZP1B", "ZZP1C", "ZZP1D"})
    check("a mode filter", {r["callsign"] for r in sel(modes=["DMR"])} == {"ZZP1B"})
    check("active only and mapped only", {r["callsign"] for r in sel(active=True)} == {"ZZP1A", "ZZP1B", "ZZP1D"} and {r["callsign"] for r in sel(mapped=True)} == {"ZZP1A", "ZZP1B"})
    check("a city filter", {r["callsign"] for r in sel(city="conw")} == {"ZZP1A"})
    check("near a point: Conway is about 27 miles from Little Rock, the one without a position is left out", {r["callsign"] for r in sel(near=(34.74, -92.28, 40))} == {"ZZP1A", "ZZP1B"} and {r["callsign"] for r in sel(near=(34.74, -92.28, 5))} == {"ZZP1B"})
    reps = sel()
    chans, tgs = pe.dm32_rows(reps, {"radio_id": "KD5VCF", "rxgroup": "DMR Ark"})
    check("the columns are the real export's", list(chans[0].keys()) == pe.DM32_COLUMNS and len(pe.DM32_COLUMNS) == 40)
    check("one analog channel for each FM repeater (4) and one digital one for each talkgroup (3)", len(chans) == 7 and sum(1 for x in chans if x["Channel Type"] == "Digital") == 3, [x["Channel Name"] for x in chans])
    an = next(x for x in chans if x["Channel Name"].startswith("ZZP1A"))
    check("an analog channel: output and input frequency, the tone, Allow TX", an["RX Frequency[MHz]"] == "146.94000" and an["TX Frequency[MHz]"] == "146.34000" and an["CTC/DCS Encode"] == "100.0" and an["TX Admit"] == "Allow TX" and an["Channel Type"] == "Analog", an)
    dc = [x for x in chans if x["Channel Type"] == "Digital"]
    check("digital channels: color code, slot, contact, radio ID, group list, input 5 MHz up", {x["TX Contact"] for x in dc} == {"Arkansas", "ARWX", "World Wide"} and {x["Time Slot"] for x in dc} == {"Slot 1", "Slot 2"} and all(x["Color Code"] == "1" and x["DMR ID"] == "KD5VCF" and x["RX Group List"] == "DMR Ark" and x["TX Frequency[MHz]"] == "448.75000" for x in dc), dc[0])
    check("a DCS tone is written as D023N", next(x for x in chans if x["Channel Name"].startswith("ZZP1C"))["CTC/DCS Encode"] == "D023N")
    check("names are 16 characters or fewer", all(len(x["Channel Name"]) <= 16 for x in chans))
    check("numbered 1 to n", [x["No."] for x in chans] == [str(i) for i in range(1, 8)])
    check("the talkgroup list has each used talkgroup once with its ID and type", {(t["Name"], t["ID"], t["Type"]) for t in tgs} == {("Arkansas", "3105", "Group Call"), ("ARWX", "31050", "Group Call"), ("World Wide", "91", "Group Call")}, tgs)
    ch_csv, tg_csv, nc, nt = pe.dm32_csv(reps, {"radio_id": "KD5VCF"})
    back = [x for x in rd.read_channels(ch_csv) if x["name"].startswith("ZZP")]
    check("the file reads back through the portal's own plug import", len(back) == 7, len(back))
    ba = next(x for x in back if x["name"].startswith("ZZP1A")); bd = [x for x in back if x["digital"]]
    check("with the same frequencies, tone, color code, slot and contact", ba["rx_hz"] == 146940000 and ba["tx_hz"] == 146340000 and ba["tone_tx"] == "100.0" and len(bd) == 3 and {x["contact"] for x in bd} == {"Arkansas", "ARWX", "World Wide"} and all(x["cc"] == "1" for x in bd) and {x["slot"] for x in bd} == {1, 2})
    tgr = list(csv.DictReader(io.StringIO(tg_csv)))
    check("the talkgroup file reads as the radio's (No., Name, ID, Type)", list(tgr[0].keys()) == pe.TG_COLUMNS and len(tgr) == 3)
    nd = pe.dm32_rows(reps, {"no_dmr": True})[0]
    check("leaving DMR out leaves the FM channels", len(nd) == 4 and all(x["Channel Type"] == "Analog" for x in nd))
    cc, n = pe.chirp_csv(reps)
    rows = list(csv.DictReader(io.StringIO(cc)))
    r1 = next(x for x in rows if x["Frequency"] == "146.940000")
    check("CHIRP: FM repeaters only, with duplex, offset and tone", n == 4 and r1["Duplex"] == "-" and r1["Offset"] == "0.600000" and r1["Tone"] == "Tone" and r1["rToneFreq"] == "100.0" and list(rows[0].keys()) == pe.CHIRP_COLUMNS, r1)
    r2 = next(x for x in rows if x["Frequency"] == "442.100000")
    check("CHIRP: a DCS repeater", r2["Tone"] == "DTCS" and r2["DtcsCode"] == "023" and r2["Duplex"] == "+" and r2["Offset"] == "5.000000", r2)
    rt, n2 = pe.rt_csv(reps)
    rr = list(csv.DictReader(io.StringIO(rt)))
    check("RT Systems style: input frequency, direction and tone mode", n2 == 4 and any(x["Receive Frequency"] == "146.94000" and x["Transmit Frequency"] == "146.34000" and x["Offset Direction"] == "Minus" and x["Tone Mode"] == "Tone" for x in rr), rr[:1])
    d.execute("DELETE FROM repeaters WHERE callsign LIKE 'ZZP%'"); d.commit()
print("ALL PASS" if ok_all else "SOME FAILED")
