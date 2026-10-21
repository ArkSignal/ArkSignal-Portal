"""In-container checks of repeater_digital.py: the talkgroup lines, the attrs, and a radio code plug (the DM32's columns) added to the directory.
docker cp to /tmp and run: docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/repdigital_unit.py"""
import os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"; os.environ["HUB_URL"] = ""
from app import create_app
from app import repeater_digital as rd
from app import repeaters as rp

app = create_app()
ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)

with app.app_context():
    from app.db import get_db
    d = get_db()
    rows, bad = rd.parse_talkgroups("1, 3105, Arkansas, static\n2,10501,ARWX,dynamic\nTS2, Local\n3105, Statewide\n# note\n\nSlot 2 , 9, Local")
    check("talkgroup lines: slot, ID, name, mode, with the slot and the mode optional", rows == [(1, 3105, "Arkansas", "static"), (2, 10501, "ARWX", "dynamic"), (2, None, "Local", "static"), (1, 3105, "Statewide", "static"), (2, 9, "Local", "static")] and not bad, rows)
    check("a line with neither a number nor a name is reported", rd.parse_talkgroups("1,")[1] != [] or rd.parse_talkgroups(",")[1] != [])
    import json
    class F(dict):
        def getlist(self, k): return [self[k]] if k in self else []
    a = json.loads(rd.attrs_from_form(F({"a_nac": "293", "a_website": "club.example.org", "a_ran": ""})))
    check("attrs keep what was filled in, and a website gets https://", a == {"nac": "293", "website": "https://club.example.org"}, a)

    d.execute("DELETE FROM repeaters WHERE source LIKE 'plug:UNIT%' OR callsign IN ('W5UNI', 'AC5ZZ')")
    d.commit()
    # one repeater already listed (output 443.125, call AC5ZZ), one not
    rid = d.execute("INSERT INTO repeaters (callsign, city, state, lat, lon, freq_hz, offset_hz, modes, source, approved) VALUES ('AC5ZZ', 'Little Rock', 'AR', 34.7, -92.3, 443125000, 5000000, 'FM', 'local', TRUE) RETURNING id").fetchone()["id"]
    d.commit()
    plug = ("No.,Channel Name,Channel Type,RX Frequency[MHz],TX Frequency[MHz],Power,Band Width,Time Slot,TX Contact,RX Group List,Color Code,CTC/DCS Decode,CTC/DCS Encode\n"
            "1,AC5ZZ Lit 1ARWX,Digital,443.12500,443.12500,High,25KHz,Slot 1,ARWX,DMR LRMet,2,00.0,00.0\n"
            "2,AC5ZZ Lit 210501,Digital,443.12500,443.12500,High,25KHz,Slot 2,10501,DMR LRMet,2,00.0,00.0\n"
            "3,W5UNI Con 1Local,Digital,145.21000,145.21000,High,25KHz,Slot 1,Local,DMR Conwy,1,00.0,00.0\n"
            "4,W5UNI Con FM,Analog,146.94000,146.34000,High,25KHz,Slot 1,None,None,0,100.0,100.0\n"
            "5,FRS 1,Analog,462.56250,462.56250,High,12.5KHz,Slot 1,None,None,0,00.0,00.0\n"
            "6,F&F 2m Dig,Digital,146.55000,146.55000,Low,25KHz,Slot 1,Family,F&F Simple,9,00.0,00.0\n")
    c, problems = rd.import_plug(plug, "Name,Call ID\nARWX,3105999\n", "UNIT radio", "tester")
    check("channels not named after a repeater's call sign (FRS, simplex F&F) are skipped", c["skipped"] == 2, c)
    check("a repeater already listed on that frequency is updated (DMR added, color code, talkgroups), a new one is added", c["updated"] == 1 and c["added"] == 2, c)
    r = rp.get(rid)
    check("the listed repeater keeps its position and gets the DMR mode and the color code", r["lat"] == 34.7 and "DMR" in r["modes"].split(",") and r["color_code"] == "2", dict(r))
    tgs = rd.talkgroups(rid)
    check("its talkgroups come from the channels' contacts with their slots; a numeric contact is its own ID and the contact list gives a name its number", [(t["slot"], t["tg_id"], t["name"]) for t in tgs] == [(1, 3105999, "ARWX"), (2, 10501, "10501")], [(t["slot"], t["tg_id"], t["name"]) for t in tgs])
    new = d.execute("SELECT * FROM repeaters WHERE callsign='W5UNI' ORDER BY freq_hz").fetchall()
    check("repeaters that were not listed are added approved, without a position, from the plug", len(new) == 2 and all(n["approved"] and n["lat"] is None and n["source"] == "plug:UNIT radio" for n in new), [dict(n) for n in new])
    fm = next(n for n in new if n["freq_hz"] == 146940000)
    check("an analog channel gives the tone and the offset from its transmit frequency", fm["tone"] == "100.0" and fm["offset_hz"] == -600000, dict(fm))
    dm = next(n for n in new if n["freq_hz"] == 145210000)
    check("a digital channel whose transmit frequency equals its receive gets the band's usual offset", dm["offset_hz"] == -600000 and dm["modes"] == "DMR" and dm["color_code"] == "1", dict(dm))
    c2, _ = rd.import_plug(plug, "", "UNIT radio", "tester")
    check("importing the same file again adds nothing twice", c2["added"] == 0 and c2["talkgroups"] == 0, c2)
    n, p = rd.set_talkgroups(rid, "1, 3105, Arkansas, static\n2, 10501, ARWX, dynamic", "tester")
    check("saving talkgroups replaces the list", n == 2 and [(t["slot"], t["tg_id"]) for t in rd.talkgroups(rid)] == [(1, 3105), (2, 10501)])
    d.execute("DELETE FROM repeaters WHERE id=%s OR source = 'plug:UNIT radio'", (rid,)); d.commit()
    check("call sign lookup refuses nonsense without asking anyone", rd.callsign_info("not a call!") is None and rd.callsign_info("") is None)
print("ALL PASS" if ok_all else "SOME FAILED")
