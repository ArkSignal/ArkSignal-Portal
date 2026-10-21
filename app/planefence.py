"""Planefence and Plane-Alert inside the portal.

The `planefence` container (see deploy/planefence) serves its public pages on port 80 and its configuration page on port 9999 inside the docker network. The
portal shows the public pages under /pf/ and, for administrators only, the configuration page under /admin/planefence/, so nothing has to be opened in Nginx
Proxy Manager and nothing is edited by hand. Only a few absolute paths in their scripts (/cgi/...) need pointing at the matching prefix.
"""
import gzip
import os
import re
import threading
import time
import urllib.error
import urllib.request
import zlib

from flask import Blueprint, Response, abort, render_template, request, stream_with_context

from . import csrf
from .admin import permission_required
from .settings import cfg_int

bp = Blueprint("planefence", __name__)

MAX_BYTES = 40_000_000
PASS_HEADERS = ("Content-Type", "Cache-Control", "Last-Modified", "ETag")


def _base(kind):
    if kind == "config":
        return (os.environ.get("PLANEFENCE_CONFIG_URL") or "http://planefence:9999").rstrip("/")
    return (os.environ.get("PLANEFENCE_URL") or "http://planefence").rstrip("/")


def _clean(path):
    """A path under the upstream root, or None when it tries to leave it."""
    if "\\" in path or "\x00" in path or any(seg in ("..", ".") for seg in path.split("/")) or path.startswith("/"):
        return None
    return path


def _down():
    return Response("<!doctype html><meta charset=utf-8><body style='font:16px sans-serif;padding:24px'><h3>Planefence is not running yet</h3>"
                    "<p>It starts by itself after the next update (Admin &gt; Updates &gt; Update Now). Nothing needs to be done by hand.</p></body>", status=503, mimetype="text/html")


def _rewrite(body, ctype, prefix):
    if not re.match(r"(text/html|text/javascript|application/javascript)", ctype or ""):
        return body
    text = body.decode("utf-8", "replace")
    text = re.sub(r"""(["'`])/(cgi/|planefence-logs|js/|css/|images/)""", lambda m: m.group(1) + prefix + m.group(2), text)
    return text.encode("utf-8")


def _gz_ok():
    return "gzip" in (request.headers.get("Accept-Encoding") or "")


def _stream(url):
    """Planefence's data comes as a stream of JSON lines (cgi/stream.sh) that its page draws as they arrive. Passing the stream straight through, compressed, keeps that:
    the first rows show at once and the page is usable while the rest follows, instead of the whole day being collected first (which is what made it take half a minute)."""
    req = urllib.request.Request(url, headers={"User-Agent": "adsb-portal", "Accept": "*/*"})
    try:
        r = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as e:
        return Response(e.read(MAX_BYTES), status=e.code, content_type=e.headers.get("Content-Type", "text/plain"))
    except (urllib.error.URLError, OSError):
        return _down()
    gz = _gz_ok()

    def parts():
        co = zlib.compressobj(6, zlib.DEFLATED, 31) if gz else None
        try:
            while True:
                chunk = r.read1(16384) if hasattr(r, "read1") else r.read(16384)
                if not chunk:
                    break
                yield (co.compress(chunk) + co.flush(zlib.Z_SYNC_FLUSH)) if co else chunk   # a flush after every piece, so nothing waits in the compressor
            if co:
                yield co.flush()
        finally:
            r.close()
    out = Response(stream_with_context(parts()), status=r.status, content_type=r.headers.get("Content-Type", "application/x-ndjson"))
    out.headers["Cache-Control"] = "no-store"
    out.headers["X-Accel-Buffering"] = "no"   # a proxy in front (Nginx Proxy Manager) must not hold the stream back either
    if gz:
        out.headers["Content-Encoding"] = "gzip"
        out.headers["Vary"] = "Accept-Encoding"
    return out


_cache = {}
_cache_lock = threading.Lock()


def _cached_page(url, prefix):
    """Pages, scripts, styles and pictures of the public site: fetched from Planefence at most once per `pf_cache_seconds` (Settings), rewritten once, kept compressed.
    Returns (status, content type, body, gzipped body) or None when Planefence cannot be reached."""
    ttl = cfg_int("pf_cache_seconds")
    now = time.time()
    with _cache_lock:
        hit = _cache.get(url)
        if hit and ttl > 0 and now - hit[0] < ttl:
            return hit[1]
    req = urllib.request.Request(url, headers={"User-Agent": "adsb-portal", "Accept": request.headers.get("Accept", "*/*")})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            body, status, ctype = r.read(MAX_BYTES), r.status, r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        body, status, ctype = e.read(MAX_BYTES), e.code, e.headers.get("Content-Type", "")
    except (urllib.error.URLError, OSError):
        return None
    body = _rewrite(body, ctype, prefix)
    item = (status, ctype, body, gzip.compress(body, 6) if len(body) > 1400 else None)
    if status == 200 and ttl > 0 and len(body) < 8_000_000:
        with _cache_lock:
            if len(_cache) > 300:
                _cache.clear()
            _cache[url] = (now, item)
    return item


def _proxy(kind, path, prefix):
    p = _clean(path)
    if p is None:
        abort(404)
    url = _base(kind) + "/" + p + (("?" + request.query_string.decode("latin-1")) if request.query_string else "")
    if kind == "site" and request.method in ("GET", "HEAD"):
        if p.startswith("cgi/"):
            return _stream(url)
        item = _cached_page(url, prefix)
        if item is None:
            return _down()
        status, ctype, body, zbody = item
        use_gz = zbody is not None and _gz_ok()
        out = Response(zbody if use_gz else body, status=status, content_type=ctype or "application/octet-stream")
        out.headers["Vary"] = "Accept-Encoding"
        if use_gz:
            out.headers["Content-Encoding"] = "gzip"
        return out
    data = request.get_data() if request.method in ("POST", "PUT") else None
    headers = {"User-Agent": "adsb-portal", "Accept": request.headers.get("Accept", "*/*")}
    if request.content_type:
        headers["Content-Type"] = request.content_type
    req = urllib.request.Request(url, data=data, method=request.method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            body, status, hdrs = r.read(MAX_BYTES), r.status, r.headers
    except urllib.error.HTTPError as e:
        body, status, hdrs = e.read(MAX_BYTES), e.code, e.headers
    except (urllib.error.URLError, OSError):
        return _down()
    ctype = hdrs.get("Content-Type", "")
    out = Response(_rewrite(body, ctype, prefix), status=status)
    for h in PASS_HEADERS:
        if hdrs.get(h):
            out.headers[h] = hdrs[h]
    out.headers.pop("Content-Length", None)
    return out


# ---- public pages: /pf/

@bp.route("/pf/", defaults={"path": ""}, methods=["GET", "HEAD"])
@bp.route("/pf/<path:path>", methods=["GET", "HEAD", "POST"])
def public(path):
    if request.method == "POST" and not path.startswith("cgi/"):
        abort(405)
    return _proxy("site", path, "/pf/")


# ---- administrators only: Planefence's own configuration page

@bp.route("/admin/planefence/")
@permission_required("manage_settings")
def admin_page():
    return render_template("admin_planefence.html", active="planefence")


def _same_origin():
    """CSRF defence for the proxied configuration page, whose own scripts cannot carry our form token: the browser must say the request comes from this site."""
    site = request.headers.get("Sec-Fetch-Site")
    if site:
        return site == "same-origin"
    origin = request.headers.get("Origin")
    return bool(origin) and origin.rstrip("/").endswith("//" + request.host)


@bp.route("/admin/planefence/ui/", defaults={"path": ""}, methods=["GET", "HEAD"])
@bp.route("/admin/planefence/ui/<path:path>", methods=["GET", "HEAD", "POST"])
@csrf.exempt
@permission_required("manage_settings")
def admin_ui(path):
    if request.method == "POST" and not _same_origin():
        abort(403)
    if request.method == "POST" and not path.startswith("cgi/"):
        abort(405)
    return _proxy("config", path, "/admin/planefence/ui/")
