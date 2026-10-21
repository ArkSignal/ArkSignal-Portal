import os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"; os.environ["HUB_URL"] = ""
from app import create_app
from app import arkansas as ar
app = create_app()
ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)
with app.app_context():
    check("places in the state are inside", all(ar.contains(la, lo) for la, lo in ((34.75, -92.3), (36.06, -94.16), (33.2, -92.66), (35.84, -90.7), (34.5, -93.05))))
    check("Dallas, Joplin and Shreveport are not", not any(ar.contains(la, lo, 15) for la, lo in ((32.78, -96.8), (37.08, -94.5), (32.5, -93.75), (36.15, -95.99))))
    check("a place just over the line counts with the margin and not without it", not ar.contains(35.15, -90.05) and ar.contains(35.15, -90.05, 15) and not ar.contains(32.5, -93.75, 15) and ar.contains(32.5, -93.75, 60))
    check("a place with no position cannot be judged and is kept", ar.contains(None, None) and ar.contains(34.0, None))
    c = ar.js_config()
    check("the page gets the switch, the margin and a lighter outline", c["on"] and c["margin"] == 15 and 300 < len(c["poly"]) < 1200, (c["on"], c["margin"], len(c["poly"])))
print("ALL PASS" if ok_all else "SOME FAILED")
