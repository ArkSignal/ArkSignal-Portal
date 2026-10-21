import re, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all=True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ")+name, "" if cond else extra); ok_all &= bool(cond)
adm=sess("marty", PW); anon=requests.Session()
h=adm.get(B+"/admin/layers").text
check("layers admin page lists the seeded tar1090 layers", all(n in h for n in ("VFR Sectional Chart","NEXRAD","TFRs","Special Use Airspace","RainViewer Radar","NOAA Radar","US ARTCC Boundaries")))
check("Map Layers tab in the admin bar", 'href="/admin/layers"' in h)
live=anon.get(B+"/live").text
check("live map page carries the layer catalog", '"slug": "vfr-sectional"' in live and 'id="lv-layers"' in live)
check("layers use the FAA tile address like tar1090", "tiles.arcgis.com/tiles/ssFJjBXIUyZDrSYZ/arcgis/rest/services/VFR_Sectional" in live)
def find():
    m=re.search(r'id="layer-(\d+)"[^>]*>\s*<summary><span class="role-name">Test Layer', adm.get(B+"/admin/layers").text); return m.group(1) if m else None
while find(): act(adm,f"/admin/layers/{find()}/delete","/admin/layers")
def save(**kw):
    d=dict(name="Test Layer",slug="test-layer",grp="Weather",kind="tile",url="https://tiles.example.org/{z}/{x}/{y}.png",options="",opacity="0.7",min_zoom="0",max_zoom="12",color="#ff4d4d",refresh_minutes="0",sort_order="900",enabled="1",attribution="")
    d.update(kw)
    for k in [k for k,v in d.items() if v is None]: d.pop(k)
    return act(adm,"/admin/layers/save","/admin/layers",**d)
ok,m,html=save(); check("add a layer", ok and "Test Layer" in html, m)
check("added layer reaches the live map", '"slug": "test-layer"' in anon.get(B+"/live").text)
ok,m,_=save(); check("duplicate short name rejected", not ok, m)
for name,kw in [("http address",dict(slug="t2",url="http://x.example.org/{z}/{x}/{y}.png")),("tile without placeholders",dict(slug="t3",url="https://x.example.org/tile.png")),
                ("bad kind",dict(slug="t4",kind="nope")),("wms without layer",dict(slug="t5",kind="wms",url="https://x.example.org/wms")),
                ("opacity too high",dict(slug="t6",opacity="3")),("zoom order",dict(slug="t7",min_zoom="10",max_zoom="5")),("bad slug",dict(slug="A b")),
                ("bad color",dict(slug="t8",kind="geojson",url="https://x.example.org/a.geojson",color="red"))]:
    ok,m,_=save(**kw); check("reject: "+name, not ok, m)
ok,m,_=save(enabled=None, id=find()); check("hide a layer", ok, m)
check("hidden layer leaves the live map", '"slug": "test-layer"' not in anon.get(B+"/live").text)
lid=find()
check("find the new layer", lid is not None)
if lid:
    ok,m,_=act(adm,f"/admin/layers/{lid}/delete","/admin/layers"); check("delete it", ok, m)
au=adm.get(B+"/admin/audit?action=layer.save").text
check("layer changes are audited", "test-layer" in au)
bg=anon.get(B+"/background.svg")
check("background chart is served as an SVG", bg.status_code==200 and bg.headers["Content-Type"].startswith("image/svg+xml") and "<clipPath" in bg.text)
check("background has the state outline and a chart of airports", bg.text.count("<path")>=3 and bg.text.count("<line")>20, bg.text.count("<line"))
check("every page uses the background", "/background.svg" in anon.get(B+"/feed/").text)
print("ALL PASS" if ok_all else "SOME FAILED")
