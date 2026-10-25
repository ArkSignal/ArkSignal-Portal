"""Shopping links for identified hardware (the mesh hardware shown on node pages and lists first).

A link is a search at the shop for the hardware's name (never a made-up product page). When an administrator has an Amazon Associates tag it is added to every link and the links are marked
rel="sponsored nofollow noopener", and the sentence Amazon requires near such links is shown (Settings > Shopping Links). An administrator can also give the exact product address for a hardware name
(Exact Product Links: `Name = https://...; Name = https://...`), which wins over the search. The site never tracks clicks; the link goes straight to the shop."""
import re
import urllib.parse

from .settings import cfg

DOMAIN_RE = re.compile(r"^(?:www\.)?amazon\.(?:com|ca|co\.uk|de|fr|it|es|com\.au|co\.jp|in|com\.mx|com\.br|nl|se|pl|sg|ae|sa|com\.tr)$")


def on():
    return cfg("shop_enabled") == "1"


def tag():
    t = (cfg("shop_amazon_tag") or "").strip()
    return t if re.fullmatch(r"[A-Za-z0-9_-]{3,40}", t) else ""


def overrides():
    """{lowercase hardware name: address} from the Exact Product Links setting; only https addresses are used."""
    out = {}
    for part in (cfg("shop_overrides") or "").split(";"):
        if "=" in part:
            name, url = part.split("=", 1)
            url = url.strip()
            if name.strip() and url.startswith("https://") and " " not in url:
                out[name.strip().lower()] = url
    return out


def _with_tag(url):
    t = tag()
    if not t:
        return url
    u = urllib.parse.urlsplit(url)
    host = (u.hostname or "").lower()
    if not DOMAIN_RE.match(host):
        return url   # another shop: the Amazon tag is not added to it
    q = [(k, v) for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True) if k != "tag"] + [("tag", t)]
    return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, urllib.parse.urlencode(q), u.fragment))


def link(name, extra="Meshtastic"):
    """{url, sponsored} for a hardware name, or None (switched off, no name, or a name the site could not recognize)."""
    name = (name or "").strip()
    if not on() or not name or name.lower().startswith("unlisted hardware") or name in ("-", "unknown", "other"):
        return None
    exact = overrides().get(name.lower())
    if exact:
        url = _with_tag(exact)
    else:
        domain = (cfg("shop_amazon_domain") or "www.amazon.com").strip().lower()
        if not DOMAIN_RE.match(domain):
            domain = "www.amazon.com"
        q = [("k", (name + " " + extra).strip())] + ([("tag", tag())] if tag() else [])
        url = "https://" + domain + "/s?" + urllib.parse.urlencode(q)
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return {"url": url, "sponsored": bool(tag()) and bool(DOMAIN_RE.match(host))}


def disclosure():
    """The sentence shown near links when an Associates tag is set (empty otherwise)."""
    return (cfg("shop_disclosure") or "").strip() if tag() and on() else ""
