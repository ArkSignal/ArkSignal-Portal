"""Admin area allow-list (app/adminallow.py), run inside the portal container."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
from app import create_app
from app import adminallow as aa
from app.settings import cfg, set_setting
from werkzeug.exceptions import NotFound
app = create_app()
with app.app_context():
    nets, bad = aa.parse("203.0.113.7, 192.168.1.0/24\nprivate; nonsense")
    check("the list takes addresses, networks and the word private, separated by commas, semicolons or lines, and reports what it cannot read", len(nets) >= 8 and bad == ["nonsense"], (len(nets), bad))
    check("an empty list lets everyone in", aa.allowed("198.51.100.4", ""))
    check("an address on the list is allowed, another is not, an IPv6 network works, a broken address is not", aa.allowed("203.0.113.7", "203.0.113.7") and not aa.allowed("203.0.113.8", "203.0.113.7") and aa.allowed("2001:db8::5", "2001:db8::/32") and not aa.allowed("garbage", "203.0.113.7"))
    check("private means home and office network addresses", aa.allowed("10.1.2.3", "private") and aa.allowed("192.168.0.9", "private") and not aa.allowed("8.8.8.8", "private"))
    old = cfg("admin_allow_addresses")
    try:
        set_setting("admin_allow_addresses", "203.0.113.0/24")
        with app.test_request_context("/admin/users", environ_overrides={"REMOTE_ADDR": "198.51.100.9"}):
            try:
                aa.enforce(); denied = False
            except NotFound:
                denied = True
        check("an admin page is not found from an address that is not on the list", denied)
        with app.test_request_context("/admin/users", environ_overrides={"REMOTE_ADDR": "203.0.113.50"}):
            check("and is served to one that is", aa.enforce() is None)
        with app.test_request_context("/live", environ_overrides={"REMOTE_ADDR": "198.51.100.9"}):
            check("public pages are never affected", aa.enforce() is None)
        with app.test_request_context("/admin/", environ_overrides={"REMOTE_ADDR": "203.0.113.50"}):
            check("a list that leaves the person out is refused with the reason", "lock you out" in aa.check("198.51.100.0/24") and aa.check("203.0.113.0/24") == "" and "not addresses" in aa.check("nope"))
        set_setting("admin_allow_addresses", "")
        with app.test_request_context("/admin/users", environ_overrides={"REMOTE_ADDR": "198.51.100.9"}):
            check("emptying it opens the Admin area again", aa.enforce() is None)
    finally:
        set_setting("admin_allow_addresses", old)
print("ALL PASS" if ok_all else "SOME FAILED")
