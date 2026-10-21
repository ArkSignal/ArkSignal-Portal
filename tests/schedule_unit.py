"""Runs inside the container: docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/schedule_unit.py"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from app.airports import update_due, next_update
ok_all = True
def check(name, cond):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name); ok_all &= bool(cond)
tz = ZoneInfo("America/Chicago")
def at(day, hh, mm=0): return datetime(2026, 10, day, hh, mm, tzinfo=tz).astimezone(timezone.utc)
last = at(1, 3, 40).isoformat()
check("off when days is 0", not update_due(at(20, 4), last, "", 0, "03:30", tz))
check("not due before the interval has passed", not update_due(at(10, 4), last, "", 30, "03:30", tz))
check("due once the interval has passed and the time of day arrived", update_due(at(31, 3, 30), last, "", 30, "03:30", tz) or update_due(at(31, 4), last, "", 30, "03:30", tz))
check("waits for the chosen time of day", not update_due(at(31, 2), last, "", 30, "03:30", tz))
check("due when never loaded", update_due(at(5, 12), "", "", 30, "03:30", tz))
check("a failed try is not repeated for six hours", not update_due(at(31, 5), last, at(31, 3, 31).isoformat(), 30, "03:30", tz))
check("but is retried after that", update_due(at(31, 10), last, at(31, 3, 31).isoformat(), 30, "03:30", tz))
check("status text says when", "2026-10-31 03:30" in next_update(last, 30, "03:30", tz) and "off" in next_update(last, 0, "03:30", tz))
print("ALL PASS" if ok_all else "SOME FAILED")
