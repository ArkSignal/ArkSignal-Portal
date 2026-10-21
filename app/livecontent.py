"""Content that updates itself: the Release Notes and Roadmap pages read their data from the GitHub repository's main branch (so they change as soon as
work is pushed, without a release), and fall back to the copy built into the running image when GitHub cannot be reached. Settings (Admin > Updates >
Live Content): repository, branch, an optional read-only token (sealed), refresh minutes, and an optional custom address (a mirror, or a test server).
Nothing here ever raises into a page: any failure just means the built-in copy is shown and the reason is kept for the admin page."""
import json
import threading
import time
import urllib.error
import urllib.request

from .settings import get_secret, get_setting

DEFAULT_REPO = "mallison01/adsb-portal-content"   # public: only the two public-safe files, published automatically by .github/workflows/publish-content.yml
MAX_BYTES = 1_000_000
_lock = threading.Lock()
_cache = {}      # name -> {"data", "etag", "at" (last good), "tried" (last attempt), "error"}


def conf():
    def _int(v, d):
        try:
            return max(1, min(1440, int(v)))
        except (TypeError, ValueError):
            return d
    return {"enabled": (get_setting("content_enabled") or "1") == "1", "repo": get_setting("content_repo") or DEFAULT_REPO, "branch": get_setting("content_branch") or "main",
            "minutes": _int(get_setting("content_minutes"), 10), "base": (get_setting("content_base_url") or "").strip().rstrip("/"), "has_token": bool(get_setting("content_token"))}


def _url(c, name):
    if c["base"]:
        return f'{c["base"]}/{name}'
    if c["has_token"]:   # a private repository (the project's own, files under app/): through the API with the token
        return f'https://api.github.com/repos/{c["repo"]}/contents/app/{name}?ref={c["branch"]}'
    return f'https://raw.githubusercontent.com/{c["repo"]}/{c["branch"]}/{name}'


def _fetch(name, c, etag=None):
    headers = {"User-Agent": "adsb-portal-content", "Accept": "application/vnd.github.raw+json"}
    token = get_secret("content_token")
    if token and not c["base"]:
        headers["Authorization"] = "Bearer " + token
    if etag:
        headers["If-None-Match"] = etag
    req = urllib.request.Request(_url(c, name), headers=headers)
    with urllib.request.urlopen(req, timeout=6) as r:
        body = r.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("the file is larger than expected")
        return json.loads(body.decode("utf-8")), r.headers.get("ETag")


def get(name, builtin, validate):
    """(data, meta). `builtin` is a function returning the copy shipped in the image; `validate` returns clean data or None for a bad document.
    meta: {"source": "github" or "built-in", "at": epoch seconds of the last good fetch or None, "error": text or ""}."""
    c = conf()
    if not c["enabled"]:
        return builtin(), {"source": "built-in", "at": None, "error": ""}
    now = time.time()
    with _lock:
        # the web server runs several worker processes, each with its own cache: a settings change made through one must reach the others, so every cache
        # entry remembers which settings it was fetched with and starts over when they differ
        sig = (c["repo"], c["branch"], c["base"], c["minutes"], get_setting("content_token") or "")
        e = _cache.get(name)
        if e is None or e.get("sig") != sig:
            e = _cache[name] = {"data": None, "etag": None, "at": None, "tried": 0.0, "error": "", "sig": sig}
        stale = now - (e["at"] or 0) > c["minutes"] * 60
        retry_ok = now - e["tried"] > 60            # after a failure, no more than one attempt a minute
        if stale and retry_ok:
            e["tried"] = now
            try:
                data, etag = _fetch(name, c, e["etag"])
                clean = validate(data)
                if clean is None:
                    raise ValueError("the document on GitHub is not in the expected format")
                e.update(data=clean, etag=etag, at=now, error="")
            except urllib.error.HTTPError as ex:
                if ex.code == 304 and e["data"] is not None:
                    e.update(at=now, error="")
                else:
                    e["error"] = f"GitHub answered {ex.code}" + (" (check the repository name and the token)" if ex.code in (401, 403, 404) else "")
            except Exception as ex:   # no network, bad JSON, timeout: keep what we have
                e["error"] = f"{type(ex).__name__}: {str(ex)[:120]}"
        if e["data"] is not None:
            return e["data"], {"source": "github", "at": e["at"], "error": e["error"]}
        return builtin(), {"source": "built-in", "at": None, "error": e["error"]}


def iso(epoch):
    """An epoch time as the ISO text the localtime filter understands, or None."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat() if epoch else None


def forget():
    """Drop cached documents so the next page view fetches again (after settings change or the Test button)."""
    with _lock:
        _cache.clear()


def status():
    with _lock:
        return {k: {"at": v["at"], "error": v["error"], "tried": v["tried"]} for k, v in _cache.items()}
