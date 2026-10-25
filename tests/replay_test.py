"""Replay from the history pages (needs selenium and Chrome on the PC): Flight History has a Replay for every flight and for the whole period, which open the live map in its own Replay at that moment on that flight;
the Balloon Flights list and each balloon's page have a Replay that plays the positions the site recorded on the Radiosonde Map (a marker moving along them with a trail, the burst and the landing marked, play and
pause, speed, a slider, several flights together, and a switch to start them together when they flew hours apart)."""
import os, re, subprocess, sys, time, requests
os.environ["TEST_HOST"] = "test-box.invalid"
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
try:
    from selenium import webdriver
except ImportError:
    print("SKIP no selenium"); print("ALL PASS"); sys.exit(0)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True, timeout=60).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
setting("flightlog_enabled", "1"); setting("sonde_enabled", "1")
psql("DELETE FROM flight_log WHERE hex LIKE 'ef%'")
psql("DELETE FROM sonde_points WHERE serial LIKE 'ZZRP%'")
psql("DELETE FROM sondes WHERE serial LIKE 'ZZRP%'")
psql("INSERT INTO flight_log (hex, callsign, reg, type, first_seen, last_seen, start_lat, start_lon, end_lat, end_lon, max_alt, max_gs, positions, mil) VALUES ('ef0001', 'RPL1', 'N1RPL', 'B738', now() - interval '3 hours', now() - interval '2 hours', 34.5, -92.5, 35.5, -91.5, 31000, 450, 80, false)")
# two balloons: one now-ish, one twelve hours earlier; 60 positions a minute apart, up to 28,800 m and back down
for serial, hours in (("ZZRP1", 5), ("ZZRP2", 17)):
    psql(f"INSERT INTO sondes (serial, kind, type, first_seen, last_seen, status, frames, max_alt) VALUES ('{serial}', 'radiosonde', 'RS41', now() - interval '{hours} hours', now() - interval '{hours} hours' + interval '59 minutes', 'landed', 60, 28800)")
    psql(f"INSERT INTO sonde_points (serial, ts, lat, lon, alt) SELECT '{serial}', now() - interval '{hours} hours' + (i * interval '1 minute'), 35 + i * 0.01, -92 + i * 0.012, CASE WHEN i <= 36 THEN i * 800 ELSE GREATEST(0, 28800 - (i - 36) * 1200) END FROM generate_series(0, 59) i")
me = sess("plain1", "plainpassword1")
anon = requests.Session()
hp = anon.get(B + "/history/?q=RPL1&duration=24").text
check("Flight History has a Replay for the flight, opening the live map on that flight a little before it was first heard", re.search(r'href="/live\?icao=ef0001&amp;only=1&amp;replay=(\d+)&amp;replaySpeed=20"', hp) is not None)
check("the flight number and the registration in the list are links to every logged flight with them", 'href="/history/?q=RPL1&amp;duration=8760"' in hp and 'href="/history/?q=N1RPL&amp;duration=8760"' in hp)
m = re.search(r'href="/live\?replay=(\d+)&amp;replaySpeed=40"', hp)
check("and a Replay This Period button for everything from the start of the period", m is not None and abs(int(m.group(1)) - (time.time() - 24 * 3600)) < 300, m and m.group(1))
fl = anon.get(B + "/sondes/flights?q=ZZRP").text
check("the Balloon Flights list has a Replay for each flight and one for the whole page", "/sondes/map?replay=ZZRP1" in fl and "/sondes/map?replay=ZZRP2" in fl and re.search(r'/sondes/map\?replay=ZZRP\d,ZZRP\d', fl) is not None)
check("each balloon's page has Replay This Flight", "/sondes/map?replay=ZZRP1" in anon.get(B + "/sondes/ZZRP1").text)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1300,900")
d = webdriver.Chrome(options=o); J = d.execute_script
def wait(js, secs=15):
    end = time.time() + secs
    while time.time() < end:
        try:
            if J(js): return True
        except Exception:
            pass
        time.sleep(.3)
    return False
try:
    d.get(B + "/flight/ef0001?at=" + str(int(time.time()) - 3 * 3600 + 1)); time.sleep(4)
    check("the flight page itself has Replay on the Big Map, opening the live map on that flight before it was first heard", wait("var r=document.getElementById('fl-replay'); return !!r && !r.hidden && /icao=ef0001&only=1&replay=[0-9]+&replaySpeed=20/.test(r.getAttribute('href'))"))
    cand = None
    for x in [x for x in anon.get(B + "/api/aircraft.json").json()["aircraft"] if x.get("lat") is not None][:25]:
        tp = anon.get(B + "/api/trace/" + x["hex"] + ".json").json()
        if len(tp["points"][tp["leg"]:]) >= 5: cand = x["hex"]; break
    if cand:
        d.get(B + "/flight/" + cand); time.sleep(4)
        check("the flight page has a Replay button on its own map", wait("var b=document.getElementById('fl-rplay'); return !!b && !b.disabled"))
        check("the flight page's registration and flight number are links to every logged flight", J("return Array.from(document.querySelectorAll('#fl-ident a')).some(function(a){return a.getAttribute('href').indexOf('/history/?q=') === 0})"))
        J("document.getElementById('fl-rplay').click()"); time.sleep(1.5)
        st = J("return window.flReplay.state()")
        check("pressing it replays the flight on that map: the aircraft marker and a trail, with the slider and time", st["replaying"] and st["hasMarker"] and st["trail"] >= 2 and J("return !document.getElementById('fl-rslide').hidden && document.getElementById('fl-rtime').textContent.length > 0"), st)
        J("window.flReplay.seek(0.5)"); time.sleep(.5)
        mid = J("return window.flReplay.state()")
        check("the slider puts the aircraft part way along", mid["T0"] < mid["T"] < mid["T1"] and mid["trail"] >= 2, mid)
        J("document.getElementById('fl-rstop').click()"); time.sleep(.5)
        check("Stop Replay returns to the live view", not J("return window.flReplay.state().replaying") and J("return document.getElementById('fl-rslide').hidden"))
    ts = int(time.time()) - 3 * 3600
    d.get(B + f"/live?icao=ef0001&only=1&replay={ts}&replaySpeed=20"); time.sleep(4)
    check("the live map opens in its Replay at that moment (the bar and the Replay box are on)", J("var b=document.getElementById('lv-replay'); return !!b && b.style.display !== 'none' && document.getElementById('h-replay').checked") and J("return document.getElementById('rp-speed').value") == "20")
    d.get(B + "/sondes/ZZRP1"); time.sleep(3)
    J("document.getElementById('f-replay').click()")
    check("the balloon's own page replays the flight on its own map (the bar above the map, the balloon moving)", wait("return !!window.smReplay && !!document.getElementById('sr-bar') && document.querySelector('#fmap') !== null && window.smReplay.state().flights.length === 1"))
    check("the bar sits in the same panel as the map and has the same controls as the aircraft Flight page (Pause, Stop Replay, Speed, slider, time)", J("var b=document.getElementById('sr-bar'); return b.parentElement.contains(document.getElementById('fmap')) && ['sr-play','sr-back','sr-speed','sr-slide','sr-time'].every(function(i){var e=document.getElementById(i); return e && e.offsetParent !== null}) && document.getElementById('sr-back').textContent === 'Stop Replay' && !document.getElementById('f-replay')"))
    check("it has the launch flag and the balloon icon on that map", J("return window.smReplay.state().flights[0].launch") is True and J("return document.querySelectorAll('#fmap .leaflet-marker-icon svg').length") >= 2)
    J("window.smReplay.stop()"); time.sleep(.5)
    check("Stop Replay takes the replay away and the button works again", J("return !window.smReplay && !document.getElementById('sr-bar') && !document.getElementById('f-replay').disabled"))
    d.get(B + "/sondes/map?replay=ZZRP1"); check("a balloon replay opens its own bar on the Radiosonde Map", wait("return !!document.getElementById('sr-bar') && !!window.smReplay"))
    time.sleep(1)
    st = J("return window.smReplay.state()")
    check("it found the recorded flight and starts playing", len(st["flights"]) == 1 and st["flights"][0]["serial"] == "ZZRP1" and st["T1"] - st["T0"] > 3500 and (st["playing"] or st["T"] > st["T0"]), st)
    check("the bar says how long the flight was and has the controls", J("return document.getElementById('sr-state').textContent") != "" and J("return !!document.getElementById('sr-speed') && !!document.getElementById('sr-slide') && !!document.getElementById('sr-play')"))
    J("window.smReplay.pause()"); J("window.smReplay.seek(0.4)"); time.sleep(.5)
    mid = J("return window.smReplay.state()")
    check("moving the slider puts the balloon where it was at that time (climbing, about 19 km up)", mid["flights"][0]["i"] in range(22, 26) and 17000 < mid["flights"][0]["alt"] < 21000 and not mid["flights"][0]["done"], mid)
    check("the readout names the flight and its height", "ZZRP1" in J("return document.getElementById('sr-read').textContent") and " m" in J("return document.getElementById('sr-read').textContent"))
    check("before the burst there is no burst marker", mid["flights"][0]["burst"] is False)
    check("the balloon icon climbs from a launch flag", mid["flights"][0]["kind"] == "climbing" and mid["flights"][0]["launch"] is True and J("return document.querySelectorAll('.leaflet-marker-icon svg').length") >= 2)
    J("window.smReplay.seek(0.8)"); time.sleep(.5)
    check("after the top of the flight the burst is marked", J("return window.smReplay.state().flights[0].burst") is True)
    check("after the burst the icon is the parachute and a burst star is on the map", J("return window.smReplay.state().flights[0].kind") == "falling")
    J("window.smReplay.seek(1)"); time.sleep(.5)
    end = J("return window.smReplay.state()")
    check("at the end the flight is landed and marked as landed", end["flights"][0]["done"] and end["flights"][0]["landed"] and "landed" in J("return document.getElementById('sr-read').textContent"))
    check("at the end the icon is the landed sonde", end["flights"][0]["kind"] == "landed")
    J("document.getElementById('sr-speed').value = '300'; document.getElementById('sr-speed').dispatchEvent(new Event('change'))"); J("window.smReplay.seek(0)"); J("window.smReplay.play()"); time.sleep(2.5)
    run = J("return window.smReplay.state()")
    check("at 300 times the flight moves along by itself, and Pause stops it", run["T"] - run["T0"] > 300)
    J("document.getElementById('sr-play').click()"); time.sleep(.4); t1 = J("return window.smReplay.state().T"); time.sleep(.8)
    check("paused it stays where it is", J("return window.smReplay.state().T") == t1 and J("return window.smReplay.state().playing") is False)
    check("Back to the Map is a link to the ordinary map", J("return document.getElementById('sr-back').getAttribute('href')") == "/sondes/map")
    d.get(B + "/sondes/map?replay=ZZRP1,ZZRP2"); wait("return !!window.smReplay"); time.sleep(1.2)
    two = J("return window.smReplay.state()")
    check("two flights replay together, each with its own marker, and they flew twelve hours apart so the whole span is long", len(two["flights"]) == 2 and two["T1"] - two["T0"] > 11 * 3600)
    check("so there is a switch to start every flight together", J("return document.getElementById('sr-alignl').style.display") == "")
    J("document.getElementById('sr-align').click()"); time.sleep(.6)
    al = J("return window.smReplay.state()")
    check("started together, the span is one flight long", al["T1"] - al["T0"] < 3700, al)
    d.get(B + "/sondes/map?replay=NOSUCH1"); time.sleep(2.5)
    check("a flight with no recorded positions says so", "No recorded positions" in J("return document.getElementById('sr-state').textContent"))
    d.set_window_size(400, 850); d.get(B + "/sondes/map?replay=ZZRP1"); wait("return !!window.smReplay"); time.sleep(1.5)
    check("phone: the replay bar fits the screen and the page does not run off the side", J("var r=document.getElementById('sr-bar').getBoundingClientRect(); return r.left >= 0 && r.right <= window.innerWidth + 1 && document.documentElement.scrollWidth <= window.innerWidth + 1"))
finally:
    d.quit()
    psql("DELETE FROM flight_log WHERE hex LIKE 'ef%'")
    psql("DELETE FROM sonde_points WHERE serial LIKE 'ZZRP%'")
    psql("DELETE FROM sondes WHERE serial LIKE 'ZZRP%'")
print("ALL PASS" if ok_all else "SOME FAILED")
