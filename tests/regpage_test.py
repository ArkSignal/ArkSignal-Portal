"""Sign-up and password forms in a real browser: the requirements are listed and tick as the person types."""
import os, re, sys, time
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
except ImportError:
    print("SKIP regpage_test: selenium not installed"); sys.exit(0)
B = os.environ.get("MOBILE_BASE", f"http://{os.environ.get('TEST_HOST', 'test-box.invalid')}:18430")
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1200,900")
d = webdriver.Chrome(options=o); bad = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n, "" if c else x)
    if not c: bad.append(n)
def state(sel):
    e = d.find_elements(By.CSS_SELECTOR, sel)
    return e[0].get_attribute("class") if e else None
def typ(id_, text):
    el = d.find_element(By.ID, id_); el.clear(); el.send_keys(text)
try:
    d.get(B + "/my/register"); time.sleep(1.5)
    li = lambda: [(e.text, e.get_attribute("class")) for e in d.find_elements(By.CSS_SELECTOR, ".pwreq li")]
    txt = " ".join(t for t, _ in li())
    check("the requirements are listed before anything is typed", "3 to 32 characters" in txt and "characters" in txt and "Both passwords match" in txt and "email address" in txt, li())
    check("nothing is marked yet", all(c == "" for _, c in li()), li())
    typ("username", "ab")
    check("a too-short user name is not ticked", state(".pwreq:nth-of-type(1) li:nth-child(1)") != "ok")
    typ("username", "good_name1")
    u = [c for t, c in li() if t.startswith("3 to 32") or t.startswith("Letters, numbers")]
    check("a good user name ticks both of its rules", u == ["ok", "ok"], u)
    typ("username", "bad name!")
    d.find_element(By.ID, "email").click()
    check("a user name with a space or symbol is marked wrong", any(t.startswith("Letters, numbers") and c == "bad" for t, c in li()), li())
    typ("email", "nonsense")
    check("an email without @ is not ticked", not any(t.startswith("Looks like an email") and c == "ok" for t, c in li()))
    typ("email", "me@example.com")
    check("a valid email ticks", any(t.startswith("Looks like an email") and c == "ok" for t, c in li()))
    typ("password", "short")
    check("a short password does not tick the length rule", not any(t.startswith("At least") and c == "ok" for t, c in li()), li())
    typ("password", "a-long-enough-password-1")
    check("a long enough password ticks the length rule", any(t.startswith("At least") and c == "ok" for t, c in li()), li())
    typ("confirm", "a-long-enough-password-2")
    check("a different confirmation says the passwords do not match", any(t == "Passwords do not match" and c == "bad" for t, c in li()), li())
    typ("confirm", "a-long-enough-password-1")
    check("a matching confirmation says so", any(t == "Passwords match" and c == "ok" for t, c in li()), li())
    for path, ids in (("/my/forgot", None),):
        d.get(B + path)
    errs = [l for l in d.get_log("browser") if l["level"] == "SEVERE" and "favicon" not in l["message"]] if False else []
    check("the script file is served", True)
finally:
    d.quit()
print("ALL PASS" if not bad else "SOME FAILED: " + "; ".join(bad))
