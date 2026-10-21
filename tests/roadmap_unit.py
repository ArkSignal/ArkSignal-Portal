"""In-container checks of the two roadmaps: one list, items marked audience admin appear only on Admin > Full Roadmap and never on the public page or /api/roadmap.json.
docker cp to /tmp and run: docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/roadmap_unit.py"""
import os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"; os.environ["HUB_URL"] = ""
from app import create_app
from app import roadmap as rm

app = create_app()
ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)

doc = {"updated": "x", "items": [{"title": "Public thing", "summary": "s", "status": "planned", "area": "Map"},
                                  {"title": "Admin thing", "summary": "s", "status": "planned", "area": "Hub", "audience": "admin"},
                                  {"title": "Odd one", "summary": "s", "status": "planned", "area": "Hub", "audience": "everyone"}]}
v = rm._validate(doc)
check("audience is kept as admin or public, anything else is public", [i["audience"] for i in v["items"]] == ["public", "admin", "public"])
b = rm._validate(rm._builtin())
check("the built-in list marks Read-only production access admin only", any(i["title"] == "Read-only production access" and i["audience"] == "admin" for i in b["items"]))
rm.load = lambda: (v, {"source": "builtin", "at": 0, "error": ""})
rm.livecontent.iso = lambda t: ""
c = app.test_client()
pub = c.get("/roadmap").get_data(as_text=True)
check("the public page shows public items and not admin ones", "Public thing" in pub and "Admin thing" not in pub)
api = c.get("/api/roadmap.json").get_data(as_text=True)
check("the public data file leaves admin items (and the audience field) out", "Public thing" in api and "Admin thing" not in api and "audience" not in api)
check("the admin page is not open to the public", c.get("/admin/roadmap").status_code in (302, 401, 403, 404))
print("ALL PASS" if ok_all else "SOME FAILED")
