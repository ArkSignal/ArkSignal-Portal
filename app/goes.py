"""NOAA GOES imagery: the newest pictures from the GOES-East and GOES-West weather satellites for the Arkansas region and the whole country, on Weather > GOES Imagery.

Source: NOAA/NESDIS STAR's public image server (cdn.star.nesdis.noaa.gov), a US Government work in the public domain (see DATA_SOURCES_TERMS.md). The site fetches each picture itself no more often than the
refresh setting (a conditional request, so an unchanged picture costs almost nothing), keeps the newest copy of each (and, for the animation loops, a short rolling set of timed pictures) in the database and serves it from there, so visitors never load NOAA's server and a short
outage on their side shows the last picture with its age. Nothing is fetched for a picture nobody asks for."""
import re
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from flask import Blueprint, Response, abort, jsonify, render_template, request

from .db import get_db
from .settings import cfg, cfg_int

bp = Blueprint("goes", __name__)

BASE = "https://cdn.star.nesdis.noaa.gov"
# key: (label, satellite and where it looks from, address pattern)
SECTORS = {
    "smv": ("Southern Mississippi Valley (Arkansas)", "GOES-19 (East)", "GOES19/ABI/SECTOR/smv/{p}/1200x1200.jpg"),
    "sp": ("Southern Plains", "GOES-19 (East)", "GOES19/ABI/SECTOR/sp/{p}/1200x1200.jpg"),
    "conus": ("Continental US, East View", "GOES-19 (East)", "GOES19/ABI/CONUS/{p}/1250x750.jpg"),
    "conus-west": ("Continental US, West View", "GOES-18 (West)", "GOES18/ABI/CONUS/{p}/1250x750.jpg"),
    "fd": ("Full Disk, East (the Whole Hemisphere)", "GOES-19 (East)", "GOES19/ABI/FD/{p}/{fd}.jpg"),
    "fd-west": ("Full Disk, West (Pacific)", "GOES-18 (West)", "GOES18/ABI/FD/{p}/{fd}.jpg"),
}
# the area part of the name NOAA gives each timed picture: <year><day of year><hour><minute>_<satellite>-ABI-<area>-<picture>-<size>.jpg
FRAME_TOKEN = {"smv": "smv", "sp": "sp", "conus": "CONUS", "conus-west": "CONUS", "fd": "FD", "fd-west": "FD"}
PRODUCTS = {
    "GEOCOLOR": ("GeoColor", "Natural color by day and a blue-and-white infrared view at night: the picture most like looking down from space."),
    "13": ("Clean Infrared", "Cloud-top temperature day and night: the colder (whiter or redder) the cloud top, the higher it is and the stronger the storm."),
    "09": ("Mid-Level Water Vapor", "Moisture in the middle air. It shows the jet stream and storms forming before clouds appear."),
    "AirMass": ("Air Mass", "Color-coded air types: shows jet streams, dry stratospheric air and warm, moist air masses."),
    "02": ("Red Visible", "The sharpest daytime picture of clouds (black at night)."),
}
_locks = {}
_guard = threading.Lock()


def enabled():
    return cfg("goes_enabled") == "1"


def csv(key, allowed):
    return [x for x in (p.strip() for p in cfg(key).split(",")) if x in allowed]


def sectors():
    return csv("goes_sectors", SECTORS)


def products():
    return csv("goes_products", PRODUCTS)


def _path(sector, product):
    return SECTORS[sector][2].format(p=product, fd=cfg("goes_fd_size") if cfg("goes_fd_size") in ("678x678", "1808x1808") else "1808x1808")


def _fetch(sector, product):
    """Bring the stored picture up to date (when it is older than the refresh setting) and return the row, or None when there is none."""
    d = get_db()
    row = d.execute("SELECT data, content_type, source_time, fetched_at FROM goes_images WHERE sector=%s AND product=%s", (sector, product)).fetchone()
    if row and row["fetched_at"] > datetime.now(timezone.utc) - timedelta(minutes=max(1, cfg_int("goes_refresh_minutes"))):
        return row
    with _guard:
        lock = _locks.setdefault((sector, product), threading.Lock())
    if not lock.acquire(blocking=False):   # another visitor is already fetching it: show what there is
        return row
    try:
        req = urllib.request.Request(f"{BASE}/{_path(sector, product)}", headers={"User-Agent": cfg("lookup_user_agent")})
        if row and row["source_time"]:
            req.add_header("If-Modified-Since", row["source_time"].astimezone(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT"))
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                data = r.read(8 * 1024 * 1024 + 1)
                lm = r.headers.get("Last-Modified")
        except urllib.error.HTTPError as e:
            if e.code == 304 and row:
                d.execute("UPDATE goes_images SET fetched_at=now() WHERE sector=%s AND product=%s", (sector, product))
                d.commit()
                return d.execute("SELECT data, content_type, source_time, fetched_at FROM goes_images WHERE sector=%s AND product=%s", (sector, product)).fetchone()
            return row
        except Exception:
            return row
        if len(data) > 8 * 1024 * 1024 or not data.startswith(b"\xff\xd8\xff"):
            return row
        try:
            src = parsedate_to_datetime(lm) if lm else datetime.now(timezone.utc)
        except (TypeError, ValueError):
            src = datetime.now(timezone.utc)
        d.execute("INSERT INTO goes_images (sector, product, data, content_type, source_time, fetched_at) VALUES (%s,%s,%s,'image/jpeg',%s,now()) "
                  "ON CONFLICT (sector, product) DO UPDATE SET data=EXCLUDED.data, source_time=EXCLUDED.source_time, fetched_at=now()", (sector, product, data, src))
        d.commit()
        return d.execute("SELECT data, content_type, source_time, fetched_at FROM goes_images WHERE sector=%s AND product=%s", (sector, product)).fetchone()
    finally:
        lock.release()


def _need(sector, product):
    if not enabled() or sector not in sectors() or product not in products():
        abort(404)


@bp.route("/goes/<sector>/<product>.jpg")
def image(sector, product):
    _need(sector, product)
    row = _fetch(sector, product)
    if row is None:
        abort(503)
    resp = Response(bytes(row["data"]), mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "public, max-age=%d" % (max(1, cfg_int("goes_refresh_minutes")) * 30)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    if row["source_time"]:
        resp.headers["Last-Modified"] = row["source_time"].astimezone(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    return resp


@bp.route("/radio/goes")
def old_address():
    from flask import redirect
    return redirect("/weather/goes" + ("?" + request.query_string.decode() if request.query_string else ""), 301)


@bp.route("/weather/goes")
def page():
    if not enabled():
        abort(404)
    secs, prods = sectors(), products()
    if not secs or not prods:
        abort(404)
    sector = request.args.get("sector", "")
    if sector not in secs:
        sector = cfg("goes_default_sector") if cfg("goes_default_sector") in secs else secs[0]
    product = request.args.get("product", "")
    if product not in prods:
        product = "GEOCOLOR" if "GEOCOLOR" in prods else prods[0]
    mode = request.args.get("mode", "")
    if mode not in ("latest", "loop"):
        mode = "loop" if cfg("goes_default_view") == "loop" else "latest"
    lengths = length_choices(sector)
    try:
        n = int(request.args.get("n", "0"))
    except ValueError:
        n = 0
    if n not in [x[0] for x in lengths]:
        n = loop_frames_wanted()
    meta = {"secs": {k: [SECTORS[k][0], SECTORS[k][1], minutes_apart(k)] for k in secs}, "prods": {k: [PRODUCTS[k][0], PRODUCTS[k][1]] for k in prods},
            "lengths": {k: length_choices(k) for k in secs}, "folders": {k: f"{BASE}/{_path(k, prods[0]).rsplit('/', 2)[0]}/" for k in secs}}
    return render_template("goes.html", sector=sector, product=product, mode=mode, n=n, secs=[(k, SECTORS[k]) for k in secs], prods=[(k, PRODUCTS[k]) for k in prods], lengths=lengths, meta=meta,
                           refresh=max(1, cfg_int("goes_refresh_minutes")), looper_v=looper_version())


# ------------------------------------------------------------------ animation loops
# A loop is the newest few timed pictures of one area and picture. They are read from NOAA's folder listing and fetched only when somebody asks for the loop (a few per request, newest first, so a loop
# fills in over a few seconds), kept in goes_frames, and the oldest are dropped by the frame count and the hours setting.

_listing = {}   # (sector, product) -> (when asked, [(epoch, file name)])
_NAME = re.compile(r'href="(\d{4})(\d{3})(\d{2})(\d{2})_([A-Za-z0-9]+-ABI-[A-Za-z0-9]+-[A-Za-z0-9]+-\d+x\d+\.jpg)"')


def looper_version():
    """A number that changes whenever the player script does, so a browser never keeps an old copy."""
    import os
    try:
        return int(os.path.getmtime(os.path.join(os.path.dirname(__file__), "static", "looper.js")))
    except OSError:
        return 0


def loop_max():
    """The most pictures a loop may have (the longest length a viewer can choose; also how many the site keeps per area and picture)."""
    return max(2, min(96, cfg_int("goes_loop_max")))


def loop_frames_wanted():
    """The default length of a loop."""
    return max(2, min(loop_max(), cfg_int("goes_loop_frames")))


def minutes_apart(sector):
    return 10 if sector.startswith("fd") else 5   # a full disk picture every ten minutes, the others about every five


def span_text(sector, n):
    m = n * minutes_apart(sector)
    return f"about {m} minutes" if m < 90 else f"about {round(m / 60)} hours" if m < 60 * 36 else f"about {round(m / 1440)} days"


def length_choices(sector):
    """[(number of pictures, label)] for the Length control."""
    top = loop_max()
    ns = sorted({n for n in (6, 12, 24, 36, 48, 72, 96, loop_frames_wanted(), top) if 2 <= n <= top})
    return [(n, f"{n} pictures ({span_text(sector, n)})") for n in ns]


def _names(sector, product):
    """The timed pictures NOAA lists for this area and picture (epoch seconds, file name), oldest first; the listing is asked for at most once per refresh time."""
    now = datetime.now(timezone.utc).timestamp()
    hit = _listing.get((sector, product))
    if hit and now - hit[0] < max(1, cfg_int("goes_refresh_minutes")) * 60:
        return hit[1]
    folder, size = _path(sector, product).rsplit("/", 1)
    code = SECTORS[sector][2].split("/")[0]
    suffix = f"{code}-ABI-{FRAME_TOKEN[sector]}-{product}-{size}"
    try:
        req = urllib.request.Request(f"{BASE}/{folder}/", headers={"User-Agent": cfg("lookup_user_agent")})
        with urllib.request.urlopen(req, timeout=25) as r:
            page = r.read(6 * 1024 * 1024).decode("utf-8", "replace")
    except Exception:
        return hit[1] if hit else []
    found = []
    for y, doy, hh, mm, rest in _NAME.findall(page):
        if rest != suffix:
            continue
        try:
            t = datetime(int(y), 1, 1, int(hh), int(mm), tzinfo=timezone.utc) + timedelta(days=int(doy) - 1)
        except ValueError:
            continue
        found.append((int(t.timestamp()), f"{y}{doy}{hh}{mm}_{rest}"))
    found.sort()
    _listing[(sector, product)] = (now, found)
    return found


def _fetch_frame(sector, product, name):
    folder = _path(sector, product).rsplit("/", 1)[0]
    try:
        req = urllib.request.Request(f"{BASE}/{folder}/{name}", headers={"User-Agent": cfg("lookup_user_agent")})
        with urllib.request.urlopen(req, timeout=40) as r:
            data = r.read(8 * 1024 * 1024 + 1)
    except Exception:
        return None
    return data if len(data) <= 8 * 1024 * 1024 and data.startswith(b"\xff\xd8\xff") else None


def _prune(d, sector, product):
    d.execute("DELETE FROM goes_frames WHERE sector=%s AND product=%s AND frame_time NOT IN "
              "(SELECT frame_time FROM goes_frames WHERE sector=%s AND product=%s ORDER BY frame_time DESC LIMIT %s)", (sector, product, sector, product, loop_max()))
    for k in SECTORS:   # old pictures go, but never ones the longest loop still needs: its span (plus an hour) is the least that is kept
        hours = max(1, cfg_int("goes_loop_keep_hours"), -(-loop_max() * minutes_apart(k) // 60) + 1)
        d.execute("DELETE FROM goes_frames WHERE sector=%s AND frame_time < now() - make_interval(hours => %s)", (k, hours))


def fill_loop(sector, product, n=None):
    """Bring a loop's stored frames up to date: fetch the newest missing pictures of the newest n (at most the per-request limit), drop the ones that are too many or too old. Returns (the newest n frame
    times held as epoch seconds, oldest first; whether some of those n listed pictures are still to be fetched, so the player asks again)."""
    d = get_db()
    n = max(1, min(loop_max(), n or loop_frames_wanted()))
    names = _names(sector, product)[-n:]
    have = {int(r["e"]) for r in d.execute("SELECT extract(epoch from frame_time)::bigint AS e FROM goes_frames WHERE sector=%s AND product=%s", (sector, product)).fetchall()}
    missing = [(e, n) for e, n in reversed(names) if e not in have]
    with _guard:
        lock = _locks.setdefault(("loop", sector, product), threading.Lock())
    if missing and lock.acquire(blocking=False):
        try:
            for e, n in missing[:max(1, cfg_int("goes_loop_per_request"))]:
                data = _fetch_frame(sector, product, n)
                if data:
                    d.execute("INSERT INTO goes_frames (sector, product, frame_time, data) VALUES (%s,%s,to_timestamp(%s),%s) ON CONFLICT DO NOTHING", (sector, product, e, data))
                    d.commit()
        finally:
            lock.release()
    _prune(d, sector, product)
    d.commit()
    held = [int(r["e"]) for r in d.execute("SELECT extract(epoch from frame_time)::bigint AS e FROM goes_frames WHERE sector=%s AND product=%s ORDER BY frame_time", (sector, product)).fetchall()]
    newest = {e for e, _ in names}
    return [e for e in held if e >= min(newest)] if newest else held[-n:], bool(newest - set(held))


@bp.route("/goes/<sector>/<product>/frames.json")
def frames_json(sector, product):
    _need(sector, product)
    try:
        n = int(request.args.get("n", "0"))
    except ValueError:
        n = 0
    n = max(1, min(loop_max(), n or loop_frames_wanted()))
    held, more = fill_loop(sector, product, n)
    held = held[-n:]
    resp = jsonify(frames=[{"t": datetime.fromtimestamp(e, timezone.utc).isoformat(), "url": f"/goes/frame/{sector}/{product}/{e}.jpg"} for e in held], more=more,
                   wanted=n, ms=max(100, cfg_int("goes_loop_ms")))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/goes/frame/<sector>/<product>/<int:epoch>.jpg")
def frame(sector, product, epoch):
    _need(sector, product)
    row = get_db().execute("SELECT data FROM goes_frames WHERE sector=%s AND product=%s AND frame_time=to_timestamp(%s)", (sector, product, epoch)).fetchone()
    if row is None:
        abort(404)
    resp = Response(bytes(row["data"]), mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "public, max-age=3600, immutable"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp
