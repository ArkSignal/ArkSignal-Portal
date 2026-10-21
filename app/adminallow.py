"""Admin area allow-list (Settings > Security > Admin Area: Allowed Addresses): when the list is not empty, the pages under /admin answer "not found" to every visitor whose address is not on it,
signed in or not. The list holds addresses and networks (192.168.1.0/24, 203.0.113.7, 2001:db8::/32) and the word `private` for every home or office network address. It can only be saved from an
address that is itself on the new list, so it cannot lock out the person who sets it; the server command `python -m app.cli clear-admin-allow` empties it if a router change ever does."""
import ipaddress

from flask import abort, request

from .settings import cfg


def parse(text):
    """([networks], [problems]) from a comma or line separated list; the word `private` stands for the private ranges."""
    nets, bad = [], []
    for tok in [t.strip() for t in (text or "").replace("\n", ",").replace(";", ",").split(",") if t.strip()]:
        if tok.lower() == "private":
            nets += [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "fc00::/7", "::1/128")]
            continue
        try:
            nets.append(ipaddress.ip_network(tok, strict=False))
        except ValueError:
            bad.append(tok)
    return nets, bad


def allowed(ip, text=None):
    """True when the list is empty or `ip` is on it."""
    text = cfg("admin_allow_addresses") if text is None else text
    if not (text or "").strip():
        return True
    nets, _bad = parse(text)
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a in n for n in nets if n.version == a.version)


def check(text):
    """'' when the list can be saved by this visitor, else the sentence to show."""
    if not (text or "").strip():
        return ""
    nets, bad = parse(text)
    if bad:
        return "These are not addresses or networks: " + ", ".join(bad[:5]) + ". Use forms like 203.0.113.7, 192.168.1.0/24 or the word private."
    if not nets:
        return "The list names no address."
    from .clientip import client_ip
    me = client_ip()
    if not allowed(me, text):
        return f"Your own address is {me}, and it is not on this list: saving it would lock you out of the Admin area. Add {me} (or the network it belongs to)."
    return ""


def enforce():
    """before_request: /admin pages are not found from an address that is not on the list."""
    if not request.path.startswith("/admin"):
        return None
    if not (cfg("admin_allow_addresses") or "").strip():
        return None
    from .clientip import client_ip
    if not allowed(client_ip()):
        abort(404)
    return None
