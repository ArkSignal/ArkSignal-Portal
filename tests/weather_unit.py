"""In-container checks of weather.py: the parsers (with fixtures), unit conversion, sanity limits, derived values, storing, roll-up and charts.
docker cp this file to /tmp and run: docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/weather_unit.py"""
import os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"; os.environ["HUB_URL"] = ""
from datetime import datetime, timedelta, timezone
import psycopg
from psycopg.rows import dict_row
from app import create_app
from app import weather as w

app = create_app()
ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)
near = lambda a, b, t=0.05: a is not None and abs(a - b) <= t

# ---- units
check("celsius to fahrenheit", near(w.to_f(20, "℃"), 68) and near(w.to_f(20, "°C"), 68) and near(w.to_f(68, "℉"), 68) and near(w.to_f("x", "c") or 0, 0))
check("speeds", near(w.to_mph(10, "m/s"), 22.37, .01) and near(w.to_mph(100, "km/h"), 62.14, .01) and near(w.to_mph(10, "kn"), 11.51, .01) and near(w.to_mph(5, "mph"), 5))
check("pressure", near(w.to_inhg(1013.25, "hPa"), 29.92, .01) and near(w.to_inhg(760, "mmHg"), 29.92, .01) and near(w.to_inhg(29.9, "inHg"), 29.9))
check("rain and distance", near(w.to_in(25.4, "mm"), 1) and near(w.to_in(2, "in"), 2) and near(w.to_in(25.4, "mm/hr"), 1) and near(w.to_mi(10, "km"), 6.21371, .001))
check("compass names", w.compass(0) == "N" and w.compass(225) == "SW" and w.compass(359) == "N" and w.compass(None) == "")
check("dew point matches the textbook (68 F, 50 %: about 48.7 F)", near(w.dew_point_f(68, 50), 48.7, .3), w.dew_point_f(68, 50))
check("heat index and wind chill", w.feels_like_f(95, 60, 5) > 110 and w.feels_like_f(20, 50, 15) < 8 and w.feels_like_f(65, 50, 5) == 65)

# ---- Ecowitt / Weather Underground push
eco = {"PASSKEY": "ABC", "stationtype": "GW2000A_V3.1.0", "model": "GW2000A", "freq": "915M", "tempf": "71.2", "humidity": "55", "tempinf": "74", "baromrelin": "29.92", "baromabsin": "29.10",
       "windspeedmph": "7.5", "windgustmph": "12.1", "winddir": "225", "rainratein": "0.05", "dailyrainin": "0.12", "weeklyrainin": "0.4", "monthlyrainin": "1.5", "yearlyrainin": "30.2",
       "solarradiation": "410.5", "uv": "3", "lightning_num": "4", "lightning": "8", "wh65batt": "0"}
r = w.parse_pws(eco)
check("an Ecowitt post is read with the outdoor values, relative pressure and a derived dew point",
      r and r["temp_f"] == 71.2 and r["hum"] == 55 and r["press_in"] == 29.92 and r["wind_mph"] == 7.5 and r["gust_mph"] == 12.1 and r["wind_dir"] == 225 and r["rain_day_in"] == 0.12 and r["rain_year_in"] == 30.2
      and r["solar"] == 410.5 and r["uv"] == 3 and r["lightning_n"] == 4 and near(r["lightning_mi"], 4.97, .01) and near(r["dew_f"], 54.2, .5), r)
check("the device model is kept as a note, not as a reading", r["_extra"]["model"] == "GW2000A" and "tempinf" not in r)
wu = w.parse_pws({"ID": "X", "PASSWORD": "y", "dateutc": "now", "tempf": "60", "humidity": "40", "dewptf": "35", "baromin": "30.10", "windspeedmph": "3", "winddir": "10", "rainin": "0", "dailyrainin": "0", "UV": "1.5", "softwaretype": "test"})
check("the Weather Underground format is read (baromin, dewptf, UV in capitals)", wu and wu["press_in"] == 30.1 and wu["dew_f"] == 35 and wu["uv"] == 1.5 and wu["rain_day_in"] == 0, wu)
check("a post with nothing usable is refused", w.parse_pws({"PASSKEY": "x", "model": "y"}) is None and w.parse_pws({}) is None)
bad = w.parse_pws({"tempf": "900", "humidity": "55", "baromrelin": "99", "windspeedmph": "-3"})
check("an impossible value is dropped but the rest is kept", bad and "temp_f" not in bad and bad["hum"] == 55 and "press_in" not in bad and "wind_mph" not in bad, bad)
check("text and not-a-number are ignored", w.parse_pws({"tempf": "nan", "humidity": "abc"}) is None)

# ---- relay
rl = w.parse_relay({"temp_c": 20, "humidity": 60, "pressure_hpa": 1013.25, "wind_kph": 36, "gust_ms": 10, "wind_dir": 90, "rain_day_mm": 25.4, "lightning_km": 16.09})
check("a relay with metric names is converted", rl and near(rl["temp_f"], 68) and near(rl["press_in"], 29.92, .01) and near(rl["wind_mph"], 22.37, .01) and near(rl["gust_mph"], 22.37, .01) and near(rl["rain_day_in"], 1, .01) and near(rl["lightning_mi"], 10, .01), rl)
rl2 = w.parse_relay({"temp_f": 50, "humidity": 70})
check("a relay with imperial names works and a relay may send Ecowitt names too", rl2["temp_f"] == 50 and w.parse_relay({"tempf": "55", "humidity": "50"})["temp_f"] == 55)
check("a relay that is not an object is refused", w.parse_relay([1, 2]) is None and w.parse_relay("x") is None and w.parse_relay({"foo": 1}) is None)

gen = w.parse_relay({"temperature": {"value": 21.5, "unit": "°C"}, "humidity": {"value": 55, "unit": "%"}, "pressure": {"value": 1013.25, "unit": "hPa"}, "wind_speed": {"value": 10, "unit": "m/s"},
                     "wind_gust": {"value": 20, "unit": "km/h"}, "wind_direction": {"value": 270, "unit": "°"}, "rain_today": {"value": 25.4, "unit": "mm"}, "rain_rate": {"value": 2.54, "unit": "mm/h"},
                     "solar_radiation": {"value": 400, "unit": "W/m²"}, "uv_index": {"value": 5, "unit": "UV index"}, "lightning_count": {"value": 2, "unit": ""}, "lightning_distance": {"value": 10, "unit": "km"}})
check("values that carry their own unit are converted (the Home Assistant and bridge form)", gen and near(gen["temp_f"], 70.7, .1) and near(gen["press_in"], 29.92, .01) and near(gen["wind_mph"], 22.37, .02) and near(gen["gust_mph"], 12.43, .02)
      and gen["wind_dir"] == 270 and near(gen["rain_day_in"], 1, .01) and near(gen["rain_rate_in"], .1, .01) and gen["solar"] == 400 and gen["uv"] == 5 and gen["lightning_n"] == 2 and near(gen["lightning_mi"], 6.2, .05), gen)
check("kelvin, kPa, knots and inches are understood too", near(w.parse_relay({"temperature": {"value": 293.15, "unit": "K"}})["temp_f"], 68, .01) and near(w.parse_relay({"pressure": {"value": 101.325, "unit": "kPa"}, "humidity": {"value": 1}})["press_in"], 29.92, .01)
      and near(w.parse_relay({"wind_speed": {"value": 10, "unit": "kn"}, "humidity": {"value": 1}})["wind_mph"], 11.51, .02) and near(w.parse_relay({"rain_today": {"value": 0.5, "unit": "in"}, "humidity": {"value": 1}})["rain_day_in"], .5))
check("a value without a number is ignored", w.parse_relay({"temperature": {"value": "unavailable", "unit": "°C"}, "humidity": {"value": 40, "unit": "%"}}).get("temp_f") is None)

# ---- Ecowitt.net answer (units come from each value's own text)
cloud = {"code": 0, "msg": "success", "data": {
    "outdoor": {"temperature": {"time": "1", "unit": "℃", "value": "21.5"}, "dew_point": {"unit": "℃", "value": "12.0"}, "humidity": {"unit": "%", "value": "55"}},
    "wind": {"wind_speed": {"unit": "km/h", "value": "18"}, "wind_gust": {"unit": "km/h", "value": "30"}, "wind_direction": {"unit": "º", "value": "270"}},
    "pressure": {"relative": {"unit": "hPa", "value": "1015"}, "absolute": {"unit": "hPa", "value": "990"}},
    "rainfall": {"rain_rate": {"unit": "mm/hr", "value": "2.5"}, "daily": {"unit": "mm", "value": "12.7"}, "weekly": {"unit": "mm", "value": "25.4"}, "monthly": {"unit": "mm", "value": "50.8"}, "yearly": {"unit": "mm", "value": "508"}},
    "solar_and_uvi": {"solar": {"unit": "W/m²", "value": "300"}, "uvi": {"unit": "", "value": "4"}},
    "lightning": {"distance": {"unit": "km", "value": "5"}, "count": {"unit": "", "value": "3"}}}}
c = w.parse_ecowitt_cloud(cloud)
check("an Ecowitt.net answer in metric is converted by its unit text", c and near(c["temp_f"], 70.7, .1) and near(c["wind_mph"], 11.18, .02) and near(c["press_in"], 29.97, .02) and near(c["rain_day_in"], 0.5, .01) and near(c["rain_rate_in"], 0.098, .005) and near(c["lightning_mi"], 3.1, .05) and c["wind_dir"] == 270, c)
import copy
imp = copy.deepcopy(cloud); imp["data"]["outdoor"]["temperature"] = {"unit": "℉", "value": "70.0"}; imp["data"]["wind"]["wind_speed"] = {"unit": "mph", "value": "11"}
check("the same answer in imperial is read as is", near(w.parse_ecowitt_cloud(imp)["temp_f"], 70) and near(w.parse_ecowitt_cloud(imp)["wind_mph"], 11))
check("an Ecowitt error answer gives nothing", w.parse_ecowitt_cloud({"code": 40010, "msg": "Illegal Application_Key"}) is None and w.parse_ecowitt_cloud({}) is None and w.parse_ecowitt_cloud("x") is None)

# ---- Tempest
tp = w.parse_tempest({"obs": [{"air_temperature": 20.0, "relative_humidity": 50, "barometric_pressure": 1010.0, "wind_avg": 5.0, "wind_gust": 9.0, "wind_direction": 180, "precip": 0.1, "precip_accum_local_day": 2.54,
                              "solar_radiation": 200, "uv": 2.0, "lightning_strike_count_last_3hr": 2, "lightning_strike_last_distance": 10, "dew_point": 9.3}]})
check("a Tempest observation (SI values) is converted", tp and near(tp["temp_f"], 68) and near(tp["wind_mph"], 11.18, .02) and near(tp["press_in"], 29.83, .02) and near(tp["rain_day_in"], 0.1, .01) and near(tp["lightning_mi"], 6.2, .05) and tp["wind_dir"] == 180, tp)
check("an empty Tempest answer gives nothing", w.parse_tempest({"obs": []}) is None and w.parse_tempest({}) is None)

# ---- display units
check("display units: imperial and metric", w.show("temp_f", 68) == ("68.0", "°F") and w.show("temp_f", 68, "metric") == ("20.0", "°C") and w.show("wind_mph", 10, "metric") == ("16", "km/h")
      and w.show("press_in", 29.92, "metric")[1] == "hPa" and w.show("rain_day_in", 1, "metric") == ("25.4", "mm") and w.show("temp_f", None) == ("-", ""))

# ---- the sky and weather icon
noon = datetime(2026, 6, 21, 18, 0, tzinfo=timezone.utc)     # solar noon in Arkansas
night = datetime(2026, 6, 22, 5, 0, tzinfo=timezone.utc)     # midnight local
check("the sun is high at solar noon and well below the horizon at midnight", w.sun_elevation(34.7, -92.3, noon) > 70 and w.sun_elevation(34.7, -92.3, night) < -20, (w.sun_elevation(34.7, -92.3, noon), w.sun_elevation(34.7, -92.3, night)))
cond = lambda la, now: (w.condition(la, 34.7, -92.3, now) or (None, None))[0]
check("sunlight near a clear sky's is sunny, about half is partly cloudy, little is cloudy", cond({"solar": 950, "temp_f": 85}, noon) == "sunny" and cond({"solar": 500, "temp_f": 85}, noon) == "partly" and cond({"solar": 120, "temp_f": 85}, noon) == "cloud")
check("a dark sky is a clear night, and rain, a storm, wind and fog win over the sky", cond({"solar": 0, "hum": 60}, night) == "moon" and cond({"rain_rate_in": 0.2, "solar": 900}, noon) == "rain" and cond({"rain_rate_in": 0.2, "lightning_n": 3}, noon) == "storm"
      and cond({"gust_mph": 38, "solar": 900}, noon) == "windy" and cond({"hum": 97, "temp_f": 50, "dew_f": 49}, noon) == "fog")
check("a station without a sun sensor says nothing about the sky, and an empty reading gives nothing", cond({"temp_f": 70}, noon) is None and w.condition({}, 34, -92) is None and w.condition(None) is None)

# ---- storing, history, roll-up (a throw-away station)
with app.app_context():
    with psycopg.connect(app.config["DATABASE_URL"], row_factory=dict_row) as conn:
        conn.execute("DELETE FROM wx_stations WHERE station_id = 'UNITWX-1'")
        uid = conn.execute("SELECT id FROM users WHERE username='marty'").fetchone()["id"]
        wid = conn.execute("INSERT INTO wx_stations (station_id, owner_id, name, kind, token_hash, public) VALUES ('UNITWX-1', %s, 'Unit', 'relay', 'unitwxhash', TRUE) RETURNING id", (uid,)).fetchone()["id"]
        conn.commit()
        now = datetime.now(timezone.utc)
        stored = w.store(conn, wid, w.clean({"temp_f": 70, "hum": 50, "press_in": 29.9, "rain_day_in": 0.1}), now)
        check("the first reading is stored in the history", stored and conn.execute("SELECT COUNT(*) AS n FROM wx_readings WHERE station_pk=%s", (wid,)).fetchone()["n"] == 1)
        stored2 = w.store(conn, wid, w.clean({"temp_f": 71, "hum": 51, "wind_mph": 4}), now + timedelta(seconds=5))
        row = conn.execute("SELECT latest, last_report FROM wx_stations WHERE id=%s", (wid,)).fetchone()
        import json
        la = json.loads(row["latest"])
        check("a reading too soon after the last is not added to the history but updates the latest", not stored2 and la["temp_f"] == 71 and la["wind_mph"] == 4 and conn.execute("SELECT COUNT(*) AS n FROM wx_readings WHERE station_pk=%s", (wid,)).fetchone()["n"] == 1)
        check("a value the device did not send this time keeps its last value", la.get("press_in") == 29.9 and la.get("rain_day_in") == 0.1, la)
        # old raw rows become hourly rows
        conn.execute("DELETE FROM wx_readings WHERE station_pk=%s", (wid,))
        conn.execute("INSERT INTO wx_readings (station_pk, ts, temp_f, hum, press_in, wind_mph, gust_mph, rain_day_in, solar, uv) "
                     "SELECT %s, date_trunc('hour', now()) - interval '10 days' + g * interval '10 minutes', 50 + g, 60, 29.9, 5, 5 + g, g * 0.01, 100, 1 FROM generate_series(0, 11) g", (wid,))
        conn.execute("INSERT INTO wx_readings (station_pk, ts, temp_f, hum) SELECT %s, now() - interval '1 hour' + g * interval '1 minute', 70 + g, 50 FROM generate_series(0, 5) g", (wid,))
        conn.commit()
        w.rollup(conn)
        h = conn.execute("SELECT * FROM wx_hourly WHERE station_pk=%s ORDER BY hour", (wid,)).fetchall()
        check("raw readings older than the limit become hourly rows (two hours here) and are deleted", len(h) == 2 and conn.execute("SELECT COUNT(*) AS n FROM wx_readings WHERE station_pk=%s AND ts < now() - interval '5 days'", (wid,)).fetchone()["n"] == 0
              and conn.execute("SELECT COUNT(*) AS n FROM wx_readings WHERE station_pk=%s", (wid,)).fetchone()["n"] == 6, [dict(x) for x in h][:1])
        check("an hourly row keeps the average, low and high", h[0]["n"] == 6 and near(h[0]["temp_min"], 50) and near(h[0]["temp_max"], 55) and near(h[0]["temp_avg"], 52.5) and near(h[0]["gust_max"], 10), dict(h[0]))
        w.rollup(conn)
        check("rolling up twice changes nothing", conn.execute("SELECT COUNT(*) AS n FROM wx_hourly WHERE station_pk=%s", (wid,)).fetchone()["n"] == 2)
        with app.test_request_context("/"):
            from flask import g
            from app import db as _db
            ser = w.series(wid, "30d")
            check("the 30 day series joins the hourly rows and the recent raw rows", len(ser) >= 2 and any(50 <= (p["temp"] or 0) <= 62 for p in ser), ser)
            svg = str(w.chart_svg(w.series(wid, "24h"), [("temp", "#fff", "Temperature")], "24h"))
            check("a chart is an inline SVG with a line", "<svg" in svg and "<path" in svg, svg[:200])
            check("a chart with no data says so", "No readings" in str(w.chart_svg([], [("temp", "#fff", "T")], "24h")))
            gap = [{"t": now - timedelta(hours=20), "temp": 60}, {"t": now - timedelta(hours=19, minutes=55), "temp": 61}, {"t": now - timedelta(hours=2), "temp": 70}, {"t": now - timedelta(hours=1, minutes=55), "temp": 71}]
            check("a gap in the data breaks the line", str(w.chart_svg(gap, [("temp", "#fff", "T")], "24h")).count("M") >= 2)
            rows = w.daily(wid, 30)
            check("the daily summary has the days with data", len(rows) >= 2 and all(r["high"] is not None for r in rows), [dict(r) for r in rows])
            rec = w.records(wid)
            check("records find the highest and lowest", rec["high"] and rec["high"]["v"] >= 75 and rec["low"]["v"] <= 51, {k: (dict(v) if v else None) for k, v in rec.items()})
        conn.execute("DELETE FROM wx_stations WHERE id=%s", (wid,))
        conn.commit()
print("ALL PASS" if ok_all else "SOME FAILED")
