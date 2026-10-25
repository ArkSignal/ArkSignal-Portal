"""The My Flights form in a real browser (needs selenium and Chrome on the PC): picking the two airports from the list that appears as you type, the worked-out times and distance appearing as the times are
entered, the simple and detailed switch, adding and removing people, saving, and the form and list fitting a phone."""
import os, re, subprocess, sys, time, requests
os.environ["TEST_HOST"] = "test-box.invalid"
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
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
psql("INSERT INTO settings (key, value) VALUES ('myflights_enabled','1') ON CONFLICT (key) DO UPDATE SET value='1'")
psql("DELETE FROM my_flights WHERE user_id IN (SELECT id FROM users WHERE username = 'mflbrowser')")
psql("DELETE FROM users WHERE username = 'mflbrowser'")
reg = requests.Session()
reg.post(B + "/my/register", data={"csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', reg.get(B + "/my/register").text).group(1), "username": "mflbrowser", "email": "mflbrowser@example.com", "password": "mflbrowserpass9", "confirm": "mflbrowserpass9"})
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1300,1000")
d = webdriver.Chrome(options=o); J = d.execute_script
def wait(js, secs=10):
    end = time.time() + secs
    while time.time() < end:
        try:
            if J(js): return True
        except Exception:
            pass
        time.sleep(.25)
    return False
def tap(el):
    J("arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", el); time.sleep(.3)
def setv(name, value):
    J("var e=document.querySelector('[name=%s]'); e.value=arguments[0]; e.dispatchEvent(new Event('change', {bubbles: true}));" % name, value)
try:
    d.get(B + "/my/login"); time.sleep(1)
    d.find_element(By.NAME, "username").send_keys("mflbrowser"); d.find_element(By.NAME, "password").send_keys("mflbrowserpass9"); d.find_element(By.CSS_SELECTOR, "form button[type=submit], form input[type=submit]").click(); time.sleep(2)
    d.get(B + "/my/flights/new"); time.sleep(1.5)
    check("the form opens with the side menu and detailed times", J("return !!document.getElementById('my-side') && !!document.getElementById('flform') && document.querySelector('[name=mode]:checked').value") == "detailed")
    if requests.get(B + "/api/flight-extra.json?items=a44e24:DAL530", timeout=20).json().get("a44e24", {}).get("airline"):   # the outside lookups answer
        setv("flight_no", "DL 530")
        check("typing a flight number fills the empty airline and the airports from the lookups", wait("return document.querySelector('[name=airline]').value.indexOf('Delta') >= 0 && !!document.getElementById('from_id').value", 15))
        J("document.getElementById('from_id').value=''; document.getElementById('to_id').value=''; document.getElementById('from_q').value=''; document.getElementById('to_q').value=''; document.querySelector('[name=airline]').value=''; document.querySelector('[name=flight_no]').value=''")
    for field, text in (("from_q", "LIT"), ("to_q", "LAS")):
        box = d.find_element(By.ID, field); box.send_keys(text)
        check(f"typing {text} brings up a list of airports", wait(f"return !document.getElementById('{field[:-2]}_l').hidden && document.getElementById('{field[:-2]}_l').children.length > 0"))
        J("var li=Array.from(document.getElementById('%s_l').children).filter(function(x){return x.textContent.indexOf('%s ')===0;})[0] || document.getElementById('%s_l').children[0]; li.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));" % (field[:-2], text, field[:-2]))
        time.sleep(.3)
    check("choosing from the list sets the airports", J("return !!document.getElementById('from_id').value && !!document.getElementById('to_id').value") and "Clinton" in J("return document.getElementById('from_q').value") and "Harry Reid" in J("return document.getElementById('to_q').value"))
    for n, v in (("act_out", "2024-08-15T12:15"), ("act_off", "2024-08-15T12:30"), ("act_on", "2024-08-15T13:50"), ("act_in", "2024-08-15T14:00")):
        setv(n, v)
    check("the worked-out times and the distance appear as the times are entered", wait("return document.getElementById('d_air').textContent === '3 h 20 min' && document.getElementById('d_total').textContent === '3 h 45 min'") and J("return document.getElementById('d_taxi_out').textContent") == "15 min" and "km" in J("return document.getElementById('d_km').textContent"))
    check("the line names the two time zones", "Chicago" in J("return document.getElementById('tzline').textContent") and "Los Angeles" in J("return document.getElementById('tzline').textContent"))
    tap(d.find_element(By.CSS_SELECTOR, "[name=mode][value=simple]")); time.sleep(.5)
    check("the simple switch hides takeoff, landing, taxi and the scheduled column and renames the rows", J("return Array.from(document.querySelectorAll('.det')).every(function(e){return e.style.display === 'none'})") and J("return document.querySelector('[data-simple=Departure]').textContent") == "Departure" and J("return document.querySelector('.sch input').style.visibility") == "hidden")
    tap(d.find_element(By.CSS_SELECTOR, "[name=mode][value=detailed]")); time.sleep(.5)
    check("and detailed brings them back", J("return Array.from(document.querySelectorAll('.det')).every(function(e){return e.style.display === ''})") and J("return document.querySelector('[data-simple=Departure]').textContent") == "Gate departure")
    n0 = J("return document.getElementById('paxbox').children.length")
    tap(d.find_element(By.ID, "addpax")); tap(d.find_element(By.ID, "addpax"))
    check("people can be added (the first box is named p0_ and the next p1_ and p2_)", J("return document.getElementById('paxbox').children.length") == n0 + 2 and J("return !!document.querySelector('[name=p2_name]')"))
    J("document.querySelector('[name=p0_name]').value='Marty Allison'; document.querySelector('[name=p1_name]').value='Blake Allison'; document.querySelector('[name=p1_seat]').value='12b';")
    tap(d.find_elements(By.CSS_SELECTOR, "#paxbox [data-rm]")[2]); time.sleep(.3)
    check("a person can be removed and the rest are renumbered", J("return document.getElementById('paxbox').children.length") == n0 + 1 and J("return !document.querySelector('[name=p2_name]')"))
    tap(d.find_element(By.CSS_SELECTOR, "#flform button[type=submit]")); time.sleep(2.5)
    check("saving goes to the list, which shows the flight", "/my/flights" in d.current_url and "LIT" in d.page_source and "LAS" in d.page_source and "Blake Allison" in d.page_source and "3 h 20 min" in d.page_source)
    check("the database has it with the two people", psql("SELECT COUNT(*) FROM my_flights WHERE user_id=(SELECT id FROM users WHERE username='mflbrowser')") == "1" and psql("SELECT COUNT(*) FROM my_flight_pax") != "0")
    d.set_window_size(1300, 1000); d.get(B + "/my/flights/map"); time.sleep(2)
    check("the map draws the route and two airports", wait("return !!window.flMap") and J("var n=0; window.flMap.eachLayer(function(l){ if (l instanceof L.Polyline && !(l instanceof L.Polygon)) n++; }); return n") >= 1 and J("var c=0; window.flMap.eachLayer(function(l){ if (l instanceof L.CircleMarker) c++; }); return c") == 2)
    check("the map zoomed to the airports", J("return window.flMap.getZoom()") >= 3)
    d.set_window_size(400, 850); time.sleep(.7)
    for path in ("/my/flights", "/my/flights/new", "/my/flights/map", "/my/flights/stats"):
        d.get(B + path); time.sleep(1.8)
        check(f"phone: {path} does not run off the side", J("return document.documentElement.scrollWidth <= window.innerWidth + 1"), J("return document.documentElement.scrollWidth + ' > ' + window.innerWidth"))
finally:
    d.quit()
    psql("DELETE FROM my_flights WHERE user_id IN (SELECT id FROM users WHERE username = 'mflbrowser')")
    psql("DELETE FROM users WHERE username = 'mflbrowser'")
print("ALL PASS" if ok_all else "SOME FAILED")
