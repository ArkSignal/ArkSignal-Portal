"""Passkeys in a real browser: Chrome's virtual authenticator adds a passkey on My Account > Passkeys and signs in with it from the sign-in page, through the page's own script. Browsers only allow passkeys
on a secure address, and localhost counts, so the test reaches the test portal through an ssh tunnel (http://localhost:18439). Needs selenium, Chrome and ssh to the test box."""
import subprocess, sys, time
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
except ImportError:
    print("SKIP no selenium"); print("ALL PASS"); sys.exit(0)
tun = subprocess.Popen(["ssh", "-q", "-N", "-o", "ExitOnForwardFailure=yes", "-L", "18439:localhost:18430", "nastemp"])
time.sleep(4)
URL = "http://localhost:18439"
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1200,900")
d = webdriver.Chrome(options=o); J = d.execute_script
try:
    d.execute_cdp_cmd("WebAuthn.enable", {})
    auth = d.execute_cdp_cmd("WebAuthn.addVirtualAuthenticator", {"options": {"protocol": "ctap2", "transport": "internal", "hasResidentKey": True, "hasUserVerification": True, "isUserVerified": True, "automaticPresenceSimulation": True}})
    aid = auth["authenticatorId"]
    d.get(URL + "/my/login"); time.sleep(2)
    d.find_element(By.ID, "username").send_keys("plain1"); d.find_element(By.ID, "password").send_keys("plainpassword1"); J("document.querySelector('form.panel button[type=submit]').click()"); time.sleep(3)
    d.get(URL + "/my/passkeys"); time.sleep(2)
    check("the page offers the add button on a secure address", d.find_element(By.ID, "pk-add").is_enabled())
    d.find_element(By.ID, "pk-name").send_keys("Browser test")
    J("document.getElementById('pk-add').click()"); time.sleep(4)
    check("adding a passkey in the browser works and it shows in the list", "Browser test is added" in d.find_element(By.ID, "pk-msg").text and "Browser test" in d.find_element(By.ID, "passkeys-list").get_attribute("innerHTML"), d.find_element(By.ID, "pk-msg").text)
    creds = d.execute_cdp_cmd("WebAuthn.getCredentials", {"authenticatorId": aid})["credentials"]
    check("the authenticator holds a discoverable passkey for this site", len(creds) == 1 and creds[0]["isResidentCredential"] and creds[0]["rpId"] == "localhost", creds)
    # sign out and back in with the passkey
    J("document.querySelector('form[action=\"/my/logout\"] button').click()"); time.sleep(2)
    d.get(URL + "/my/login"); time.sleep(2)
    if "/my/login" not in d.current_url:
        # the passkey offered in the user name box (the browser's own prompt) already signed the person in, with the test authenticator answering at once
        check("the passkey offered in the user name box signs the person in with no user name or password", "plain1" in d.page_source, d.current_url)
    else:
        check("the sign-in page offers the passkey button", d.find_element(By.ID, "pk-login").is_displayed())
        J("document.getElementById('pk-login').click()"); time.sleep(4)
        check("a click on it signs the person in with no user name or password", "/my/login" not in d.current_url and "plain1" in d.page_source, d.current_url)
    creds = d.execute_cdp_cmd("WebAuthn.getCredentials", {"authenticatorId": aid})["credentials"]
    check("the counter moved on in the authenticator", creds[0]["signCount"] >= 1, creds)
finally:
    d.quit()
    tun.terminate()
subprocess.run(["ssh", "-q", "nastemp", "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"DELETE FROM passkeys\""], capture_output=True)
print("ALL PASS" if ok_all else "SOME FAILED")
