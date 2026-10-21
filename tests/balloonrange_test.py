"""Balloon Map time window: the live windows stop at 48 hours (Settings > Radiosondes), and older flights are loaded as history with From and To dates, limited to a number of days."""
import re, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)

m = requests.get(B + "/sondes/map").text
sel = re.search(r'<select id="sm-hours">(.*?)</select>', m, re.S).group(1)
vals = re.findall(r'<option value="([^"]+)"', sel)
check("the live windows stop at 48 hours", [v for v in vals if v.isdigit() and int(v) > 48] == [] and "48" in vals and "24" in vals, vals)
check("the last choice is Pick dates (history)", vals[-1] == "range", vals)
check("From and To date pickers and a Load History button are there", all(x in m for x in ('id="sm-from"', 'id="sm-to"', 'type="date"', 'id="sm-rangego"')))
check("a history link (from and to in the address) is understood", 'q.get("from")' in m and "&from=" in m)

j = requests.get(B + "/api/sondes/map.json", params={"hours": 720, "static": 0}).json()
check("a live request is not history", j["history"] is False and "range" not in j)
j = requests.get(B + "/api/sondes/map.json", params={"from": "2026-10-01", "to": "2026-10-03", "static": 0}).json()
check("dates make a history request and say which days were used", j["history"] is True and j["range"] == ["2026-10-01", "2026-10-03"] and j["note"] == "", j.get("range"))
j = requests.get(B + "/api/sondes/map.json", params={"from": "2026-10-05", "to": "2026-10-02", "static": 0}).json()
check("reversed dates are put in order", j["range"] == ["2026-10-02", "2026-10-05"], j.get("range"))
j = requests.get(B + "/api/sondes/map.json", params={"from": "2020-01-01", "to": "2020-12-31", "static": 0}).json()
check("a range longer than the limit is cut to the most recent days with a note", j["history"] and j["range"][1] == "2020-12-31" and "cut to 31 days" in j["note"], (j.get("range"), j.get("note")))
j = requests.get(B + "/api/sondes/map.json", params={"from": "nonsense", "to": "2026-10-03", "static": 0}).json()
check("a bad date is ignored (live window)", j["history"] is False)
j = requests.get(B + "/api/sondes/map.json", params={"from": "2026-10-03", "static": 0}).json()
check("a single date is one day", j["range"] == ["2026-10-03", "2026-10-03"], j.get("range"))

adm = sess("marty", PW)
sp = adm.get(B + "/admin/settings").text
check("the two limits are settings under Radiosondes", 'name="sonde_map_max_hours"' in sp and 'name="sonde_history_max_days"' in sp)
print("ALL PASS" if ok_all else "SOME FAILED")
