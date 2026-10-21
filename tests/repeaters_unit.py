"""In-container checks of repeaters.py: frequency and offset parsing, Hearham records turned into ours, the import rules (verified and edited records are never overwritten, records the
source dropped are removed only when nobody touched them, a bad listing changes nothing), form validation, reports and their limits, applying a report, and the change history.
docker cp this file to /tmp and run: docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/repeaters_unit.py"""
import os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"; os.environ["HUB_URL"] = ""
from werkzeug.datastructures import MultiDict
from app import create_app
from app import repeaters as rp
from app.settings import set_setting

app = create_app()
ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)

class U:   # a stand-in for a signed-in member
    def __init__(self, i): self.id, self.username = i, f"u{i}"

with app.app_context():
    from app.db import get_db
    d = get_db()
    for t in ("repeater_edits", "repeater_reports", "repeaters"):
        d.execute(f"DELETE FROM {t}")
    d.commit()
    set_setting("repeater_states", "AR"); set_setting("repeater_reports_per_hour", "3")

    # ---- numbers
    check("a frequency is read in MHz or hertz", rp.parse_mhz("146.94") == 146940000 and rp.parse_mhz("146,940,000") == 146940000 and rp.parse_mhz("442.1") == 442100000 and rp.parse_mhz("abc") is None and rp.parse_mhz("0.5") is None)
    check("an offset is read in MHz, kHz or hertz, signed", rp.parse_offset("+0.6") == 600000 and rp.parse_offset("-5") == -5000000 and rp.parse_offset("600 kHz") == 600000 and rp.parse_offset("-600000") == -600000 and rp.parse_offset("") == 0 and rp.parse_offset("simplex") == 0 and rp.parse_offset("x") is None)
    check("the band follows the frequency", rp.band_of(146940000) == "2 m" and rp.band_of(442100000) == "70 cm" and rp.band_of(52525000) == "6 m" and rp.band_of(99e6) == "Other")
    check("frequencies show without trailing zeros", rp.mhz(146940000) == "146.94" and rp.mhz(442100000) == "442.1" and rp.mhz(146520000) == "146.52")

    # ---- Hearham records
    h1 = {"id": 5972, "callsign": "kg5qym", "latitude": 34.51, "longitude": -93.25, "city": "Royal`, Arkansas", "group": "DMR", "internet_node": "310150", "mode": "DMR", "encode": "CC1", "decode": "", "frequency": 442100000, "offset": 5000000,
          "description": "Network: Brandmeister\n\nSee https://brandmeister.network/?page=repeater&id=310150", "power": "unknown", "operational": 1, "restriction": ""}
    n1 = rp.normalize_hearham(h1)
    check("a DMR record keeps its color code, network, call sign and offset", n1["callsign"] == "KG5QYM" and n1["color_code"] == "1" and n1["tone"] == "" and n1["network"] == "Brandmeister" and n1["modes"] == "DMR" and n1["offset_hz"] == 5000000 and n1["status"] == "active" and n1["power"] == "", n1)
    h2 = {"id": 1252, "callsign": "W5ANR", "latitude": 35.73618, "longitude": -94.43932, "city": "Fort Smith, AR", "group": "PRA", "mode": "FM", "encode": "100.0", "decode": "100.0", "frequency": 444300000, "offset": 5000000, "description": "Liberty Hill", "power": "grid", "operational": 0}
    n2 = rp.normalize_hearham(h2)
    check("an FM record keeps its tone and its off-the-air flag", n2["tone"] == "100.0" and n2["modes"] == "FM" and n2["status"] == "off" and n2["power"] == "grid", n2)
    h3 = dict(h1, id=1002, callsign="KF5BRU", mode="YSF/FM", group="IRLP", encode="123.0", frequency=442100000)
    n3 = rp.normalize_hearham(h3)
    check("a mixed mode record lists both modes and a node group becomes the kind", n3["modes"] == "Fusion,FM" and n3["kind"] == "irlp", n3)
    check("a record without a frequency or position is dropped", rp.normalize_hearham({"id": 1, "frequency": 0, "latitude": 1, "longitude": 1}) is None and rp.normalize_hearham({"id": 2, "frequency": 146e6}) is None)

    # ---- the import
    far = dict(h1, id=9001, callsign="N0MO", latitude=39.1, longitude=-94.6, city="Kansas City, MO")   # outside Arkansas: not kept
    data = [h1, h2, h3, far]
    c = rp.import_hearham(raw=data)
    check("the import keeps the Arkansas records only", c["added"] == 3 and c["total"] == 3, c)
    row = d.execute("SELECT * FROM repeaters WHERE source_id='5972'").fetchone()
    check("a record carries its source and the state it is in", row["source"] == "hearham" and row["state"] == "AR" and row["approved"])
    c = rp.import_hearham(raw=data)
    check("a second import adds nothing and refreshes the same records", c["added"] == 0 and c["updated"] == 3, c)
    rid = row["id"]
    rp.update(rid, {"tone": "88.5", "status": "off"}, "editor1")
    changed = dict(h1, frequency=442200000)
    c = rp.import_hearham(raw=[changed, h2, h3])
    check("an edited record is left alone by the import", c["skipped"] == 1 and d.execute("SELECT freq_hz, tone FROM repeaters WHERE id=%s", (rid,)).fetchone()["freq_hz"] == 442100000)
    rid2 = d.execute("SELECT id FROM repeaters WHERE source_id='1252'").fetchone()["id"]
    check("the change is in the history with who made it", any(h["actor"] == "editor1" and "tone" in h["detail"] for h in rp.history(rid)))
    rp.verify(rid2, "editor2")
    check("verifying stamps who and when and counts as editing", d.execute("SELECT verified_by, verified_at IS NOT NULL AS v, edited FROM repeaters WHERE id=%s", (rid2,)).fetchone() == {"verified_by": "editor2", "v": True, "edited": True})
    # a listing that dropped two of the records: untouched ones go, edited and verified ones stay (the safe size check applies to a real download, so give enough records)
    many = [dict(h2, id=3000 + k, callsign=f"W5T{k:02d}", latitude=35.0 + k * .01, longitude=-92.5, frequency=int(147e6 + k * 15000)) for k in range(25)]
    rp.import_hearham(raw=many + [h1, h2, h3])
    check("records of the earlier import that nobody touched are listed again", d.execute("SELECT COUNT(*) AS n FROM repeaters WHERE source='hearham'").fetchone()["n"] == 28)
    c = rp.import_hearham(raw=many)
    kept = d.execute("SELECT source_id FROM repeaters WHERE source='hearham' AND source_id IN ('5972', '1252', '1002')").fetchall()
    check("a record the listing dropped is removed unless a person edited or verified it", {r["source_id"] for r in kept} == {"5972", "1252"} and c["removed"] == 1, (kept, c))
    try:
        rp.import_hearham(raw="not a list")
        bad = False
    except RuntimeError:
        bad = True
    check("a listing in the wrong shape changes nothing and says so", bad)

    # ---- the form
    def form(**kw):
        base = {"freq": "146.94", "offset": "-0.6", "tone": "100.0", "callsign": "w5abc", "city": "Little Rock", "lat": "34.74", "lon": "-92.29", "kind": "repeater", "status": "active"}
        base.update(kw); m = MultiDict(base); m.setlist("modes", kw.get("modes_list", ["FM", "DMR"])); return m
    f, err = rp.clean(form())
    check("a good form gives clean fields (call sign upper case, the state found from the position)", f and f["callsign"] == "W5ABC" and f["freq_hz"] == 146940000 and f["offset_hz"] == -600000 and f["state"] == "AR" and f["modes"] == "FM,DMR", (f, err))
    for bad, key in (({"freq": "x"}, "frequency"), ({"offset": "zz"}, "offset"), ({"tone": "12abc"}, "tone"), ({"color_code": "99x"}, "color"), ({"lat": "95"}, "latitude"), ({"lon": ""}, "latitude")):
        f2, err2 = rp.clean(form(**bad))
        check(f"a bad {key} is refused with a plain message", f2 is None and err2, err2)

    # ---- creating, reports and their limits
    rid3 = rp.create(f, "u1", source="member", approved=False, submitted_by=1)
    check("a submission waits for an editor and is not in the public search", d.execute("SELECT approved FROM repeaters WHERE id=%s", (rid3,)).fetchone()["approved"] is False and rid3 not in [r["id"] for r in rp.search(only_public=True)[1]] and rid3 in [r["id"] for r in rp.search(only_public=False)[1]])
    u = U(7)
    ok1, _ = rp.report(u, rid, "tone", "it is 100.0", {"tone": "100.0"})
    ok2, m2 = rp.report(u, rid, "tone", "again")
    ok3, _ = rp.report(u, rid, "off", "")
    ok4, _ = rp.report(u, rid, "coords", "x")
    ok5, m5 = rp.report(u, rid, "frequency", "x")
    check("a report is taken once, and a member is held to the hourly limit", ok1 and not ok2 and "already" in m2 and ok3 and ok4 and not ok5 and "lot of reports" in m5, (ok1, ok2, ok3, ok4, ok5, m5))
    check("a report of kind other needs words", not rp.report(U(8), rid, "other", "")[0] and not rp.report(U(8), rid, "bogus", "x")[0])
    r1 = d.execute("SELECT id FROM repeater_reports WHERE repeater_id=%s AND kind='tone'", (rid,)).fetchone()["id"]
    check("applying a report makes its suggested change and closes it", rp.handle_report(r1, "editor1", True) and d.execute("SELECT tone FROM repeaters WHERE id=%s", (rid,)).fetchone()["tone"] == "100.0" and d.execute("SELECT status FROM repeater_reports WHERE id=%s", (r1,)).fetchone()["status"] == "applied")
    r2 = d.execute("SELECT id FROM repeater_reports WHERE repeater_id=%s AND kind='off'", (rid,)).fetchone()["id"]
    d.execute("UPDATE repeaters SET status='active' WHERE id=%s", (rid,)); d.commit()
    rp.handle_report(r2, "editor1", True)
    check("an off the air report marks the repeater off when applied", d.execute("SELECT status FROM repeaters WHERE id=%s", (rid,)).fetchone()["status"] == "off")
    r3 = d.execute("SELECT id FROM repeater_reports WHERE repeater_id=%s AND kind='coords'", (rid,)).fetchone()["id"]
    rp.handle_report(r3, "editor1", False)
    check("a rejected report changes nothing", d.execute("SELECT status FROM repeater_reports WHERE id=%s", (r3,)).fetchone()["status"] == "rejected" and not rp.handle_report(r3, "editor1", True))

    # ---- search
    check("search finds by call sign, frequency text, mode and band", rp.search(q="KG5QYM")[0] == 1 and rp.search(q="442.1")[0] >= 1 and rp.search(mode="DMR")[0] >= 1 and rp.search(band="70 cm")[0] >= 1 and rp.search(band="2 m")[0] >= 1 and rp.search(q="zzzzz")[0] == 0, [rp.search(**kw)[0] for kw in ({"q": "KG5QYM"}, {"q": "442.1"}, {"mode": "DMR"}, {"band": "70 cm"}, {"band": "2 m"}, {"q": "zzzzz"})])
    check("the map data lists approved records with a position", all(p["lat"] is not None for p in rp.map_points()) and rid3 not in [p["id"] for p in rp.map_points()])

    # ---- repeaters announced on APRS
    pa = rp.parse_aprs_repeater("147.18-R", "147.180MHz +060 PL114.8 SPA AMATEUR RADIO ASSOCIATION ALLSTR 456150")
    check("an APRS repeater beacon gives frequency, a +060 offset of 0.6 MHz and an exact PL tone", pa and pa["freq_hz"] == 147180000 and pa["offset_hz"] == 600000 and pa["tone"] == "114.8" and pa["modes"] == "FM" and pa["allstar"], pa)
    pb = rp.parse_aprs_repeater("WB5SPA-DR", "442.350MHz +500 CC2 TG311575 on TS2 BM DMR Repeater")
    check("a DMR beacon gives +500 as 5 MHz, the color code and the network", pb and pb["freq_hz"] == 442350000 and pb["offset_hz"] == 5000000 and pb["color_code"] == "2" and pb["modes"] == "DMR" and pb["network"] == "Brandmeister", pb)
    pc = rp.parse_aprs_repeater("146.64-OK", "T088 R90m Net TH7PM")
    check("the frequency can be in the object's name, and T088 is the tone code for 88.5 Hz", pc and pc["freq_hz"] == 146640000 and pc["tone"] == "88.5" and not pc["has_offset"], pc)
    pd = rp.parse_aprs_repeater("NA0D-R2", "AllStar Hub 472350 Echolink 196181, NA0D Baxter 444.050 + 107.2 www.na0d.com")
    check("a bare plus sign is the usual offset of the band, and a tone after it is read", pd and pd["freq_hz"] == 444050000 and pd["offset_hz"] == 5000000 and pd["tone"] == "107.2", pd)
    pe = rp.parse_aprs_repeater("KI5LAW-R1", "AUXCOMM Repeater 147.330MHz T146.2 +0.6")
    check("an explicit +0.6 is 0.6 MHz and T146.2 is read", pe and pe["offset_hz"] == 600000 and pe["tone"] == "146.2", pe)
    check("a beacon without an amateur frequency gives nothing", rp.parse_aprs_repeater("W5ABC-9", "on the road 55 mph") is None and rp.parse_aprs_repeater("X", "channel 3 at 99.5 MHz") is None)
    for t in ("repeater_edits", "repeater_reports", "repeaters"):
        d.execute(f"DELETE FROM {t}")
    d.commit()
    rp.import_hearham(raw=[h2])   # Fort Smith, 444.300 +5
    stations = [{"call": "147.18-R", "lat": 34.4687, "lon": -93.0675, "comment": "147.180MHz +060 PL114.8 SPA ALLSTR"},
                {"call": "444.300FS", "lat": 35.7362, "lon": -94.4393, "comment": "T100 +500"},   # the repeater Hearham already lists, near Fort Smith
                {"call": "W5XYZ-9", "lat": 34.9, "lon": -92.1, "comment": "driving"},
                {"call": "146.94-LR", "lat": 39.1, "lon": -94.6, "comment": "146.940MHz -060"}]   # in Missouri: not kept
    a = rp.import_aprs(stations)
    check("APRS repeaters are added when the directory lacks them; one already listed nearby is not added twice; unreadable and out-of-state ones are left out", a == {"added": 1, "known": 1, "unreadable": 1}, a)
    ra = d.execute("SELECT * FROM repeaters WHERE source='aprs'").fetchone()
    check("an added one is marked as from APRS and not verified, with its read values", ra and ra["status"] == "unverified" and ra["freq_hz"] == 147180000 and ra["tone"] == "114.8" and ra["state"] == "AR" and "Announced on APRS" in ra["description"], ra)
    check("running it again adds nothing", rp.import_aprs(stations)["added"] == 0)

    # ---- a club list
    NL = chr(10)
    csv_text = NL.join(["freq,offset,tone,callsign,city,state,lat,lon,modes,notes",
                        "146.94,-0.6,100.0,W5ABC,Little Rock,AR,34.7465,-92.2896,FM,Club repeater",
                        "147.18,+0.6,114.8,W5SPA,Hot Springs,AR,34.4687,-93.0675,FM,same as the APRS one above",
                        "443.5,+5,,K5DEF,Conway,AR,,,FM DMR,placed at the city",
                        "99.5,,,X,Nowhere,AR,35,-92,FM,not a ham band",
                        "146.52,abc,,Y,Z,AR,35,-92,FM,bad offset"]) + NL
    c, problems = rp.import_csv(csv_text, "Test council list", "editor1")
    check("a club list adds new repeaters, skips ones already listed, and says what it could not take", c["added"] >= 1 and c["known"] == 1 and len(problems) >= 2, (c, problems))
    rl = d.execute("SELECT * FROM repeaters WHERE callsign='W5ABC'").fetchone()
    check("a listed repeater keeps its values and shows whose list it came from", rl and rl["freq_hz"] == 146940000 and rl["offset_hz"] == -600000 and rl["tone"] == "100.0" and rl["source"] == "list:Test council list" and rl["edited"], rl)
    rcity = d.execute("SELECT * FROM repeaters WHERE callsign='K5DEF'").fetchone()
    check("a row with a city and no position is placed at the city (when the places are loaded) and takes two modes", rcity is None or (rcity["modes"] == "FM,DMR" and 34 < rcity["lat"] < 36), rcity)
    bad, pr_ = rp.import_csv("a,b" + NL + "1,2" + NL, "x", "e")
    check("a file without a freq column is refused with a plain message", bad["added"] == 0 and pr_ and "freq" in pr_[0])

    for t in ("repeater_edits", "repeater_reports", "repeaters"):
        d.execute(f"DELETE FROM {t}")
    d.execute("DELETE FROM settings WHERE key IN ('repeater_reports_per_hour')")
    d.commit()
print("ALL PASS" if ok_all else "SOME FAILED")
