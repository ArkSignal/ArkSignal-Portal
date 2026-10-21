"""Alert delivery to a web address the person chooses (Discord, Slack, ntfy or any service that accepts a JSON post).

Email is not set up yet, and a webhook needs no mail server: the person pastes the address their chat or notification service gave them.
Because the portal makes the request on their behalf, the address is checked so it cannot be used to poke at the portal's own network:
https only, no addresses on private, loopback or link-local networks (checked again at every send, after DNS), and no redirects.
"""
import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from .settings import cfg, cfg_int

KINDS = {"json": "Generic JSON", "discord": "Discord", "slack": "Slack", "ntfy": "ntfy (plain text)"}


def check_url(url):
    """(clean url, error). Syntax and, unless Settings allow private addresses, where it points."""
    url = (url or "").strip()
    if not url:
        return "", None
    u = urllib.parse.urlparse(url)
    private_ok = cfg("webhook_allow_private") == "1"
    if len(url) > 400 or " " in url or not u.netloc or not u.hostname:
        return None, "Enter a full web address, for example https://discord.com/api/webhooks/..."
    if u.scheme != "https" and not (private_ok and u.scheme == "http"):
        return None, "The address must start with https://"
    if u.username or u.password:
        return None, "Leave out any user name and password in the address; services give you a secret path or token instead."
    if not private_ok:
        err = _reject_private(u.hostname, u.port or 443)
        if err:
            return None, err
    return url, None


def _reject_private(host, port):
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return "That address could not be found. Check it for typing mistakes."
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return "That address points inside a private network, which the portal will not contact."
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def build(kind, text, matches, site):
    """(body bytes, headers) for one delivery."""
    if kind == "discord":
        return json.dumps({"content": text[:1900], "username": site[:80]}).encode(), {"Content-Type": "application/json"}
    if kind == "slack":
        return json.dumps({"text": text[:3000]}).encode(), {"Content-Type": "application/json"}
    if kind == "ntfy":
        return text[:3500].encode("utf-8"), {"Content-Type": "text/plain; charset=utf-8", "Title": site[:120].encode("ascii", "replace").decode(), "Tags": "airplane"}
    return json.dumps({"site": site, "count": len(matches), "matches": matches, "text": text}).encode(), {"Content-Type": "application/json"}


def send(url, kind, text, matches, site):
    """(ok, message). Never raises."""
    clean, err = check_url(url)
    if err or not clean:
        return False, err or "No webhook address is set."
    body, headers = build(kind, text, matches, site)
    headers["User-Agent"] = cfg("lookup_user_agent")
    req = urllib.request.Request(clean, data=body, headers=headers, method="POST")
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=cfg_int("webhook_timeout_seconds")) as r:
            return (200 <= r.status < 300), f"The service answered {r.status}."
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400:
            return False, "The address redirects somewhere else, which the portal will not follow. Use the final address."
        return False, f"The service refused it (error {e.code}). Check the address is still valid."
    except Exception as e:
        return False, f"Could not reach the address ({type(e).__name__})."
