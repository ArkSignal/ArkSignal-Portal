"""Every map is the same engine: asking for the drive times of a place folds Layers and Filters away, like picking an item does."""
import os, sys, time
try:
    from selenium import webdriver
except ImportError:
    print("SKIP: selenium not installed"); sys.exit(0)
HOST = os.environ.get("TEST_HOST", "test-box.invalid"); B = os.environ.get("MOBILE_BASE", f"http://{HOST}:18430")
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
d = webdriver.Chrome(options=o)
try:
    for page in ("/counties/", "/live", "/weather/map", "/sondes/map", "/aprs/map", "/repeaters/map"):
        d.get(B + page); time.sleep(4)
        d.execute_script("var b=document.getElementById('lv-toolsbody'); if (b.style.display === 'none') document.getElementById('lv-filtbtn').click()"); time.sleep(.4)
        open0 = d.execute_script("return document.getElementById('lv-toolsbody').style.display") != "none"
        d.execute_script("ArkDrive.at(34.75, -92.29, 'Test place')"); time.sleep(2.5)
        shown = d.execute_script("return getComputedStyle(document.getElementById('lv-drive')).display") != "none"
        folded = d.execute_script("return document.getElementById('lv-toolsbody').style.display") == "none"
        check(f"{page}: drive times open and Layers and Filters fold away", open0 and shown and folded, (open0, shown, folded))
    # a popup is an item's details too (the Coverage Map shows its stations in popups)
    d.get(B + "/coverage"); time.sleep(5)
    d.execute_script("var b=document.getElementById('lv-toolsbody'); if (b.style.display === 'none') document.getElementById('lv-filtbtn').click()"); time.sleep(.4)
    d.execute_script("var m=window.arkMap; L.popup().setLatLng(m.getCenter()).setContent('x').openOn(m)"); time.sleep(.8)
    check("/coverage: opening a popup folds Layers and Filters", d.execute_script("return document.getElementById('lv-toolsbody').style.display") == "none")
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
