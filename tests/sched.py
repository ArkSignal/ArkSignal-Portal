import os, time
os.environ["DISABLE_BACKUP_SCHEDULER"]="1"
from app import create_app
from app import backup as bk
from app.settings import set_setting, get_setting
app=create_app()
with app.app_context():
    set_setting("backup_schedule","hourly"); set_setting("backup_retention","2"); set_setting("backup_last_run_at","")
    print("due when never run:", bk.is_due())
bk.run_scheduled(app)
with app.app_context():
    sched=[b for b in bk.list_backups() if b["kind"]=="scheduled"]
    print("scheduled backups after tick 1:", len(sched), "| status:", get_setting("backup_last_status"))
    print("due right after run:", bk.is_due())
bk.run_scheduled(app)
with app.app_context():
    print("tick 2 made none:", len([b for b in bk.list_backups() if b["kind"]=="scheduled"])==1)
    # retention: force 3 more runs
    for i in range(3):
        set_setting("backup_last_run_at","")
        time.sleep(1.1)
        bk.run_scheduled(app) if False else None
for i in range(3):
    with app.app_context(): set_setting("backup_last_run_at","")
    time.sleep(1.1); bk.run_scheduled(app)
with app.app_context():
    sched=[b for b in bk.list_backups() if b["kind"]=="scheduled"]
    print("scheduled kept with retention=2:", len(sched))
    set_setting("backup_schedule","off"); print("off => never due:", bk.is_due()==False)
    from datetime import datetime, timezone, timedelta
    set_setting("backup_schedule","daily"); set_setting("backup_time","02:00")
    set_setting("backup_last_run_at",(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()); print("daily, last run 2 days ago => due:", bk.is_due())
    set_setting("backup_last_run_at",datetime.now(timezone.utc).isoformat()); print("daily, just ran => not due:", not bk.is_due())
    set_setting("backup_schedule","daily"); set_setting("backup_retention","14"); set_setting("backup_last_run_at","")
    for b in bk.list_backups():
        if b["kind"]=="scheduled": bk.delete_backup(b["name"])
