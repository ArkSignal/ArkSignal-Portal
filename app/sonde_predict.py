"""The portal's side of the prediction and archive service (deploy/predict, its own container and database): settings are sent to it, flights the portal recorded are
handed to it, and its forecasts of where the balloons of future launches will land are shown on the balloon pages. When the service is away every call returns a plain
note and the rest of the site works as before."""
import json
import urllib.error
import urllib.request

from .cache import cached
from .db import get_db
from .settings import cfg, cfg_int

SETTING_KEYS = {"predict_sites": "sites", "predict_since": "since", "predict_rate": "rate_per_second", "predict_date_mode": "date_mode", "predict_horizon_hours": "horizon_hours",
                "predict_lookback_days": "lookback_days", "predict_season_days": "season_days", "predict_persist_hours": "persist_hours", "predict_auto_import": "auto_import",
                "predict_methods": "methods", "predict_default_method": "default_method", "predict_wind_url": "wind_url", "predict_wind_cache_hours": "wind_cache_hours", "predict_descent_ms": "descent_ms",
                "predict_wind_source": "wind_source", "predict_gfs_enabled": "gfs_enabled", "predict_gfs_box": "gfs_box", "predict_gfs_hours": "gfs_hours", "predict_gfs_refresh_minutes": "gfs_refresh_minutes",
                "predict_tawhiri_url": "tawhiri_url", "predict_wind_horizon_hours": "wind_horizon_hours", "predict_wind_refresh_hours": "wind_refresh_hours", "predict_wind_max_per_day": "wind_max_per_day"}

WIND_SOURCES = {"auto": "Our own GFS data, else Open-Meteo", "gfs": "Our own GFS data only", "openmeteo": "Open-Meteo only"}
METHOD_NAMES = {"statistical": "Past flights (statistical)", "wind": "Forecast winds (our model)", "tawhiri": "Tawhiri"}


def clean_method(m):
    """A method name from a request, or the site's default."""
    m = (m or "").strip().lower()
    return m if m in METHOD_NAMES else (cfg("predict_default_method") if cfg("predict_default_method") in METHOD_NAMES else "statistical")


def enabled():
    return cfg("predict_enabled") == "1"


def call(path, method="GET", body=None, timeout=10):
    """(ok, data or message). Never raises."""
    if not enabled():
        return False, "The prediction service is switched off in Settings."
    url = cfg("predict_url").rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=dict({"User-Agent": cfg("lookup_user_agent"), "Content-Type": "application/json"}, **__import__("app.servicecall", fromlist=["headers"]).headers()))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, json.loads(r.read(20_000_000).decode("utf-8"))
    except urllib.error.HTTPError as e:
        return False, f"The prediction service answered with an error ({e.code})."
    except Exception as e:
        return False, "The prediction service did not answer (" + type(e).__name__ + ")."


def stats():
    def load():
        ok, d = call("/stats", timeout=3)
        return {"ok": ok, "data": d if ok else None, "note": "" if ok else d}
    return cached("predict_stats", load, 15)


def forecast(method=""):
    """{ok, predictions, scores, methods, note}: the next launches and where they should land by one predictor, with the service's own error history."""
    method = clean_method(method)

    def load():
        ok, d = call("/predict/next?method=" + method, timeout=60 if method != "statistical" else 10)
        if not ok:
            return {"ok": False, "predictions": [], "scores": None, "methods": [], "note": d}
        return {"ok": True, "predictions": d.get("predictions", []), "scores": d.get("scores"), "methods": d.get("methods", []), "note": ""}
    return cached(("predict_next", method), load, 300)


def forecast_at(site, when_iso, method=""):
    """{ok, prediction, note}: a forecast flight from one site at a chosen launch time (kept ten minutes per site and hour)."""
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,12}", site or "") or not when_iso:
        return {"ok": False, "prediction": None, "note": "Choose a launch site and a date and time."}
    hour = when_iso[:13]
    method = clean_method(method)

    def load():
        import urllib.parse
        ok, d = call("/predict/at?" + urllib.parse.urlencode({"site": site, "when": when_iso, "method": method}), timeout=60)
        if not ok:
            return {"ok": False, "prediction": None, "note": d}
        return {"ok": True, "prediction": d.get("prediction"), "note": d.get("note", "")}
    return cached(("predict_at", site, hour, method), load, 600)


def gfs_forecast(lat, lon):
    """{ok, rows, cycle, note}: the surface forecast of a place every three hours from the service's own NOAA GFS data (kept ten minutes per place)."""
    def load():
        ok, d = call(f"/gfs/forecast?lat={lat:.3f}&lon={lon:.3f}", timeout=8)
        if not ok:
            return {"ok": False, "rows": [], "note": d}
        return {"ok": bool(d.get("ok")), "rows": d.get("rows", []), "cycle": d.get("cycle"), "note": d.get("note", ""), "credit": d.get("credit", "")}
    return cached(("gfs_fc", round(lat, 2), round(lon, 2)), load, 600)


def gfs_refresh(force=True):
    return call("/gfs/refresh", "POST", {"force": bool(force)})


def site_names():
    return {r["site_id"]: r["name"] for r in get_db().execute("SELECT site_id, name FROM sonde_sites").fetchall()}


def push_config():
    cfgs = {k: cfg(s) for s, k in SETTING_KEYS.items()}
    return call("/config", "POST", cfgs)


def push_recent(conn, hours=96):
    """Hand the service the flights this portal recorded itself (the archive trails real time by days), so the newest winds count in the forecasts."""
    rows = conn.execute("SELECT serial, type, subtype, manufacturer, site_id, launch_time, launch_lat, launch_lon, launch_alt, max_alt_ts, max_lat, max_lon, max_alt, last_seen, landing_lat, landing_lon, landing_alt, status "
                        "FROM sondes WHERE site_id <> '' AND launch_time IS NOT NULL AND last_seen > now() - make_interval(hours => %s) AND status IN ('landed', 'lost') ORDER BY last_seen DESC LIMIT 300", (hours,)).fetchall()
    flights = []
    for r in rows:
        f = {"serial": r["serial"], "site": r["site_id"], "type": r["type"], "subtype": r["subtype"], "manufacturer": r["manufacturer"], "launch_ts": r["launch_time"].isoformat(),
             "launch_lat": r["launch_lat"], "launch_lon": r["launch_lon"], "launch_alt": r["launch_alt"], "burst_ts": r["max_alt_ts"] and r["max_alt_ts"].isoformat(),
             "burst_lat": r["max_lat"], "burst_lon": r["max_lon"], "burst_alt": r["max_alt"], "lost": r["status"] == "lost"}
        if r["status"] == "landed" and r["landing_lat"] is not None:
            f.update(land_ts=r["last_seen"].isoformat(), land_lat=r["landing_lat"], land_lon=r["landing_lon"], land_alt=r["landing_alt"])
        flights.append(f)
    if not flights:
        return True, {"stored": 0}
    return call("/ingest", "POST", {"flights": flights}, timeout=30)


def start_import():
    push_config()
    return call("/import/start", "POST", {})


def stop_import():
    return call("/import/stop", "POST", {})
