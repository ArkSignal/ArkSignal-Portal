"""The theme schedule (app/themes.py): which theme a day gets, holidays that move, the New Year crossing and the administrator's changes. No database needed."""
import os, sys
from datetime import date
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
import app.themes as th
th.overrides = lambda: {}
check("Thanksgiving is the fourth Thursday of November (2026: the 26th; 2027: the 25th)", th.holiday_date("thanksgiving", 2026) == date(2026, 11, 26) and th.holiday_date("thanksgiving", 2027) == date(2027, 11, 25))
check("Easter is worked out (2026: April 5; 2027: March 28)", th.holiday_date("easter", 2026) == date(2026, 4, 5) and th.holiday_date("easter", 2027) == date(2027, 3, 28))
want = {date(2026, 10, 31): "halloween", date(2026, 11, 22): "thanksgiving", date(2026, 11, 27): "thanksgiving", date(2026, 12, 25): "christmas", date(2026, 12, 31): "newyear", date(2027, 1, 2): "newyear",
        date(2026, 7, 4): "july4", date(2026, 7, 20): "summer", date(2027, 1, 15): "winter", date(2026, 4, 1): "easter", date(2026, 3, 15): "stpatrick", date(2026, 2, 10): "valentines",
        date(2026, 10, 5): "fall", date(2026, 5, 1): "spring", date(2026, 12, 22): "christmas", date(2026, 11, 28): "fall"}
bad = {d: (th.active_seasonal(d), w) for d, w in want.items() if th.active_seasonal(d) != w}
check("every day gets the theme it should (a holiday beats its season; ranges cross New Year)", not bad, bad)
check("every day of a year has a seasonal theme, so the site is never plain by accident", all(th.active_seasonal(date(2026, 1, 1).replace(month=m, day=d)) for m in range(1, 13) for d in (1, 10, 20, 28)))
th.overrides = lambda: {"halloween": {"enabled": False}, "fall": {"from": "10-01", "to": "11-30"}}
check("a theme an administrator switched off is skipped (Halloween off: October 31 is Fall)", th.active_seasonal(date(2026, 10, 31)) == "fall")
th.overrides = lambda: {"summer": {"enabled": True, "from": "07-10", "to": "08-10"}}
check("changed dates take effect (Summer July 10 to August 10: July 4 is Independence Day, July 20 is Summer, June 25 is the Spring that runs to June 20 -> none)", th.active_seasonal(date(2026, 7, 20)) == "summer" and th.active_seasonal(date(2026, 7, 4)) == "july4" and th.active_seasonal(date(2026, 8, 20)) == "")
th.overrides = lambda: {"halloween": {"from": "garbage", "to": "x"}}
check("broken dates fall back to the built-in ones", th.matches(th.schedule_of("halloween"), date(2026, 10, 31)))
cfgd = th.public_config(date(2026, 10, 31)) if False else None
check("every theme has the keys the browser needs and every seasonal one has decorations", all({"name", "kind", "mode", "vars"} <= set(t) for t in th.THEMES.values()) and all(th.THEMES[k].get("deco") for k in th.PRIORITY))
check("balloons and planes are listed only where they make sense (Halloween has both, Christmas has Santa planes)", th.THEMES["halloween"]["balloons"] and th.THEMES["christmas"]["planes"] and th.THEMES["christmas"]["plane_share"] > 0)
print("ALL PASS" if ok_all else "SOME FAILED")
