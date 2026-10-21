"""The visitor's real public IP address.

The portal sits behind a reverse proxy (Nginx Proxy Manager, possibly behind Cloudflare). Werkzeug's ProxyFix already trusts one
proxy hop of X-Forwarded-For, but with Cloudflare in front that last hop is Cloudflare's own address. Admin > Settings > Accounts
names the request headers to prefer (for example CF-Connecting-IP, then X-Real-IP); the first one present and valid wins, and
otherwise the proxy-corrected address is used. Only run the portal behind the proxy, never directly on the internet, since these
headers can be set by whoever connects.
"""
import ipaddress

from flask import request

from .settings import cfg


def client_ip():
    for name in [h.strip() for h in cfg("client_ip_headers").split(",") if h.strip()]:
        value = (request.headers.get(name, "") or "").split(",")[0].strip()
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            continue
    return request.remote_addr


def is_private(ip):
    try:
        a = ipaddress.ip_address(ip)
        return a.is_private or a.is_loopback or a.is_link_local
    except ValueError:
        return False
