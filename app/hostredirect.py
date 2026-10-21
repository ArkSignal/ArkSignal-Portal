"""Moving the public site to a new host name: page requests that arrive on an old address are sent to the main address with a permanent redirect.
Off until Admin > Settings > General > Main Address is filled in. Only GET and HEAD are redirected, /healthz is left alone (the proxy and the container health check
call it), and the feed and MLAT host names are separate TCP services that never reach the portal."""
from flask import redirect, request

from .settings import cfg


def hosts(text):
    return {h.strip().lower() for h in (text or "").replace(";", ",").split(",") if h.strip()}


def target(host, path, query, method):
    """The address to send this request to, or None when it stays where it is."""
    main = (cfg("main_address") or "").strip().lower()
    if not main or method not in ("GET", "HEAD") or path == "/healthz":
        return None
    host = (host or "").split(":")[0].lower()
    if host == main or host not in hosts(cfg("redirect_addresses")):
        return None
    return "https://" + main + path + ("?" + query if query else "")


def enforce():
    to = target(request.host, request.path, request.query_string.decode("utf-8", "replace"), request.method)
    return redirect(to, code=301) if to else None
