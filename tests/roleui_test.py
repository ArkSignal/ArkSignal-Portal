"""Assign and remove a role by clicking the buttons in headless Chrome (the in-page JS path, not a direct POST).
Regression: a hidden field named "action" shadowed form.action, so every in-page form posted to the wrong URL (404)."""
import os, re, sys, time, requests
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import Select
except ImportError:
    print("SKIP roleui_test: selenium not installed"); sys.exit(0)
B = os.environ.get("MOBILE_BASE", f"http://{os.environ.get('TEST_HOST', 'test-box.invalid')}:18430")
PW = re.search(r"password: (\S+)", open(os.path.join(os.path.dirname(__file__), "..", "TEST_CREDENTIALS.md")).read()).group(1)
s = requests.Session()
tok = lambda path: re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + path).text).group(1)
if "roleui" not in s.get(B + "/my/login").text:
    s.post(B + "/my/register", data={"csrf_token": tok("/my/register"), "username": "roleui", "email": "roleui@example.com", "password": "Sup3r-secret-pw!x", "confirm": "Sup3r-secret-pw!x"})
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1280,900")
d = webdriver.Chrome(options=o); bad = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n, "" if c else x)
    if not c: bad.append(n)
def mid(el):   # the page has a sticky menu and footer: bring the element to the middle before clicking
    d.execute_script("arguments[0].scrollIntoView({block:'center'})", el); time.sleep(.3); return el
def row():
    return [tr for tr in d.find_elements(By.CSS_SELECTOR, "#users-table tr")[1:] if tr.find_element(By.TAG_NAME, "td").text == "roleui"][0]
try:
    d.get(B + "/my/login"); d.find_element(By.NAME, "username").send_keys("marty"); d.find_element(By.NAME, "password").send_keys(PW)
    d.execute_script("arguments[0].click()", d.find_element(By.CSS_SELECTOR, "form .btn")); time.sleep(1.5)
    d.get(B + "/admin/users"); time.sleep(.5)
    sel = row().find_element(By.CSS_SELECTOR, "select[name=role_id]"); Select(sel).select_by_visible_text("Admin")
    mid(row().find_element(By.CSS_SELECTOR, "form.inline-row .btn")).click(); time.sleep(1)
    msg = d.find_element(By.ID, "action-feedback").text
    check("clicking Assign works in the browser", "Admin assigned to roleui" in msg, msg)
    d.execute_script("window.confirm=function(){return true}")
    mid(row().find_elements(By.CSS_SELECTOR, "button.x")[-1]).click(); time.sleep(1)
    msg = d.find_element(By.ID, "action-feedback").text
    check("clicking the x removes the role in the browser", "removed from roleui" in msg, msg)
finally:
    d.quit()
print("ALL PASS" if not bad else "SOME FAILED")
sys.exit(1 if bad else 0)
