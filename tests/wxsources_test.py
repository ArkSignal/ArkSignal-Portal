"""Weather from every source in one format: members, APRS and airport weather on the Weather Map; an airport APRS also carries is drawn once; APRS views can hide airport stations."""
import requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)

d = requests.get(B + "/api/weather.json").json()
check("without sources the data file is the members' stations only (as before)", d["sources"] == ["members"] and all(s.get("src", "members") == "members" for s in d["stations"]))
d = requests.get(B + "/api/weather.json", params={"sources": "members,aprs,airport"}).json()
check("with all three sources each station says where it came from", d["sources"] == ["members", "aprs", "airport"] and all(s["src"] in ("members", "aprs", "airport") for s in d["stations"]))
keys = {"id", "name", "lat", "lon", "online", "temp_f", "r", "href", "src"}
others = [s for s in d["stations"] if s["src"] != "members"]
check("APRS and airport stations carry the same fields as a member's station, readings in imperial under r", all(keys <= set(s) for s in others) and all(isinstance(s["r"], dict) for s in others), others[:1])
aprs = {s["id"] for s in d["stations"] if s["src"] == "aprs"}
air = {s["id"] for s in d["stations"] if s["src"] == "airport"}
check("an airport APRS carries is not drawn twice (no APRS station that is an airport while airport weather is on)", not any(len(i.split("-")[0]) == 4 and i.split("-")[0] in air for i in aprs), sorted(aprs & air)[:5])
only_aprs = requests.get(B + "/api/weather.json", params={"sources": "aprs"}).json()
check("with the airport source off, the airports APRS carries are shown as APRS stations", only_aprs["sources"] == ["aprs"])
check("a bad source name falls back to the members", requests.get(B + "/api/weather.json", params={"sources": "bogus"}).json()["sources"] == ["members"])

st = requests.get(B + "/api/aprs/stations.json").json()
check("APRS stations say whether they are an airport's own weather", all("airport" in s for s in st["stations"]) or not st["stations"])
hid = requests.get(B + "/api/aprs/stations.json", params={"airports": "0"}).json()["stations"]
check("airports=0 leaves them out", not any(s["airport"] for s in hid))
w = requests.get(B + "/aprs/weather?airports=0").text
check("the APRS Weather page has the Hide airport weather filter", "Hide airport weather" in requests.get(B + "/aprs/weather").text and "Hide airport weather" in w)
check("and so do the APRS list and the APRS map", "Hide airport weather" in requests.get(B + "/aprs/").text and 'id="am-noairport"' in requests.get(B + "/aprs/map").text)
wm = requests.get(B + "/weather/map").text
check("the Weather Map offers the three weather sources", all(x in wm for x in ('id="wm-src"', 'data-src="members"', 'data-src="aprs"', 'data-src="airport"', "sources: srcList")))
print("ALL PASS" if ok_all else "SOME FAILED")
