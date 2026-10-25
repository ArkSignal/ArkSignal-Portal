"""Being found on Google and other search engines (free, no account on our side): `/robots.txt`, `/sitemap.xml`, a description, a canonical address and social-card tags on every public page, and a "noindex" on the pages that
are private (My Account, Admin, sign-in, chat, member pages, the APIs).

What is public is what a visitor who is not signed in can see: the sitemap lists the menu's own pages as an anonymous visitor gets them (so a feature that is off or limited to members by Feature Access is not listed) and a few fixed pages.
Settings > Public Pages: Search Engines on or off (off tells every crawler to stay away and marks pages noindex), the description shown under the site's name in search results, and the verification codes that Google Search Console
and Bing Webmaster Tools hand out (a code is put in a meta tag on the home page; the site's address can also be verified in DNS, which needs no code here). Telling Google the site exists is done in Search Console: add the address,
verify it, and submit `/sitemap.xml`."""
import re
from xml.sax.saxutils import escape

from flask import Blueprint, Response, request

from .settings import cfg

bp = Blueprint("seo", __name__)

# Never indexed: private, per-person or machine pages
NOINDEX = ("/admin", "/my", "/share", "/chat", "/login", "/logout", "/members", "/api", "/internal", "/bridge", "/healthz")
FIXED = ("/", "/about", "/release-notes", "/roadmap", "/contact", "/status")
CODE_RE = re.compile(r"^[A-Za-z0-9_\-]{8,120}$")


def enabled():
    return cfg("seo_enabled") == "1"


def noindex_path(path):
    return any(path == p or path.startswith(p + "/") for p in NOINDEX)


def base_url():
    """The address search engines should use: the Main Address setting (https), else the address this request came to."""
    main = (cfg("main_address") or "").strip().lower()
    return ("https://" + main) if main else request.url_root.rstrip("/")


def page_info():
    """What the page head needs: {on, description, canonical, noindex, google, bing, title_default}."""
    path = request.path
    return {"on": enabled(), "description": cfg("seo_description"), "canonical": base_url() + (path if path != "/" else "/"), "noindex": (not enabled()) or noindex_path(path) or request.method != "GET",
            "google": cfg("seo_google_verification") if path == "/" else "", "bing": cfg("seo_bing_verification") if path == "/" else ""}


@bp.route("/robots.txt")
def robots():
    if not enabled():
        body = "User-agent: *\nDisallow: /\n"
    else:
        lines = ["User-agent: *"] + [f"Disallow: {p}/" if p != "/healthz" else f"Disallow: {p}" for p in NOINDEX] + ["", f"Sitemap: {base_url()}/sitemap.xml"]
        body = "\n".join(lines) + "\n"
    r = Response(body, mimetype="text/plain")
    r.headers["Cache-Control"] = "public, max-age=3600"
    return r


def urls():
    """The public pages, in menu order, without duplicates."""
    from . import navmenu, tools
    out, seen = [], set()

    def add(href):
        if not href or not href.startswith("/") or any(c in href for c in "?#{}") or noindex_path(href.rstrip("/") or "/"):
            return
        if href not in seen:
            seen.add(href)
            out.append(href)
    for h in FIXED[:1]:
        add(h)
    for g in navmenu.groups(tools.list_enabled()):
        add(g.get("home"))
        for _label, href in g["items"]:
            add(href)
    for h in FIXED[1:]:
        add(h)
    return out


@bp.route("/sitemap.xml")
def sitemap():
    if not enabled():
        return Response("", status=404)
    base = base_url()
    body = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "".join(f"  <url><loc>{escape(base + h)}</loc></url>\n" for h in urls()) + "</urlset>\n"
    r = Response(body, mimetype="application/xml")
    r.headers["Cache-Control"] = "public, max-age=3600"
    return r


@bp.after_app_request
def robots_header(resp):
    """Private pages also say noindex in a header (this covers pages that are not HTML, and redirects)."""
    try:
        if (not enabled() or noindex_path(request.path)) and "X-Robots-Tag" not in resp.headers:
            resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    except Exception:
        pass
    return resp
