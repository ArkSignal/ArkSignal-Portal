"""Checking a new site address before it is saved (Settings > General > Main Address, and the feed address).

Moving the site to a new host name is the one setting that can lock everybody out: every page request on an old address is redirected to the Main Address, so a name that does not answer, or that answers
from some other server, breaks the whole site. So the Main Address is saved only when the new name resolves in DNS, answers over https with a valid certificate, and answers as THIS site: the site publishes
a random identifier of its own at /.well-known/arksignal.json and the check reads it from the new name. The feed address (a TCP service the portal never serves) is checked for DNS only."""
import json
import secrets
import socket
import ssl
import urllib.error
import urllib.request

from flask import Blueprint, jsonify

from .db import get_db
from .settings import cfg

bp = Blueprint("siteaddress", __name__)


def instance_id():
    """This site's own random identifier (made once, kept in the settings table)."""
    d = get_db()
    r = d.execute("SELECT value FROM settings WHERE key='instance_id'").fetchone()
    if r and r["value"]:
        return r["value"]
    v = secrets.token_hex(8)
    d.execute("INSERT INTO settings (key, value) VALUES ('instance_id', %s) ON CONFLICT (key) DO NOTHING", (v,))
    d.commit()
    return d.execute("SELECT value FROM settings WHERE key='instance_id'").fetchone()["value"]


@bp.route("/.well-known/arksignal.json")
def well_known():
    resp = jsonify({"id": instance_id(), "site": cfg("site_name")})
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _resolves(host):
    try:
        return bool(socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP))
    except OSError:
        return False


def check(host, serves_pages=True, timeout=None):
    """None when the host name is fit to use, else a plain sentence saying what is wrong."""
    host = (host or "").strip().lower()
    if not host:
        return None
    if not _resolves(host):
        return f"{host} does not resolve in DNS yet. Add its DNS record, wait a few minutes and save again."
    if not serves_pages:
        return None
    seconds = timeout or max(3, cfg_int_safe("address_check_seconds", 8))
    try:
        with urllib.request.urlopen(urllib.request.Request(f"https://{host}/.well-known/arksignal.json", headers={"User-Agent": cfg("lookup_user_agent")}), timeout=seconds) as r:
            data = json.loads(r.read(4096).decode("utf-8", "replace"))
    except ssl.SSLError as e:
        return f"{host} answers, but its https certificate is not valid ({str(e)[:80]}). Add a certificate for it in the proxy and save again."
    except urllib.error.HTTPError:
        return f"{host} answers over https, but not as this site (the address /.well-known/arksignal.json was not found). Check where its proxy host forwards to."
    except urllib.error.URLError as e:
        if isinstance(e.reason, ssl.SSLError):
            return f"{host} answers, but its https certificate is not valid ({str(e.reason)[:80]}). Add a certificate for it in the proxy and save again."
        return f"{host} does not answer over https ({str(e.reason)[:80]}). Check the proxy host for it and that port 443 reaches the server."
    except Exception as e:
        return f"{host} could not be checked ({str(e)[:80]})."
    if not isinstance(data, dict) or data.get("id") != instance_id():
        return f"{host} answers over https, but from a different server, not this site. Check where its proxy host forwards to."
    return None


def cfg_int_safe(key, default):
    try:
        return int(cfg(key))
    except (TypeError, ValueError):
        return default
