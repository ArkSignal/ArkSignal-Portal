"""Airline schedules for My Flights: the scheduled times, terminals and aircraft of a flight number on a date, to fill the Scheduled column of a diary flight.

Source: AeroDataBox (https://aerodatabox.com/), "flight status by number and date", reached through the API.Market or RapidAPI marketplace with a key an administrator enters (Settings > Aircraft Lookups: Airline
Schedules). Its terms are recorded in DATA_SOURCES_TERMS.md: the free Basic plan needs an attribution and is for non-commercial use, data may be kept for 7 days at most (so the answers are cached for the days set in
Settings, never more than seven), and nothing is passed on to third parties (a member's lookup is shown only to that member, as a suggestion they confirm). The free plan is small (400 units a month, two units for this
kind of call), so there are limits per member per day and for the whole site per month, and every answer is cached. Off until an administrator turns it on and enters a key; the key is sealed and never shown or logged."""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

from . import tzplace
from .audit import audit
from .db import get_db
from .settings import cfg, cfg_int

bp = Blueprint("schedules", __name__)

PROVIDERS = {
    "apimarket": ("https://prod.api.market/api/v1/aedbx/aerodatabox", lambda key: {"x-api-market-key": key}),
    "rapidapi": ("https://aerodatabox.p.rapidapi.com", lambda key: {"x-rapidapi-key": key, "x-rapidapi-host": "aerodatabox.p.rapidapi.com"}),
}
MAX_CACHE_DAYS = 7   # the source's own limit on keeping its data


def configured():
    return cfg("schedules_enabled") == "1" and bool(cfg("schedules_key"))


def base_url():
    return (cfg("schedules_base_url").strip() or PROVIDERS.get(cfg("schedules_provider"), PROVIDERS["apimarket"])[0]).rstrip("/")


def _clean_number(q):
    """'WN 1672' -> 'WN1672' (airline code and number) or None."""
    q = re.sub(r"[^A-Z0-9]", "", (q or "").upper())
    return q if re.match(r"^[A-Z0-9]{2,3}[0-9]{1,4}[A-Z]?$", q) and not (q[0] == "N" and q[1].isdigit()) else None


def _utc(t):
    """A time like '2026-11-02 14:35Z' or '2026-11-02T14:35:00+00:00' as an aware UTC time, or None."""
    if not t or not isinstance(t, str):
        return None
    t = t.strip().replace("Z", "+00:00")
    if "T" not in t:
        t = t.replace(" ", "T", 1)
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _time(m):
    st = (m or {}).get("scheduledTime") or {}
    return _utc(st.get("utc") if isinstance(st, dict) else st)


def parse(body):
    """The flights in an answer as [{number, airline, from, to, sched_out, sched_in, terminal_from, terminal_to, aircraft, registration}] (times as UTC); anything missing is left empty."""
    out = []
    for f in body if isinstance(body, list) else []:
        if not isinstance(f, dict):
            continue
        dep, arr = f.get("departure") or {}, f.get("arrival") or {}
        a, b = dep.get("airport") or {}, arr.get("airport") or {}
        out.append({"number": str(f.get("number") or ""), "airline": str((f.get("airline") or {}).get("name") or ""),
                    "from": {"iata": a.get("iata") or "", "icao": a.get("icao") or "", "name": a.get("name") or ""}, "to": {"iata": b.get("iata") or "", "icao": b.get("icao") or "", "name": b.get("name") or ""},
                    "sched_out": _time(dep), "sched_in": _time(arr), "terminal_from": str(dep.get("terminal") or ""), "terminal_to": str(arr.get("terminal") or ""),
                    "aircraft": str((f.get("aircraft") or {}).get("model") or ""), "registration": str((f.get("aircraft") or {}).get("reg") or "")})
    return out


def _http(number, day):
    """(status, body) from the source, or (0, error text)."""
    headers = PROVIDERS.get(cfg("schedules_provider"), PROVIDERS["apimarket"])[1](cfg("schedules_key"))
    headers.update({"Accept": "application/json", "User-Agent": cfg("lookup_user_agent")})
    url = f"{base_url()}/flights/number/{urllib.parse.quote(number)}/{urllib.parse.quote(day)}?dateLocalRole=Both&withAircraftImage=false&withLocation=false"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=cfg_int("schedules_timeout_seconds")) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace") or "[]")
    except urllib.error.HTTPError as e:
        try:
            current_app.logger.warning("schedules: the service answered %s for %s on %s: %s", e.code, number, day, e.read(300).decode("utf-8", "replace"))
        except Exception:
            pass
        return e.code, None
    except Exception as e:
        current_app.logger.warning("schedules: no answer for %s on %s: %s %s", number, day, type(e).__name__, str(e)[:160])
        return 0, type(e).__name__


def lookup(user_id, number, day):
    """({'flights': [...], 'cached': bool} or {'error': text}). Cached answers are free; a real call is counted against the member's day and the site's month first."""
    d = get_db()
    key = f"{number}|{day}"
    days = min(MAX_CACHE_DAYS, max(1, cfg_int("schedules_cache_days")))
    hit = d.execute("SELECT body FROM flight_schedule_cache WHERE key=%s AND fetched > now() - make_interval(days => %s)", (key, days)).fetchone()
    if hit:
        return {"flights": json.loads(hit["body"]), "cached": True}
    if d.execute("SELECT COUNT(*) AS n FROM flight_schedule_calls WHERE user_id=%s AND ts > now() - interval '24 hours'", (user_id,)).fetchone()["n"] >= cfg_int("schedules_per_member_day"):
        return {"error": f"You have used your {cfg_int('schedules_per_member_day')} schedule look-ups for today. Try again tomorrow, or fill the times in by hand."}
    if d.execute("SELECT COUNT(*) AS n FROM flight_schedule_calls WHERE ts > date_trunc('month', now())").fetchone()["n"] >= cfg_int("schedules_site_month"):
        return {"error": "The schedule look-ups for this month have all been used. Fill the times in by hand."}
    d.execute("INSERT INTO flight_schedule_calls (user_id) VALUES (%s)", (user_id,))
    d.commit()
    status, body = _http(number, day)
    if status == 404:
        body, status = [], 200
    if status != 200 or not isinstance(body, list):
        d.execute("UPDATE flight_schedule_calls SET ok=false WHERE id = (SELECT max(id) FROM flight_schedule_calls WHERE user_id=%s)", (user_id,))
        d.commit()
        if status in (401, 403):
            msg = "The schedule service did not accept the key (check the key, and that the marketplace chosen in Settings is the one it was bought from)."
        elif status == 429:
            msg = "The schedule service says the plan's limit has been used. Try again later, or fill the times in by hand."
        elif status in (400, 422):
            msg = "The schedule service did not accept that flight number or date. It covers recent and coming flights (about the last year and the next few months): try a nearer date."
        elif status == 0:
            msg = "The site could not reach the schedule service. Try again later."
        else:
            msg = f"The schedule service had a problem (error {status}). Try again later."
        return {"error": msg}
    flights = [dict(f, sched_out=f["sched_out"].isoformat() if f["sched_out"] else "", sched_in=f["sched_in"].isoformat() if f["sched_in"] else "") for f in parse(body)]
    d.execute("INSERT INTO flight_schedule_cache (key, body) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET body=EXCLUDED.body, fetched=now()", (key, json.dumps(flights)))
    d.execute("DELETE FROM flight_schedule_cache WHERE fetched < now() - interval '7 days'")
    d.execute("DELETE FROM flight_schedule_calls WHERE ts < now() - interval '62 days'")
    d.commit()
    return {"flights": flights, "cached": False}


@bp.route("/my/flights/schedule.json")
@login_required
def schedule_json():
    from .myflights import _on, local_text
    from .myflightsio import _airport_of
    _on()
    if not configured():
        return jsonify({"flights": [], "note": "Airline schedules are not set up on this site."}), 404
    number = _clean_number(request.args.get("q"))
    day = (request.args.get("date") or "").strip()[:10]
    if not number:
        return jsonify({"flights": [], "note": "Type the flight number, such as WN 1672."})
    if not day:   # no date typed: today, in the site's display time zone
        from zoneinfo import ZoneInfo
        try:
            day = datetime.now(ZoneInfo(cfg("timezone"))).date().isoformat()
        except Exception:
            day = datetime.now(timezone.utc).date().isoformat()
    try:
        datetime.fromisoformat(day)
    except ValueError:
        return jsonify({"flights": [], "note": "Choose the date of the flight."})
    res = lookup(current_user.id, number, day)
    if "error" in res:
        return jsonify({"flights": [], "note": res["error"]})
    out = []
    for f in res["flights"]:
        a, b = _airport_of(f["from"]), _airport_of(f["to"])
        item = {k: f[k] for k in ("number", "airline", "aircraft", "registration", "terminal_from", "terminal_to")}
        item["from"], item["to"] = a, b
        item["from_code"], item["to_code"] = f["from"]["iata"] or f["from"]["icao"], f["to"]["iata"] or f["to"]["icao"]
        item["from_name"] = (a or {}).get("name") or f["from"].get("name") or ""
        item["to_name"] = (b or {}).get("name") or f["to"].get("name") or ""
        for side, ap_, col in (("sched_out", a, "from"), ("sched_in", b, "to")):
            when = _utc(f[side])
            zone = None
            if ap_:
                from .myflights import _airport
                row = _airport(ap_["id"])
                zone = tzplace.zone_for(row["country"], row["lat"], row["lon"])
            item[side] = local_text(when, zone) if (when and zone) else ""
        out.append(item)
    return jsonify({"flights": out, "cached": res["cached"], "note": "" if out else "The schedule has no flight with that number on that date.",
                    "credit": "Schedule data from AeroDataBox."})
