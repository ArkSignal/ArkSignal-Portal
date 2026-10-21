import re, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all=True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ")+name, "" if cond else extra); ok_all &= bool(cond)
adm=sess("marty", PW); anon=requests.Session()
check("bad aircraft address rejected", anon.get(B+"/api/aircraft-info/xyz.json").status_code==400)
check("short address rejected", anon.get(B+"/api/aircraft-info/abc12.json").status_code==400)
d=anon.get(B+"/api/aircraft-info/a403df.json?callsign=AAL2242&lat=34.8&lon=-92.7").json()
check("answer has photo, aircraft and route keys", set(d)=={"photo","aircraft","route"}, d)
if d["aircraft"] or d["route"] or d["photo"]:   # the outside services answered
    check("aircraft details carry make or owner", not d["aircraft"] or "type" in d["aircraft"] or "manufacturer" in d["aircraft"], d)
    check("route has at least two airports", not d["route"] or len(d["route"]["airports"])>=2, d)
    check("photo has image and credit link", not d["photo"] or (d["photo"]["src"] and d["photo"]["link"]), d)
d2=anon.get(B+"/api/aircraft-info/a403df.json?callsign=N500R").json()
check("registration-style callsign gets no route", d2["route"] is None, d2)
# settings: off switch and validation
def sec(**kw): return act(adm,"/admin/settings/save","/admin/settings",section="aircraft-lookups",**kw)
full=dict(lookups_enabled="1",photo_api_url="https://api.planespotters.net/pub/photos",aircraft_db_url="https://api.adsbdb.com/v0",route_api_url="https://adsb.im/api/0/routeset",lookup_user_agent="ArkansasADSBPortal/1.0 (+https://adsb.allisononline.org)",lookup_cache_hours="24",lookup_miss_minutes="30",lookup_timeout_seconds="6")
ok,m,_=sec(**dict(full,lookups_enabled="0")); check("turn lookups off", ok, m)
d=anon.get(B+"/api/aircraft-info/a403df.json?callsign=AAL2242").json()
check("off means nothing is looked up", d=={"photo":None,"aircraft":None,"route":None}, d)
ok,m,_=sec(**dict(full,photo_api_url="http://example.com/x")); check("reject plain http for a public address", not ok, m)
ok,m,_=sec(**dict(full,lookup_timeout_seconds="1")); check("reject out-of-range timeout", not ok, m)
ok,m,_=sec(**full); check("restore lookup settings", ok, m)
h=adm.get(B+"/admin/live" if False else B+"/live").text
check("live page has columns, labels and in-view filter", all(s in h for s in ('id="lv-colbtn"','id="l-labels"','id="f-view"','id="c-extra"')))
check("live page offers the default columns from settings", '"columns": ["callsign", "type", "alt", "gs", "dist"]' in h, re.findall(r'"columns"[^]]*\]',h))
ac=anon.get(B+"/api/aircraft.json").json()["aircraft"]
check("aircraft feed carries the extra detail fields", not ac or any("msgs" in a for a in ac))
# column default setting
def col(**kw): return act(adm,"/admin/settings/save","/admin/settings",section="home-page-and-live-map",**kw)
print("ALL PASS" if ok_all else "SOME FAILED")
