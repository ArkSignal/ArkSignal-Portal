"""Runs inside the portal container (tests/offsiteafter_test.py): the small copy right after the nightly backup sends that backup (no second dump), and a copy by hand or at a set time makes a snapshot that is removed when its run ends. Prints one JSON line."""
import json, os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
os.environ["DISABLE_MESH_SYNC"] = "1"
from app import create_app
app = create_app()
out = {}
with app.app_context():
    from datetime import datetime, timezone, timedelta
    from app import backup as bk, offsite
    from app.db import get_db
    from app.settings import set_setting, set_secret
    d = get_db()
    url = app.config["DATABASE_URL"]
    d.execute("DELETE FROM offsite_runs"); d.commit()
    for b in bk.list_backups(scan=False):
        if b["kind"] == "scheduled":
            bk.delete_backup(b["name"])
    cfg = offsite.load()
    cfg.update(enabled=True, type="webdav", host="https://nas.example.org/webdav", user="u")
    cfg["small"]["mode"] = "after_backup"; cfg["small"]["every_days"] = 1
    set_setting("offsite_config", json.dumps(cfg))
    set_secret("offsite_secret", "pw"); set_secret("offsite_pass", "phrase")
    d.commit()
    now = datetime.now(timezone.utc)
    out["nothing_due_before_a_nightly_backup_exists"] = "small" not in offsite.due(now)
    import time
    first, _ = bk.create_backup(url, "scheduled")
    time.sleep(31)
    out["due_once_the_nightly_backup_exists"] = "small" in offsite.due(datetime.now(timezone.utc))
    client = app.test_client()
    tok = os.environ.get("STACK_TOKEN", "") or app.config.get("STACK_TOKEN", "")
    hdr = {"Authorization": "Bearer " + tok}
    r = client.post("/internal/backup/start", json={"kind": "small", "manual": False}, headers=hdr)
    j = r.get_json() or {}
    out["the_scheduled_copy_sends_the_nightly_backup_itself"] = j.get("db_backup") == first and d.execute("SELECT db_backup, source_backup FROM offsite_runs WHERE id=%s", (j.get("run_id"),)).fetchone()["db_backup"] == ""
    client.post("/internal/backup/report", json={"run_id": j.get("run_id"), "ok": True, "bytes": 5, "remote_name": "small/x"}, headers=hdr)
    out["and_it_is_left_in_the_list"] = os.path.exists(bk.backup_path(first))
    out["not_due_again_for_the_same_backup"] = "small" not in offsite.due(datetime.now(timezone.utc))
    second, _ = bk.create_backup(url, "scheduled")
    time.sleep(31)
    # the days between copies still count: a day has not passed
    cfg["small"]["every_days"] = 2; set_setting("offsite_config", json.dumps(cfg)); d.commit()
    out["the_days_between_copies_are_kept"] = "small" not in offsite.due(datetime.now(timezone.utc))
    cfg["small"]["every_days"] = 1; set_setting("offsite_config", json.dumps(cfg))
    d.execute("UPDATE offsite_runs SET finished = finished - interval '1 day' WHERE kind='small' AND ok"); d.commit()   # the next night
    out["a_newer_nightly_backup_makes_it_due_again"] = "small" in offsite.due(datetime.now(timezone.utc))
    # by hand: a fresh snapshot, removed when the run ends
    r = client.post("/internal/backup/start", json={"kind": "small", "manual": True}, headers=hdr)
    j2 = r.get_json() or {}
    snap = j2.get("db_backup")
    out["a_copy_by_hand_makes_its_own_snapshot"] = bool(snap) and snap not in (first, second) and os.path.exists(bk.backup_path(snap))
    client.post("/internal/backup/report", json={"run_id": j2.get("run_id"), "ok": True, "bytes": 5, "remote_name": "small/y"}, headers=hdr)
    out["and_removes_it_when_the_run_ends"] = not os.path.exists(bk.backup_path(snap)) and os.path.exists(bk.backup_path(second))
    # at a set time: its own snapshot too
    cfg["small"]["mode"] = "time"; set_setting("offsite_config", json.dumps(cfg)); d.commit()
    r = client.post("/internal/backup/start", json={"kind": "small", "manual": False}, headers=hdr)
    j3 = r.get_json() or {}
    out["at_a_set_time_it_makes_its_own_snapshot"] = bool(j3.get("db_backup")) and j3["db_backup"] not in (first, second)
    client.post("/internal/backup/report", json={"run_id": j3.get("run_id"), "ok": True, "bytes": 5, "remote_name": "small/z"}, headers=hdr)
    out["and_removes_that_too"] = not os.path.exists(bk.backup_path(j3["db_backup"]))
    for n in (first, second):
        bk.delete_backup(n)
    d.execute("DELETE FROM offsite_runs"); d.execute("DELETE FROM settings WHERE key IN ('offsite_config','offsite_secret','offsite_pass')"); d.commit()
print(json.dumps(out))
