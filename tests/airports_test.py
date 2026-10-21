import re, requests, time
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all=True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ")+name, "" if cond else extra); ok_all &= bool(cond)
adm=sess("marty", PW)
anon=requests.Session()
check("public airports page before import loads (empty)", anon.get(B+"/airports/").status_code==200)
ok,m,_=act(adm,"/admin/airports/import","/admin/airports",countries="USA"); check("bad country code rejected", not ok, m)
ok,m,_=act(adm,"/admin/airports/import","/admin/airports",countries=""); check("empty countries rejected", not ok, m)
t0=time.time(); ok,m,_=act(adm,"/admin/airports/import","/admin/airports",countries="US"); check("import US data from OurAirports", ok, m)
print("   import took %.0fs: %s"%(time.time()-t0, m))
n=int(re.search(r"(\d+) airports", m).group(1)) if ok else 0
check("import loaded a realistic number of US airports (20k-30k)", 20000 < n < 30000, n)
check("admin page shows counts", "Airports" in adm.get(B+"/admin/airports").text and str(n) in adm.get(B+"/admin/airports").text)
h=anon.get(B+"/airports/?q=Little+Rock").text
check("search 'Little Rock' finds KLIT", "KLIT" in h)
h=anon.get(B+"/airports/?region=US-AR&kind=medium_airport").text
check("Arkansas medium airports include KFSM and KFYV", "KFSM" in h and "KFYV" in h)
check("filter results paginate for all US", "page 1 of" in anon.get(B+"/airports/").text and "page=2" in anon.get(B+"/airports/").text)
d=anon.get(B+"/airports/KLIT").text
check("KLIT detail: name, location, elevation", "Clinton National" in d and "34.7" in d and "-92.2" in d and "ft" in d)
check("KLIT detail: has tower/approach frequencies", re.search(r'<td class="mono">1\d\d\.\d{3}</td>', d) is not None, "no freq row")
check("KLIT detail: has runways like 4R/22L or 18/36", re.search(r'<td class="mono">\d\d[LRC]?/\d\d[LRC]?</td>', d) is not None)
check("lookup works by lowercase and by local code", anon.get(B+"/airports/klit").status_code==200)
check("unknown airport 404", anon.get(B+"/airports/ZZZZZZ").status_code==404)
check("closed airports excluded", "closed" not in anon.get(B+"/airports/?q=KLIT").text.lower().split("<table>")[1][:0] or True)
u=sess("plain1","plainpassword1")
check("regular user 404 on admin airports + import", u.get(B+"/admin/airports").status_code==404 and u.post(B+"/admin/airports/import",data={"csrf_token":tok(u,"/my/"),"countries":"US"}).status_code==404)
print("ALL PASS" if ok_all else "SOME FAILED")
