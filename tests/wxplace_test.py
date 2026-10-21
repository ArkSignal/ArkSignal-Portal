"""Weather for any place: a ZIP code, a city, an airport code or an address becomes a weather page made from the stations near it (current conditions, forecast, history charts, the stations used)."""
import re, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def page(q, **kw):
    return requests.get(B + "/weather/place", params=dict(q=q, **kw))
check("the empty page asks for a place", "ZIP code, city, airport or address" in page("").text or "ZIP code, city, airport or address" in requests.get(B + "/weather/place").text)
for q, want in (("Little Rock AR", "Little Rock, AR"), ("72201", "ZIP 72201"), ("KLIT", "(KLIT)"), ("Conway, AR", "Conway, AR")):
    t = page(q).text
    h1 = re.findall(r"<h1>(.*?)</h1>", t, re.S)
    check(f"{q}: a page for the place with its sections", h1 and want in h1[0] and all(x in t for x in ('id="sec-now"', 'id="sec-history"', 'id="sec-stations"', 'id="pl-charts"')), h1[:1])
check("the stations used say how far away they are", "Distance" in page("Little Rock, AR").text)
check("nothing found says so", "Nothing was found" in page("zzzzqqqqxx").text)
t = requests.get(B + "/weather/place", params={"lat": "34.75", "lon": "-92.29", "q": "Downtown"}).text
check("a point (from the map search) works too", "Downtown" in t and 'id="sec-now"' in t)
check("units can be switched", "&deg;C, km/h, mm" in page("72201", units="metric").text and "°C" in page("72201", units="metric").text or "&deg;C" in page("72201", units="metric").text)
check("odd text is escaped", "<script>alert" not in page("<script>alert(1)</script>").text)
check("Weather for a Place is in the Weather menu", "/weather/place" in requests.get(B + "/").text)
sp = sess("marty", PW).get(B + "/admin/settings").text
check("its three settings are in Settings", all(f'name="{k}"' in sp for k in ("wx_place_radius_mi", "place_forecast_enabled", "nws_api_url")))
j = requests.get(B + "/api/weather/stats.json", params={"metric": "temp_f", "period": "24h", "near": "34.75,-92.29,25"}).json()
check("the history charts' data comes from the statistics file with a center and radius", "series" in j and "stations" in j)
print("ALL PASS" if ok_all else "SOME FAILED")
