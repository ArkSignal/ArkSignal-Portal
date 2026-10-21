"""Builds weather-dashboard.json and sondes-dashboard.json: the filtered statistics (Infinity data source reading the portal's statistics files) on top, the dashboards' own network panels from the .base.json files below. Run: python make_stats_dashboards.py"""
import json
DS = {"type": "yesoreyeram-infinity-datasource", "uid": "portal-json"}
BASE = "http://adsb-portal:8430"

def var_text(name, label, default=""):
    return {"type": "textbox", "name": name, "label": label, "query": default, "current": {"text": default, "value": default}, "options": [{"selected": True, "text": default, "value": default}], "hide": 0}

def var_custom(name, label, values, multi=False, default=None):
    cur = default or values[0]
    return {"type": "custom", "name": name, "label": label, "query": ",".join(values), "multi": multi, "includeAll": False,
            "current": {"text": cur, "value": cur}, "options": [{"selected": v == cur, "text": v, "value": v} for v in values], "hide": 0}

def query(path, params, root, cols, fmt="table"):
    return {"refId": "A", "datasource": DS, "type": "json", "source": "url", "format": fmt, "url": BASE + path, "root_selector": root,
            "url_options": {"method": "GET", "params": [{"key": k, "value": v} for k, v in params]}, "columns": [{"selector": s, "text": t, "type": ty} for s, t, ty in cols]}

def panel(i, title, kind, x, y, w, h, q, unit=None, extra=None):
    p = {"id": i, "type": kind, "title": title, "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h}, "targets": [q],
         "fieldConfig": {"defaults": {"unit": unit or "none"}, "overrides": []}, "options": {}}
    if extra: p.update(extra)
    return p

def dash(uid, title, variables, panels):
    return {"uid": uid, "title": title, "tags": ["arksignal"], "timezone": "browser", "schemaVersion": 39, "version": 1, "refresh": "5m", "time": {"from": "now-24h", "to": "now"}, "editable": True,
            "templating": {"list": variables}, "panels": panels, "annotations": {"list": []}}

# ---- weather
wp = [("sources", "${sources:csv}"), ("metric", "${reading}"), ("period", "${period}"), ("counties", "${counties}"), ("cities", "${cities}"), ("zips", "${zips}"), ("stations", "${stations}"), ("near", "${near}")]
wvars = [var_custom("sources", "Sources", ["members", "aprs", "airport"], True, "members"), var_custom("reading", "Reading", ["temp_f", "dew_f", "hum", "press_in", "wind_mph", "gust_mph", "rain_day_in", "uv", "cloud_pct"]),
         var_custom("period", "Period", ["24h", "6h", "3d", "7d", "30d"]), var_text("counties", "Counties (Name|Name)"), var_text("cities", "Cities (Name|Name)"), var_text("zips", "ZIP codes (72201|72032)"),
         var_text("stations", "Stations (members:ID|aprs:CALL|airport:KLIT)"), var_text("near", "Near (lat,lon,miles)")]
ts_cols = [("ts", "Time", "timestamp"), ("max", "Highest", "number"), ("avg", "Average", "number"), ("min", "Lowest", "number")]
wd = dash("weather", "Weather Dashboard", wvars, [
    panel(1, "Highest, average and lowest of the chosen stations", "timeseries", 0, 0, 24, 10, query("/api/weather/stats.json", wp, "series", ts_cols), extra={"options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}}}),
    panel(2, "Stations", "table", 0, 10, 24, 12, query("/api/weather/stats.json", wp, "stations", [("name", "Station", "string"), ("src", "Source", "string"), ("city", "City", "string"), ("county", "County", "string"), ("latest", "Latest", "number"), ("min", "Lowest", "number"), ("avg", "Average", "number"), ("max", "Highest", "number"), ("n", "Readings", "number")]))])
# ---- radiosondes
sp = [("sources", "${kinds:csv}"), ("sites", "${sites}"), ("stations", "${stations}"), ("flights", "${flights}"), ("from", "${__from:date:YYYY-MM-DD}"), ("to", "${__to:date:YYYY-MM-DD}"), ("status", "${status}")]
svars = [var_custom("kinds", "Kinds", ["nws", "amateur", "other"], True, "nws"), var_custom("status", "Status", ["", "aloft", "landed", "lost"]), var_text("sites", "Launch sites (72340|72248)"), var_text("stations", "Receiving stations (CALL|CALL)"), var_text("flights", "Flights (serial|serial)")]
sd = dash("sondes", "Radiosonde Dashboard", svars, [
    panel(1, "Flights per day", "barchart", 0, 0, 12, 8, query("/api/sondes/stats.json", sp, "per_day", [("day", "Day", "string"), ("n", "Flights", "number")])),
    panel(2, "How high flights burst", "barchart", 12, 0, 12, 8, query("/api/sondes/stats.json", sp, "burst_hist", [("from_m", "Burst height from (m)", "number"), ("n", "Flights", "number")])),
    panel(3, "Receiving stations that heard the most flights", "table", 0, 8, 8, 10, query("/api/sondes/stats.json", sp, "top_stations", [("callsign", "Station", "string"), ("flights", "Flights", "number")])),
    panel(4, "Flights", "table", 8, 8, 16, 10, query("/api/sondes/stats.json", sp, "flights", [("serial", "Flight", "string"), ("type", "Type", "string"), ("site", "Launch site", "string"), ("status", "Status", "string"), ("max_alt", "Burst height (m)", "number")]))])
def merge(base_file, out_file, new, title, uid):
    """The existing Prometheus panels of the dashboard (the network's own metrics) go under the filtered statistics: one dashboard, filters on top, the overview below."""
    base = json.load(open(base_file, encoding="utf-8"))
    shift = max(p["gridPos"]["y"] + p["gridPos"]["h"] for p in new["panels"])
    panels = list(new["panels"])
    nid = max(p["id"] for p in panels) + 1
    for p in base["panels"]:
        p = json.loads(json.dumps(p)); p["id"] = nid; nid += 1; p["gridPos"]["y"] += shift; panels.append(p)
    new = dict(new); new["panels"] = panels; new["title"] = title; new["uid"] = uid
    json.dump(new, open(out_file, "w"), indent=1)

merge("weather-dashboard.base.json", "weather-dashboard.json", wd, "Weather Dashboard", "weather")
merge("sondes-dashboard.base.json", "sondes-dashboard.json", sd, "Radiosonde Dashboard", "sondes")
print("written")
