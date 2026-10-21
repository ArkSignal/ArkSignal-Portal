"""Is a newer stable release published than the one running? Same design as the photo pipeline's update check.

A container cannot learn its own image digest without Docker socket access, which this app deliberately does not have, so the
running version is BUILD_INFO's `version:` line and "the latest stable release" is the vX.Y.Z tag that shares `stable`'s digest on
Docker Hub's public API. The network call happens only from the background thread (and the About page's Check Now button, which a
person asked for); every page just reads the saved result from the database. Nothing here ever raises: no internet, a Docker Hub
outage or a malformed answer simply means "could not check".
"""
import json
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from . import build_info
from .settings import cfg, cfg_int, get_setting, set_setting

STATE_KEY = "update_check_state"
_VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
MAX_PAGES = 5  # sane cap on pagination; this repository has a few dozen tags


def _version_tuple(version):
    parts = (version or "").split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def _fetch_tags(repo):
    """All tags of the Docker Hub repository, or None on any failure."""
    tags, url = [], f"https://hub.docker.com/v2/repositories/{repo}/tags?page_size=100"
    try:
        for _ in range(MAX_PAGES):
            if not url:
                break
            with urllib.request.urlopen(url, timeout=cfg_int("lookup_timeout_seconds") + 4) as r:
                page = json.loads(r.read().decode("utf-8"))
            tags.extend(page.get("results", []))
            url = page.get("next")
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    return tags


def latest_stable_version(tags):
    """The vX.Y.Z tag (the highest, if there are several) that shares `stable`'s digest right now."""
    by_name = {t["name"]: t for t in tags if t.get("images")}
    stable = by_name.get("stable")
    digest = stable["images"][0].get("digest") if stable else None
    if not digest:
        return None
    found = [t["name"][1:] for t in tags if _VERSION_RE.match(t["name"]) and t.get("images") and t["images"][0].get("digest") == digest]
    return max(found, key=_version_tuple) if found else None


def check_now():
    """The real Docker Hub call. Returns a JSON-able dict; never raises."""
    when = datetime.now(timezone.utc).isoformat(timespec="seconds")
    own = build_info.read()["version"]
    base = {"checked_at": when, "current_version": own, "latest_stable_version": None, "update_available": None, "error": None, "note": None}
    repo = cfg("update_check_repo").strip()
    if not repo:
        return dict(base, note="Update checking is turned off.")
    if own == "unreleased":
        return dict(base, note="This build was not made as a numbered release, so there is nothing to compare against.")
    tags = _fetch_tags(repo)
    if tags is None:
        return dict(base, error="Could not reach Docker Hub to check for an update.")
    latest = latest_stable_version(tags)
    a, b = _version_tuple(own), _version_tuple(latest)
    if not latest or a is None or b is None:
        return dict(base, latest_stable_version=latest, error="Could not work out the currently published stable version.")
    return dict(base, latest_stable_version=latest, update_available=b > a)


def read_state():
    try:
        data = json.loads(get_setting(STATE_KEY) or "{}")
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def maybe_check(force=False):
    """A cheap no-op most of the time; does the real check when none is saved, the saved one is stale, or `force` is set."""
    state = read_state()
    hours = cfg_int("update_check_hours")
    if not force:
        if hours <= 0:
            return state
        try:
            if state.get("checked_at") and datetime.now(timezone.utc) - datetime.fromisoformat(state["checked_at"]) < timedelta(hours=hours):
                return state
        except ValueError:
            pass
    try:
        fresh = check_now()
        set_setting(STATE_KEY, json.dumps(fresh))
        return fresh
    except Exception:
        return state


def start_update_checks(app):
    def loop():
        time.sleep(180)
        while True:
            try:
                with app.app_context():
                    maybe_check()
            except Exception:
                pass
            time.sleep(3600)
    threading.Thread(target=loop, name="update-check", daemon=True).start()
