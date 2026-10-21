"""Runs inside the container: docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/csrf_unit.py
A form left open for hours must still work (it used to fail with a bare 400 after one hour)."""
import os, re, time
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
os.environ["HUB_URL"] = ""
import itsdangerous.timed
from app import create_app

app = create_app()
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)

real = time.time
def post_after(hours, limit):
    app.config["WTF_CSRF_TIME_LIMIT"] = limit
    c = app.test_client()
    html = c.get("/my/login").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
    itsdangerous.timed.time.time = lambda: real() + hours * 3600       # the page has now been open this long
    try:
        return c.post("/my/login", data={"csrf_token": token, "username": "nobody", "password": "x"}).status_code
    finally:
        itsdangerous.timed.time.time = real
check("the app no longer limits a form token's age", app.config["WTF_CSRF_TIME_LIMIT"] is None)
check("a form open for 3 hours still works", post_after(3, None) == 200)
check("(for reference) with the old one-hour limit the same request is refused with 400", post_after(3, 3600) == 400)
check("a form open for 30 minutes works either way", post_after(0.5, 3600) == 200)
print("ALL PASS" if ok_all else "SOME FAILED")
