"""Release notes: what was added, improved, fixed and secured, and when. The data is `release_notes.json` next to this file, newest first; the entry
named "next" collects what is committed but not released yet and is renamed to its version number when a release is built (release.sh refuses to
release a version that has no entry). The running version's date falls back to BUILD_INFO's build time when the entry has none."""
import json
import os
import re

from flask import Blueprint, jsonify, render_template

from . import build_info, livecontent

bp = Blueprint("releasenotes", __name__)
SECTIONS = (("added", "Added"), ("improved", "Improved"), ("fixed", "Fixed"), ("security", "Security"))
_cache = {"mtime": None, "data": []}


def _builtin():
    path = os.path.join(os.path.dirname(__file__), "release_notes.json")
    try:
        m = os.path.getmtime(path)
        if _cache["mtime"] != m:
            with open(path, encoding="utf-8") as f:
                _cache.update(mtime=m, data=json.load(f))
    except (OSError, ValueError):
        return []
    return _cache["data"]


def _validate(doc):
    """A list of release entries with only the fields we show, each text trimmed; None when the document is not in the expected shape."""
    if not isinstance(doc, list) or not doc or len(doc) > 400:
        return None
    out = []
    for e in doc:
        if not isinstance(e, dict) or not isinstance(e.get("version"), str):
            return None
        c = {"version": e["version"][:20], "released": str(e["released"])[:30] if e.get("released") else None, "title": str(e.get("title", ""))[:160]}
        for k in ("added", "improved", "fixed", "security"):
            v = e.get(k) or []
            if not isinstance(v, list):
                return None
            c[k] = [str(x)[:900] for x in v[:60]]
        out.append(c)
    return out


def load():
    return livecontent.get("release_notes.json", _builtin, _validate)


def _utc(text):
    return text + "+00:00" if text and not re.search(r"(Z|[+-]\d\d:\d\d)$", text) else text


def entries():
    running = build_info.read()
    out = []
    data, _meta = load()
    for e in data:
        e = dict(e)
        e["released"] = _utc(e.get("released")) if e.get("released") else None
        e["running"] = e["version"] == running.get("version")
        if e["running"] and not e["released"] and running.get("built", "unknown")[:2] == "20":
            e["released"] = running["built"]
        e["sections"] = [(label, e.get(key) or []) for key, label in SECTIONS if e.get(key)]
        out.append(e)
    return out


@bp.route("/release-notes")
def page():
    _data, meta = load()
    return render_template("release_notes.html", entries=entries(), running=build_info.read()["version"], meta=meta, fetched=livecontent.iso(meta["at"]))


@bp.route("/api/release-notes.json")
def api():
    _data, meta = load()
    return jsonify({"running": build_info.read()["version"], "source": meta["source"],
                    "releases": [{k: e[k] for k in ("version", "released", "title", "added", "improved", "fixed", "security")} for e in entries()]})
