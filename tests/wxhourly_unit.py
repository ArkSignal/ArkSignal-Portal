"""In-container check of the hour-by-hour forecast on Weather for a Place: the NWS hourly answer is read into hours with the place's own dates, labels and icons, and the page shows them with clickable days.
docker cp to /tmp and run: docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/wxhourly_unit.py"""
import os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"; os.environ["HUB_URL"] = ""
from app import create_app, wxplace

app = create_app()
ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)

def hour(start, temp, short, day):
    return {"startTime": start, "temperature": temp, "temperatureUnit": "F", "windSpeed": "5 mph", "windDirection": "N", "shortForecast": short, "isDaytime": day, "probabilityOfPrecipitation": {"value": 20}}
def fake(url):
    if "/points/" in url:
        return {"properties": {"forecast": "F", "forecastHourly": "H", "forecastGridData": "G", "cwa": "LZK", "relativeLocation": {"properties": {"city": "Little Rock"}}}}
    if url == "G":
        return {"properties": {"quantitativePrecipitation": {"values": [{"validTime": "2026-11-13T21:00:00+00:00/PT6H", "value": 6.0}]},
                               "snowfallAmount": {"values": [{"validTime": "2026-11-14T06:00:00+00:00/PT2H", "value": 10.0}]}, "iceAccumulation": {"values": [{"validTime": "2026-11-13T21:00:00+00:00/P1DT0H", "value": None}]}}}
    if url == "F":
        return {"properties": {"updateTime": "x", "periods": [{"startTime": "2026-11-13T06:00:00-06:00", "name": "Today", "temperature": 55, "temperatureUnit": "F", "windSpeed": "5 mph", "windDirection": "N", "shortForecast": "Sunny", "detailedForecast": "d", "isDaytime": True},
                                                                {"startTime": "2026-11-14T06:00:00-06:00", "name": "Saturday", "temperature": 55, "temperatureUnit": "F", "windSpeed": "5 mph", "windDirection": "N", "shortForecast": "Mostly Clear", "detailedForecast": "d", "isDaytime": True}]}}
    return {"properties": {"periods": [hour("2026-11-13T15:00:00-06:00", 56, "Sunny", True), hour("2026-11-13T23:00:00-06:00", 40, "Mostly Clear", False), hour("2026-11-14T00:00:00-06:00", 38, "Chance Light Rain", False)]}}
wxplace._get_json = fake
with app.app_context():
    f = wxplace.forecast(34.7001, -92.2001)
    check("the daily forecast is still there", f and len(f["periods"]) == 2 and f["periods"][1]["date"] == "2026-11-14", f)
    sun = [h for h in f["hours"] if h.get("sun")]
    check("the sunset is a marker between 3 PM and 11 PM, near 5:05 PM", len(sun) == 1 and sun[0]["sun"] == "Sunset" and sun[0]["date"] == "2026-11-13" and sun[0]["label"] in ("5:03 PM", "5:04 PM", "5:05 PM", "5:06 PM", "5:07 PM") and f["hours"][1] is sun[0], sun)
    check("hours say whether the chance is of rain or snow", [h["snow"] for h in f["hours"] if not h.get("sun")] == [False, False, False])
    hs = [h for h in f["hours"] if not h.get("sun")]
    check("hours keep the place's own date and clock", [h["date"] for h in hs] == ["2026-11-13", "2026-11-13", "2026-11-14"] and hs[0]["label"] == "3 PM" and hs[1]["label"] == "11 PM" and hs[2]["label"] == "12 AM", hs)
    check("hours have icons and rain chances", [h["icon"] for h in hs] == ["sunny", "moon", "rain"] and hs[0]["rain"] == 20, hs)
    check("a 6 hour block of 6 mm is shared out as 1 mm an hour (3 PM local is in it, 11 PM is not)", hs[0]["liq"] == 1.0 and hs[1]["liq"] == 0.0, hs)
    check("snow amounts are kept the same way (5 mm in the midnight hour)", hs[2]["snowmm"] == 5.0 and hs[0]["snowmm"] == 0.0, hs)
    check("day totals add up the hours of each date", f["totals"]["2026-11-13"]["liq"] == 1.0 and f["totals"]["2026-11-14"]["snowmm"] == 5.0, f["totals"])
    check("a block with no value is skipped", wxplace._grid_series({"values": [{"validTime": "2026-11-13T00:00:00+00:00/PT3H", "value": None}, {"validTime": "2026-11-13T00:00:00+00:00/PT3H", "value": 3}]}) == {"2026-11-13T00": 1.0, "2026-11-13T01": 1.0, "2026-11-13T02": 1.0})
print("ALL PASS" if ok_all else "SOME FAILED")
