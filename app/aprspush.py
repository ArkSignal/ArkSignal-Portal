"""Send a weather station to the APRS network: a member chooses, on their own weather station's page, to have its latest reading sent as an APRS weather report to the CWOP (Citizen Weather Observer Program, which
feeds NOAA's MADIS) or the APRS-IS network that amateur radio operators use, so it shows on aprs.fi and findU and reaches the Weather Service.

Nothing is sent unless the owner switches it on, accepts that the readings and the position they chose are public, and gives a CWOP ID or their own call sign. A background loop (one web worker at a time, by a
database lock) sends a station's newest reading every few minutes, as one short connection per report. The packet follows the APRS 1.01 weather format; missing values are sent as dots. A call sign sent to
APRS-IS must be the owner's (it is checked against the call sign on their profile unless an administrator turned that check off), and its passcode is worked out the public way unless the owner gives one."""
import re
import socket
import time
from datetime import datetime, timezone

from flask import Blueprint, abort, jsonify, request, render_template
from flask_login import current_user, login_required

from . import weather as wx
from .action_response import action_response
from .audit import audit
from .db import get_db
from .settings import cfg, cfg_int

bp = Blueprint("aprspush", __name__)

LOCK_ID = 727021
HAM_RE = re.compile(r"^[A-Z0-9]{1,2}[0-9][A-Z0-9]{1,4}(-([0-9]|1[0-5]))?$")
CWOP_RE = re.compile(r"^[A-Z]{1,2}[0-9]{3,6}$")
MODES = {"cwop": "CWOP (Citizen Weather Observer Program)", "aprs": "APRS-IS (amateur radio)"}


def passcode(call):
    """The APRS-IS passcode of a call sign (the public hash: the SSID does not count)."""
    base = (call or "").upper().split("-")[0]
    h = 0x73E2
    for i in range(0, len(base), 2):
        h ^= ord(base[i]) << 8
        if i + 1 < len(base):
            h ^= ord(base[i + 1])
    return h & 0x7FFF


def _num(v, width, scale=1, signed=False):
    """A weather field of `width` digits (three dots when the station sent nothing)."""
    if v is None:
        return "." * width
    n = int(round(v * scale))
    if signed and n < 0:
        return "-" + str(min(-n, 10 ** (width - 1) - 1)).zfill(width - 1)
    return str(max(0, min(n, 10 ** width - 1))).zfill(width)


def _lat(v):
    a = abs(v)
    return "%02d%05.2f%s" % (int(a), (a - int(a)) * 60, "N" if v >= 0 else "S")


def _lon(v):
    a = abs(v)
    return "%03d%05.2f%s" % (int(a), (a - int(a)) * 60, "E" if v >= 0 else "W")


def packet(call, lat, lon, la, when):
    """The APRS weather report: `CALL>APRS,TCPIP*:@DDHHMMz<position>_<wind dir>/<speed>g<gust>t<temp>r<rain 1h>p<rain 24h>P<rain today>h<humidity>b<pressure>[L<solar>]eArkS`.
    `la` is the station's latest reading (a dict); wind is in mph, temperature in degrees F, rain in hundredths of an inch, pressure in tenths of a hectopascal."""
    wd = la.get("wind_dir")
    wind_dir = "..." if wd is None else ("360" if int(round(wd)) % 360 == 0 and (la.get("wind_mph") or 0) > 0 else str(int(round(wd)) % 360).zfill(3))
    hum = la.get("hum")
    h = ".." if hum is None else ("00" if int(round(hum)) >= 100 else str(max(1, int(round(hum)))).zfill(2))
    press = la.get("press_in")
    b = "....." if press is None else str(int(round(press * 33.8639 * 10))).zfill(5)
    solar = la.get("solar")
    lum = "" if solar is None or solar < 0 else ("L" + str(int(round(solar))).zfill(3) if solar < 1000 else "l" + str(min(int(round(solar)) - 1000, 999)).zfill(3))
    return (f"{call}>APRS,TCPIP*:@{when:%d%H%M}z{_lat(lat)}/{_lon(lon)}_{wind_dir}/{_num(la.get('wind_mph'), 3)}g{_num(la.get('gust_mph'), 3)}t{_num(la.get('temp_f'), 3, signed=True)}"
            f"r...p...P{_num(la.get('rain_day_in'), 3, 100)}h{h}b{b}{lum}eArkS")


# ------------------------------------------------------------------ the data

def get(wid):
    return get_db().execute("SELECT * FROM wx_push WHERE station_pk=%s", (wid,)).fetchone()


def context(row):
    """What the station page's APRS section shows."""
    p = get(row["id"])
    owner_call = (row["owner_callsign"] or "").upper()
    return {"p": p, "on": cfg("aprspush_enabled") == "1", "modes": MODES, "owner_call": owner_call, "match": cfg("aprspush_ham_match_profile") == "1", "interval": cfg_int("aprspush_interval_minutes"),
            "has_position": row["plat"] is not None and row["plon"] is not None, "level": row["pprivacy"]}


def _position(row, choice):
    from . import privacy
    if row["plat"] is None or row["plon"] is None:
        return None
    return (row["plat"], row["plon"]) if choice == "exact" else privacy.position(row["plat"], row["plon"], row["pprivacy"])


def build(row, p, la=None):
    """(packet text, error) for a station and its push settings."""
    pos = _position(row, p["position"])
    if pos is None:
        return None, "There is no position to send: the station has none, or its location privacy is Hidden. Choose Exact (and accept that it is public) or change the location privacy."
    la = la if la is not None else wx.latest_of(row)
    ts = row["last_report"]
    if not la or ts is None:
        return None, "The station has not reported a reading yet."
    return packet(p["callsign"], pos[0], pos[1], la, ts.astimezone(timezone.utc)), None


def _server(mode):
    host, _sep, port = cfg("aprspush_server_cwop" if mode == "cwop" else "aprspush_server_aprs").rpartition(":")
    return host or "cwop.aprs.net", int(port) if port.isdigit() else 14580


def _unseal(p):
    from .vault import unseal
    try:
        return unseal(p["passcode_sealed"]) if p["passcode_sealed"] else ""
    except Exception:
        return ""


def send(row, p, text):
    """Log in to the server and send one report. Returns (ok, message)."""
    host, port = _server(p["mode"])
    call = p["callsign"]
    code = "-1" if p["mode"] == "cwop" else (_unseal(p) or str(passcode(call)))
    try:
        with socket.create_connection((host, port), timeout=cfg_int("aprspush_timeout_seconds")) as s:
            s.settimeout(cfg_int("aprspush_timeout_seconds"))
            f = s.makefile("rwb", buffering=0)
            f.readline()   # the server's greeting
            f.write(f"user {call} pass {code} vers ArkSignal 1.0\r\n".encode())
            reply = f.readline().decode("utf-8", "replace").strip()
            if p["mode"] == "aprs" and ("unverified" in reply.lower() or "verified" not in reply.lower()):
                return False, "The APRS-IS server did not accept the call sign and passcode."
            f.write((text + "\r\n").encode())
            time.sleep(0.4)   # the server does not answer a report: a moment for it to take the line before the connection closes
    except (OSError, ValueError) as e:
        return False, f"Could not reach {host}:{port} ({type(e).__name__})."
    return True, f"Sent to {host}."


def push_one(row, force=False):
    """Send a station's newest reading (when it is due, or at once with `force`). Returns (ok, message)."""
    p = get(row["id"])
    if p is None or (not p["enabled"] and not force):
        return False, "APRS is not switched on for this station."
    ts = row["last_report"]
    if not force:
        if ts is None or (datetime.now(timezone.utc) - ts).total_seconds() > cfg_int("aprspush_max_age_minutes") * 60:
            return False, "no recent reading"
        if p["last_reading"] is not None and ts <= p["last_reading"]:
            return False, "no new reading"
    text, err = build(row, p)
    d = get_db()
    if err:
        d.execute("UPDATE wx_push SET last_push=now(), last_ok=FALSE, last_result=%s WHERE station_pk=%s", (err[:200], row["id"]))
        d.commit()
        return False, err
    ok, msg = send(row, p, text)
    d.execute("UPDATE wx_push SET last_push=now(), last_ok=%s, last_result=%s, fail_count=CASE WHEN %s THEN 0 ELSE fail_count + 1 END, sent=sent + CASE WHEN %s THEN 1 ELSE 0 END, last_reading=CASE WHEN %s THEN %s ELSE last_reading END WHERE station_pk=%s",
              (ok, msg[:200], ok, ok, ok, ts, row["id"]))
    d.commit()
    return ok, msg


def due(conn):
    """Stations to send now."""
    return conn.execute(wx.SELECT + "JOIN wx_push p ON p.station_pk = w.id WHERE p.enabled AND NOT w.disabled AND (p.last_push IS NULL OR p.last_push < now() - make_interval(mins => %s)) "
                        "AND (p.fail_count < 8 OR p.last_push < now() - interval '6 hours')", (max(5, cfg_int("aprspush_interval_minutes")),)).fetchall()


def start_push(app):
    """A background loop in every web worker; a database lock means only one does the work at a time."""
    import threading
    import psycopg
    from psycopg.rows import dict_row

    def loop():
        time.sleep(75)
        while True:
            try:
                with app.app_context():
                    on = cfg("aprspush_enabled") == "1"
                if on:
                    with psycopg.connect(app.config["DATABASE_URL"], autocommit=True) as lock_conn:
                        if lock_conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,)).fetchone()[0]:
                            try:
                                with app.app_context():
                                    for row in due(get_db()):
                                        push_one(row)
                            finally:
                                lock_conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
            except Exception:
                pass
            time.sleep(60)
    threading.Thread(target=loop, name="aprs-push", daemon=True).start()


# ------------------------------------------------------------------ the station page

def _owned(wid):
    from .weather_views import _owned as owned
    return owned(wid)


def _response(row, ok, message):
    return action_response(ok, message, redirect_endpoint="wx.my_weather_station", redirect_kwargs={"wid": row["id"]}, fragment_template="partials/_wx_aprs.html",
                           fragment_context=dict(context(_owned(row["id"])), r=_owned(row["id"]), managing=""))


def _on():
    if cfg("aprspush_enabled") != "1":
        abort(404)


@bp.route("/my/weather/<int:wid>/aprs/save", methods=["POST"])
@login_required
def save(wid):
    _on()
    row = _owned(wid)
    f = request.form
    mode = f.get("mode", "cwop")
    call = re.sub(r"[^A-Za-z0-9-]", "", f.get("callsign", "")).upper()[:12]
    enabled = bool(f.get("enabled"))
    position = "exact" if f.get("position") == "exact" else "public"
    if mode not in MODES:
        return _response(row, False, "Choose where to send it.")
    if enabled:
        if mode == "cwop" and not (CWOP_RE.match(call) or HAM_RE.match(call)):
            return _response(row, False, "A CWOP ID looks like CW1234 or DW5678 (you get it when you register your station at wxqa.com); a call sign works too.")
        if mode == "aprs":
            if not HAM_RE.match(call):
                return _response(row, False, "Enter your amateur radio call sign, with an SSID such as -13 if you like (KD5VCF-13).")
            owner = (row["owner_callsign"] or "").upper().split("-")[0]
            if cfg("aprspush_ham_match_profile") == "1" and (not owner or call.split("-")[0] != owner):
                return _response(row, False, "APRS-IS is for licensed operators: the call sign must be the one on the owner's profile" + (f" ({owner})." if owner else ", and there is none yet (My Account > Profile)."))
        if not f.get("consent"):
            return _response(row, False, "Tick the box to confirm you understand the readings and the position are public once they are sent.")
        if position == "exact" and not f.get("exact_ok"):
            return _response(row, False, "To send the exact position, tick that you accept it becomes public.")
        if _position(row, position) is None:
            return _response(row, False, "There is no position to send: the station has none, or its location privacy is Hidden. Choose Exact (and accept that it is public) or change the location privacy.")
    code = (f.get("passcode") or "").strip()
    if code and not re.match(r"^-?\d{1,6}$", code):
        return _response(row, False, "A passcode is a number.")
    from .vault import seal
    d = get_db()
    d.execute("INSERT INTO wx_push (station_pk, enabled, mode, callsign, passcode_sealed, position) VALUES (%s,%s,%s,%s,%s,%s) "
              "ON CONFLICT (station_pk) DO UPDATE SET enabled=EXCLUDED.enabled, mode=EXCLUDED.mode, callsign=EXCLUDED.callsign, position=EXCLUDED.position, "
              "passcode_sealed=CASE WHEN %s <> '' THEN EXCLUDED.passcode_sealed ELSE wx_push.passcode_sealed END, fail_count=0",
              (wid, enabled, mode, call, seal(code) if code else "", position, code))
    d.commit()
    audit("wx.aprs_save", target=str(wid), detail=f"{'on' if enabled else 'off'}, {mode}, {call}, {position}")
    return _response(row, True, "Saved. The station's newest reading goes out every few minutes." if enabled else "Saved. Nothing is sent.")


@bp.route("/my/weather/<int:wid>/aprs/preview", methods=["POST"])
@login_required
def preview(wid):
    _on()
    row = _owned(wid)
    p = get(wid)
    if p is None or not p["callsign"]:
        return jsonify({"ok": False, "message": "Save the call sign or CWOP ID first."}), 400
    text, err = build(row, p)
    if err:
        return jsonify({"ok": False, "message": err}), 400
    return jsonify({"ok": True, "packet": text})


@bp.route("/my/weather/<int:wid>/aprs/send", methods=["POST"])
@login_required
def send_now(wid):
    _on()
    row = _owned(wid)
    p = get(wid)
    if p is None or not p["callsign"]:
        return _response(row, False, "Save the call sign or CWOP ID first.")
    ok, msg = push_one(row, force=True)
    audit("wx.aprs_send", target=str(wid), detail=msg)
    return _response(row, ok, msg if ok else "Not sent: " + msg)
