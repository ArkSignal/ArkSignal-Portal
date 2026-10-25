"""Set the throwaway test site (http://test-box.invalid:18430) up so Matrix chat can be tried by hand in a browser: start the Matrix containers if they are not running, turn Matrix Chat on and apply it, make two
test members to chat between (their passwords are written to TEST_CREDENTIALS.md, which is not committed), and write a small script that opens Chrome the way Element needs on this plain-http test box.
Run it again whenever a test run has switched Matrix off. Usage: python tests/matrix_demo.py"""
import os, re, secrets, subprocess, sys, time, requests
here = os.path.dirname(os.path.abspath(__file__))
os.chdir(here)
os.environ["TEST_HOST"] = "test-box.invalid"
sys.path.insert(0, here)
HS, EL, PORTAL = "http://test-box.invalid:18008", "http://test-box.invalid:18009", "http://test-box.invalid:18430"
def up(url):
    try:
        return requests.get(url, timeout=4).status_code == 200
    except requests.RequestException:
        return False
if not (up(HS + "/health") and up(EL + "/")):
    print("Starting the Matrix containers on the test box (a few minutes the first time)...")
    subprocess.run(["sh", "tests/deploy_matrix.sh"], cwd=os.path.dirname(here), check=False)
import mxon   # writes the Matrix settings (server name, addresses, return address) into the throwaway site and turns Matrix on
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
adm = sess("marty", PW)
ok, msg, _h = act(adm, "/admin/matrix/apply", "/admin/matrix")
print("Apply:", msg)
for _ in range(60):
    if up(HS + "/health"):
        break
    time.sleep(3)
time.sleep(5)
ok, msg, _h = act(adm, "/admin/matrix/sync", "/admin/matrix")
print("Sync Now:", msg)
cred = open(os.path.join(os.path.dirname(here), "TEST_CREDENTIALS.md"), encoding="utf-8").read() if os.path.exists(os.path.join(os.path.dirname(here), "TEST_CREDENTIALS.md")) else ""
lines = []
for name in ("chatdemo1", "chatdemo2"):
    mine = re.search(rf"{name}\s*/\s*(\S+)", cred)
    pw = mine.group(1) if mine else secrets.token_urlsafe(12)
    subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -c \"DELETE FROM users WHERE username='{name}'\""], capture_output=True)
    s = requests.Session()
    t = re.search(r'name="csrf_token" value="([^"]+)"', s.get(PORTAL + "/my/register").text).group(1)
    s.post(PORTAL + "/my/register", data={"csrf_token": t, "username": name, "email": name + "@example.com", "password": pw, "confirm": pw})
    lines.append(f"{name} / {pw}")
act(adm, "/admin/matrix/sync", "/admin/matrix")
with open(os.path.join(os.path.dirname(here), "TEST_CREDENTIALS.md"), "a", encoding="utf-8") as f:
    if "chatdemo1" not in cred:
        f.write("\n# Matrix chat demo members on the throwaway site (tests/matrix_demo.py), user / password:\n" + "\n".join(lines) + "\n")
bat = os.path.join(here, "open_matrix_test.bat")
open(bat, "w").write('@echo off\r\nrem Opens Chrome with its own profile and lets Element run on the plain-http test box (it insists on a secure page otherwise).\r\n'
                     'start "" "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --user-data-dir="%TEMP%\\matrixtest" --no-first-run '
                     f'--unsafely-treat-insecure-origin-as-secure={PORTAL},{EL},{HS} {PORTAL}/chat/\r\n')
print("\nReady. Open", PORTAL + "/chat/", "with tests\\open_matrix_test.bat (a separate Chrome window).")
print("Sign in as marty (TEST_CREDENTIALS.md), and as chatdemo1 in a second window or a private window; the chatdemo passwords are at the end of TEST_CREDENTIALS.md.")
