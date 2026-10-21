import os
"""Update Now button, end to end against the Watchtower test stack (~/updates on the test box, portal on :18432).
Not part of run_all (needs that stack). Usage: python updatenow_test.py <admin password>"""
import sys, time, subprocess
from selenium import webdriver
from selenium.webdriver.common.by import By
B = f"http://{os.environ.get('TEST_HOST', 'test-box.invalid')}:18432"; PW = sys.argv[1]
def ssh(cmd): return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True).stdout
D = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker "
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); d = webdriver.Chrome(options=o); bad = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n, "" if c else x)
    if not c: bad.append(n)
def version(): d.get(B + "/admin/updates"); return d.find_element(By.XPATH, "//th[text()='Version']/following-sibling::td").text
try:
    d.get(B + "/my/login"); d.find_element(By.NAME, "username").send_keys("marty"); d.find_element(By.NAME, "password").send_keys(PW)
    d.find_element(By.CSS_SELECTOR, "form .btn").click(); time.sleep(1.5)
    v0 = version(); print("running", v0)
    btn = d.find_elements(By.XPATH, "//button[text()='Update Now']")
    check("Update Now button is shown", len(btn) == 1)
    d.execute_script("window.confirm=function(){return true}")
    btn[0].click(); time.sleep(6)
    msg = d.find_element(By.ID, "action-feedback").text; print(msg)
    check("the button gets an answer (finished or restarting)", "finished" in msg or "started" in msg, msg)
    time.sleep(15)
    v1 = version(); print("now running", v1)
    check("a newer release was installed", v1 != v0, v1)
finally:
    d.quit()
print("ALL PASS" if not bad else "SOME FAILED"); sys.exit(1 if bad else 0)
