"""Admin pages for restoring part of a backup (see restorepart.py for what is restored and how)."""
import os

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for

from . import backup as bk
from .admin import permission_required
from .audit import audit
from .db import get_db
from .restorepart import RECORD_LABELS, RECORD_TABLES, list_records, preview, restore_areas, restore_records

bp = Blueprint("restorepart", __name__)


def _path(name):
    if not bk.valid_name(name) or not os.path.exists(bk.backup_path(name)):
        abort(404)
    return bk.backup_path(name)


def _summary(written):
    return f"{sum(written.values()):,} rows written across {len(written)} tables (" + ", ".join(f"{t} {c:,}" for t, c in written.items() if c) + ")"


def _safety(url):
    get_db().rollback()   # release this request's own locks so the restore is not blocked by them
    return bk.create_backup(url, "pre-restore")[0]


@bp.route("/admin/backups/<name>/parts")
@permission_required("manage_settings")
def parts(name):
    path = _path(name)
    try:
        pv = preview(path, current_app.config["DATABASE_URL"])
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("admin.backups"))
    return render_template("admin_restore_parts.html", active="backups", name=name, pv=pv)


@bp.route("/admin/backups/<name>/parts/restore", methods=["POST"])
@permission_required("manage_settings")
def parts_restore(name):
    path = _path(name)
    url = current_app.config["DATABASE_URL"]
    areas, mode = request.form.getlist("area"), request.form.get("mode", "merge")
    if not areas:
        flash("Tick at least one area to restore.", "error")
        return redirect(url_for("restorepart.parts", name=name))
    try:
        safety = _safety(url)
    except Exception as e:
        flash(f"The safety backup failed, so nothing was restored: {e}", "error")
        return redirect(url_for("restorepart.parts", name=name))
    try:
        written = restore_areas(path, url, areas, mode)
    except ValueError as e:
        audit("backup.restore_part_failed", target=name, detail=str(e)[:300])
        flash(f"Nothing was changed: {e}", "error")
        return redirect(url_for("restorepart.parts", name=name))
    bk.apply_retention()
    audit("backup.restore_part", target=name, detail=f"{mode}: {', '.join(areas)}; safety backup {safety}")
    flash(f"Restored ({mode}) from {name}: {_summary(written)}. The previous data was saved first as {safety}.", "success")
    return redirect(url_for("admin.backups"))


@bp.route("/admin/backups/<name>/records/<table>.json")
@permission_required("manage_settings")
def records(name, table):
    path = _path(name)
    try:
        return jsonify({"records": list_records(path, current_app.config["DATABASE_URL"], table)})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/admin/backups/<name>/records/restore", methods=["POST"])
@permission_required("manage_settings")
def records_restore(name):
    path = _path(name)
    url = current_app.config["DATABASE_URL"]
    table, ids = request.form.get("table", ""), request.form.getlist("id")
    if table not in RECORD_TABLES or not ids:
        flash("Tick at least one record to restore.", "error")
        return redirect(url_for("restorepart.parts", name=name))
    try:
        safety = _safety(url)
    except Exception as e:
        flash(f"The safety backup failed, so nothing was restored: {e}", "error")
        return redirect(url_for("restorepart.parts", name=name))
    try:
        written = restore_records(path, url, table, ids)
    except ValueError as e:
        audit("backup.restore_records_failed", target=name, detail=f"{table}: {str(e)[:250]}")
        flash(f"Nothing was changed: {e}", "error")
        return redirect(url_for("restorepart.parts", name=name))
    bk.apply_retention()
    audit("backup.restore_records", target=name, detail=f"{table} x{len(ids)}; safety backup {safety}")
    flash(f"Restored {len(ids)} {RECORD_LABELS[table].lower()} from {name}: {_summary(written)}. The previous data was saved first as {safety}.", "success")
    return redirect(url_for("admin.backups"))
