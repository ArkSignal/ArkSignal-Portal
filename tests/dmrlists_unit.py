"""In-container checks of dmrlists.py: the formats it reads and the keeping of the lists.
docker cp to /tmp and run: docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/dmrlists_unit.py"""
import os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"; os.environ["HUB_URL"] = ""
from app import create_app
from app import dmrlists as dl
ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)
radio_tg = "No.,Name,ID,Type\n1,ZZ Local,9990001,Group Call\n2,ZZ Statewide,9990002,Group Call\n"
check("a radio's talkgroup export is read", dl.parse_talkgroups(radio_tg) == [(9990001, "ZZ Local", "Group Call"), (9990002, "ZZ Statewide", "Group Call")], dl.parse_talkgroups(radio_tg))
check("a BrandMeister-style JSON list is read", dl.parse_talkgroups('{"9990003": "ZZ Nationwide", "x": "no", "9990004": ""}') == [(9990003, "ZZ Nationwide", "Group Call")])
check("a list of objects is read too", dl.parse_talkgroups('[{"id": 9990005, "name": "ZZ Five"}]') == [(9990005, "ZZ Five", "Group Call")])
radio_ct = "No.,ID,Repeater,Name,City,Province,Country,Remark,Type,Alert Call\n1,9990101,,ZZUser1,Little Rock,Arkansas,United States,,Private Call,0\n2,abc,,Bad,,,,,Private Call,0\n"
check("a radio's contact export is read and a bad ID is skipped", dl.parse_contacts(radio_ct) == [(9990101, "ZZUser1", "Little Rock", "Arkansas", "United States", "Private Call", "", "")], dl.parse_contacts(radio_ct))
rid = "RADIO_ID,CALLSIGN,FIRST_NAME,LAST_NAME,CITY,STATE,COUNTRY\n9990102,ZZ1ABC,Pat,Doe,Conway,Arkansas,United States\n"
check("a RadioID-style user list is read (the call sign is kept apart from the first name; the last name is the remark)", dl.parse_contacts(rid) == [(9990102, "Pat", "Conway", "Arkansas", "United States", "Private Call", "ZZ1ABC", "Doe")], dl.parse_contacts(rid))
app = create_app()
with app.app_context():
    from app.db import get_db
    d = get_db(); d.execute("DELETE FROM dmr_talkgroups WHERE id >= 9990000"); d.execute("DELETE FROM dmr_contacts WHERE id >= 9990000"); d.commit()
    check("importing keeps them and importing again updates, not duplicates", dl.import_talkgroups(radio_tg) == 2 and dl.import_talkgroups(radio_tg.replace("ZZ Local", "ZZ Local 2")) == 2
          and d.execute("SELECT count(*) AS n FROM dmr_talkgroups WHERE id >= 9990000").fetchone()["n"] == 2 and dl.name_for_id(9990001) == "ZZ Local 2")
    check("a name finds its number, ignoring case", dl.id_for_name("zz statewide") == 9990002 and dl.id_for_name("nothing like it") is None)
    check("contacts are kept", dl.import_contacts(radio_ct) == 1 and dl.import_contacts(rid) == 1 and d.execute("SELECT count(*) AS n FROM dmr_contacts WHERE id >= 9990000").fetchone()["n"] == 2)
    rad = "No.,ID,Repeater,Name,City,Province,Country,Remark,Type,Alert Call\n1,9990201,ZZ2DEF,Marty,Conway,Arkansas,United States,,Private Call,0\n"
    check("a radio export that carries the call sign in the Repeater column keeps it", dl.parse_contacts(rad)[0][1:] == ("Marty", "Conway", "Arkansas", "United States", "Private Call", "ZZ2DEF", ""), dl.parse_contacts(rad))
    NL, Q = chr(10), chr(34)
    bigq = "ID,Name,DMR ID,Call Sign,City,Province,Country,Remark,Friend" + NL + Q + "138699" + Q + "," + Q + "Marty" + Q + "," + Q + "3227955" + Q + "," + Q + "KD5VCF" + Q + "," + Q + "Sheridan" + Q + ",,,,"
    check("a list with a row number column called ID uses the DMR ID column and keeps the Call Sign column", [(r[0], r[1], r[6]) for r in dl.parse_contacts(bigq)] == [(3227955, "Marty", "KD5VCF")], dl.parse_contacts(bigq))
    check("the scheduled update does nothing, and says so, while both addresses are blank", "No address" in dl.refresh())
    d.execute("DELETE FROM dmr_talkgroups WHERE id >= 9990000"); d.execute("DELETE FROM dmr_contacts WHERE id >= 9990000"); d.commit()
print("ALL PASS" if ok_all else "SOME FAILED")
