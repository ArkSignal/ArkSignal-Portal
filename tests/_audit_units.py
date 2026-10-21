import re, sys, time, json
from selenium import webdriver
B = "http://test-box.invalid:18431"
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,1000")
d = webdriver.Chrome(options=o)
EXP = {"aircraft": "aero", "weather": "imperial", "sondes": "metric", "aprs": "imperial", "acars": "aero", "mesh": "metric", "repeaters": "imperial", "places": "imperial", "network": "aero", "home": "imperial"}
OK = {"aero": {"ft", "kt", "nm", "ft/min", "°C"}, "imperial": {"ft", "mph", "mi", "°F", "inHg", "in", "ft/min"}, "metric": {"m", "km", "km/h", "m/s", "°C", "hPa", "mm"}}
ALLU = r"(ft/min|km/h|m/s|inHg|mph|°F|°C|hPa|ft|km|kt|nm|mi|mm|in|m)"
pages = sys.argv[1:] or ["/", "/live", "/aircraft", "/history", "/airports/", "/coverage", "/feeders/", "/mlat/", "/status", "/acars/", "/weather/", "/weather/map", "/weather/stations/", "/sondes/", "/sondes/flights", "/sondes/stations/", "/sondes/statistics", "/sondes/map", "/aprs/", "/aprs/map", "/repeaters/", "/repeaters/map", "/counties/", "/hospitals/", "/mesh/", "/mesh/map", "/radio/", "/frequencies"]
try:
    for p in pages:
        d.get(B + p); time.sleep(3.5)
        area = d.execute_script("return window.ARK_AREA")
        txt = d.execute_script("return document.body.innerText")
        toks = re.findall(r"(?<![A-Za-z0-9/])[0-9][0-9,\.]*\s?" + ALLU + r"(?![A-Za-z/])", txt)
        exp = EXP.get(area, "imperial")
        bad = sorted({t for t in toks if t not in OK[exp]})
        print(f"{p:26} area={area:10} expect={exp:9} found={sorted(set(toks))}  BAD={bad}")
finally:
    d.quit()
