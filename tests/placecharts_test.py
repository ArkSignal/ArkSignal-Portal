"""Charts are two columns on a wide screen and one on a phone (place page, station page, flight page); the place page has Use My Location; feeder icons on the Feeders page are big airplane badges."""
import os, sys, time, requests
HOST = os.environ.get("TEST_HOST", "test-box.invalid"); B = os.environ.get("MOBILE_BASE", f"http://{HOST}:18430")
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
t = requests.get(B + "/weather/place").text
check("the place page has a Use My Location button", 'id="pl-here"' in t)
check("the place page carries the two column chart rule", "repeat(2,minmax(0,1fr))" in requests.get(B + "/weather/place?q=72201").text)
try:
    from selenium import webdriver
except ImportError:
    print("SKIP the browser part"); print("ALL PASS" if ok_all else "SOME FAILED"); sys.exit(0)
for w, cols in ((1300, 2), (400, 1)):
    o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument(f"--window-size={w},900")
    d = webdriver.Chrome(options=o)
    try:
        d.get(B + "/weather/place?q=Little%20Rock%20AR"); time.sleep(6)
        n = d.execute_script("var g=document.getElementById('pl-charts'); if(!g) return -1; return getComputedStyle(g).gridTemplateColumns.split(' ').length")
        check(f"place page charts are {cols} column(s) at {w} px wide", n == cols, n)
    finally:
        d.quit()
f = requests.get(B + "/feeders/").text
check("the Feeders page draws each feeder as an airplane badge, not a small dot", 'SondeIcons.marker("feeder"' in f and "circleMarker" not in f.split("var pts")[1])
print("ALL PASS" if ok_all else "SOME FAILED")
