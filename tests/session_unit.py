"""Runs inside the container: docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/session_unit.py
Session limits, inactivity sign-out, administrator limits, sign-everyone-out, password rules and age."""
import os, re
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
os.environ["HUB_URL"] = ""
import psycopg
from app import create_app, security
from app.models import User
from app.settings import set_setting

app = create_app()
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
URL = app.config["DATABASE_URL"]
clock = [1_800_000_000.0]
security.now = lambda: clock[0]

def settings(**kw):
    with app.app_context():
        for k, v in kw.items(): set_setting(k, str(v))
DEFAULTS = dict(idle_timeout_minutes=120, admin_idle_timeout_minutes=30, session_hours=12, admin_session_hours=8, password_max_age_days=0, password_require_mix=0, min_password_length=12)
settings(**DEFAULTS)
with psycopg.connect(URL, autocommit=True) as c:
    c.execute("DELETE FROM users WHERE username IN ('sess_user','sess_admin','sess_user2')")
with app.app_context():
    User.create("sess_user", "sess_user@example.com", "sessionpass-123")
    User.create("sess_user2", "sess_user2@example.com", "sessionpass-123")
    User.create("sess_admin", "sess_admin@example.com", "sessionpass-123", role_name="Admin")

def token(cl, path):
    return re.search(r'name="csrf_token" value="([^"]+)"', cl.get(path).get_data(as_text=True)).group(1)
def login(name):
    cl = app.test_client()
    r = cl.post("/my/login", data={"csrf_token": token(cl, "/my/login"), "username": name, "password": "sessionpass-123"})
    assert r.status_code == 302, r.status_code
    return cl
def alive(cl, path="/my/"): return cl.get(path).status_code == 200
def advance(minutes): clock[0] += minutes * 60

# inactivity
settings(idle_timeout_minutes=10)
u = login("sess_user"); check("a fresh sign-in works", alive(u))
advance(9); check("still signed in after 9 minutes", alive(u))
advance(9); check("activity restarts the inactivity clock (18 minutes in, 9 since last)", alive(u))
advance(11); r = u.get("/my/"); check("11 minutes of inactivity signs the person out", r.status_code == 302 and "/my/login" in r.headers["Location"], r.status_code)
check("and tells them why", "without activity" in u.get("/my/login").get_data(as_text=True))

# background requests do not keep a session alive
u = login("sess_user")
advance(9); check("a background data request is accepted", u.get("/api/aircraft.json").status_code in (200, 503))
advance(2); r = u.get("/my/"); check("but it did not extend the session", r.status_code == 302, r.status_code)
u = login("sess_user"); advance(11)
r = u.get("/api/aircraft.json"); check("background requests after expiry get a 401, not a redirect", r.status_code == 401 and r.get_json()["signed_out"] is True, r.status_code)
u = login("sess_user"); advance(9)
tk = token(u, "/my/")
check("Stay Signed In (keepalive) is accepted", u.post("/my/keepalive", headers={"X-CSRFToken": tk}).status_code == 204)
advance(9); check("and counts as activity (18 minutes in, 9 since the keepalive)", alive(u))

# administrators have shorter limits
settings(idle_timeout_minutes=120, admin_idle_timeout_minutes=5)
a, u = login("sess_admin"), login("sess_user")
advance(6)
check("an administrator is signed out after the shorter limit", a.get("/admin/").status_code == 302)
check("an ordinary person with the same inactivity is not", alive(u))
settings(**DEFAULTS)

# longest session
settings(session_hours=1, admin_session_hours=8, idle_timeout_minutes=0)
u = login("sess_user")
for _ in range(11): advance(5); alive(u)
check("still signed in after 55 active minutes", alive(u))
advance(10); r = u.get("/my/"); check("signed out after the longest session even though active", r.status_code == 302, r.status_code)
settings(**DEFAULTS)
settings(session_hours=0, idle_timeout_minutes=0, admin_idle_timeout_minutes=0)
u = login("sess_user"); advance(60 * 24 * 3); check("with every limit off a session lasts", alive(u))
settings(**DEFAULTS)

# sign everyone out
a, u, u2 = login("sess_admin"), login("sess_user"), login("sess_user2")
html = a.get("/admin/settings").get_data(as_text=True)
check("the Security section offers Sign Everyone Out Now", "Sign Everyone Out Now" in html)
tok = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
advance(1)
r = a.post("/admin/security/sign-out-all", data={"csrf_token": tok}, headers={"X-Requested-With": "XMLHttpRequest"})
check("an administrator can end all other sessions", r.status_code == 200 and r.get_json()["ok"], r.status_code)
check("the administrator stays signed in", alive(a, "/admin/"))
check("everyone else is signed out", u.get("/my/").status_code == 302 and u2.get("/my/").status_code == 302)
check("and can sign in again", alive(login("sess_user")))

# password rules and age
with app.app_context():
    set_setting("password_require_mix", "0"); set_setting("min_password_length", "12")
    check("length rule", security.password_error("short") and security.password_error("longenoughpassword") is None)
    set_setting("password_require_mix", "1")
    check("letters-and-numbers rule", security.password_error("onlylettersnonumbers") and security.password_error("1234567890123") and security.password_error("letters12345678") is None)
    check("the rules are described in plain words", "letters and numbers" in security.password_rules_text())
settings(**DEFAULTS)
settings(password_max_age_days=30)
with psycopg.connect(URL, autocommit=True) as c:
    c.execute("UPDATE users SET password_changed_at = to_timestamp(%s) - interval '40 days' WHERE username='sess_user2'", (clock[0],))
u2 = login("sess_user2"); r = u2.get("/my/")
check("a password older than the limit forces a change", r.status_code == 302 and "/my/password" in r.headers["Location"], (r.status_code, r.headers.get("Location")))
check("the change page itself stays reachable", u2.get("/my/password").status_code == 200)
with psycopg.connect(URL, autocommit=True) as c:      # the test clock is not the database clock: make this password "changed now" on the test clock
    c.execute("UPDATE users SET password_changed_at = to_timestamp(%s) WHERE username='sess_user'", (clock[0],))
check("a recent password is not affected", alive(login("sess_user")))
settings(**DEFAULTS)

with psycopg.connect(URL, autocommit=True) as c:
    c.execute("DELETE FROM users WHERE username IN ('sess_user','sess_admin','sess_user2')")
with app.app_context():
    set_setting("sessions_valid_after", "0")
print("ALL PASS" if ok_all else "SOME FAILED")
