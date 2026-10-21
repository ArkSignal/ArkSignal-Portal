
import os, json
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
from datetime import datetime, timezone
from app import radiolists as rl
def E(**k):
    d = dict(recurrence="weekly", weekday=1, nth=None, at_time="19:30", tz="America/Chicago"); d.update(k); return d
def runs(e, now, n=3):
    return [r.isoformat() for r in rl.next_runs(e, now, n)]
out = {}
now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)        # a Thursday in winter (CST, UTC-6)
out["weekly"] = runs(E(), now, 2)                                # Tuesdays 19:30 CST = 01:30 UTC on Wednesday
out["weekly_summer"] = runs(E(), datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc), 1)   # CDT, UTC-5
out["monthly_2nd_thu"] = runs(E(recurrence="monthly", weekday=3, nth=2, at_time="20:00"), now, 2)
out["last_sunday"] = runs(E(recurrence="monthly", weekday=6, nth=5, at_time="08:00"), now, 2)
out["daily"] = runs(E(recurrence="daily", weekday=None, at_time="07:00"), now, 2)
out["irregular"] = runs(E(recurrence="irregular", at_time=""), now)
out["today_passed"] = runs(E(weekday=3, at_time="05:00"), now, 1)   # Thursday 05:00 CST already past at 12:00 UTC? 12:00 UTC = 06:00 CST, so next Thursday
out["today_later"] = runs(E(weekday=3, at_time="20:00"), now, 1)    # later today
out["when_weekly"] = rl.when_text(E())
out["when_monthly"] = rl.when_text(E(recurrence="monthly", weekday=3, nth=2, at_time="20:00"))
out["when_last"] = rl.when_text(E(recurrence="monthly", weekday=6, nth=5, at_time="08:00"))
out["when_daily"] = rl.when_text(E(recurrence="daily", weekday=None, at_time="07:00"))
out["dist"] = round(rl.distance_mi(34.7465, -92.2896, 36.0626, -94.1574))
print(json.dumps(out))
