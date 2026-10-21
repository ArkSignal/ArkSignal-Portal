"""Am I Feeding? also checks the signed-in owner's weather stations: a hint when signed out, the station with its state when signed in."""
import re
exec(open("roles_test.py", encoding="utf-8").read().split("adm=sess(")[0])
import requests
anon = requests.Session()
html = anon.get(B + "/feed/check?check=1").text
check("signed out: weather section asks to sign in", "Your Weather Stations" in html and "Sign in</a> and check again" in html)

adm = sess("marty", PW)
html = adm.get(B + "/feed/check").text
check("signed in, before Check Now: prompt to press the button", "Press Check Now above to see whether your weather stations" in html)
html = adm.get(B + "/feed/check?check=1").text
had = "You have no weather stations yet" in html
r = adm.post(B + "/my/weather/add", data={"csrf_token": tok(adm, "/my/weather"), "kind": "relay", "name": "Check Test", "station_id": "CHKTEST-WX", "lat": "35.1", "lon": "-92.4", "privacy": "general"}, allow_redirects=False)
check("add a weather station", r.status_code in (200, 302), r.status_code)
html = adm.get(B + "/feed/check?check=1").text
check("signed in: the station is listed with a state", "CHKTEST-WX" in html and "no recent report" in html, html[html.find("Your Weather Stations"):][:300] if "Your Weather Stations" in html else "no section")
wid = re.search(r'href="/my/weather/(\d+)">Open station', html)
check("the Open station link is there", bool(wid))
if wid:
    adm.post(B + f"/my/weather/{wid.group(1)}/delete", data={"csrf_token": tok(adm, f"/my/weather/{wid.group(1)}")})
html = adm.get(B + "/feed/check?check=1").text
check("cleaned up", "CHKTEST-WX" not in html)
print("ALL PASS" if ok_all else "SOME FAILED")
