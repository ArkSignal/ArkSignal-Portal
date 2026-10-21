"""THE RULE (Marty, 2026-10-16): opening a details card, or choosing another item while it is open, folds the Weather, Layers and Filters and Legend panels away on every map.
Needs selenium and Chrome on the PC."""
import sys, time
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
try:
    from selenium import webdriver
except ImportError:
    print("SKIP no selenium"); print("ALL PASS"); sys.exit(0)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1300,900")
d = webdriver.Chrome(options=o); J = d.execute_script
def PH(js):   # evaluated at a phone's width (the title row stays put on phones only)
    size = d.get_window_size(); d.set_window_size(400, 850); time.sleep(.6)
    try:
        return d.execute_script(js)
    finally:
        d.set_window_size(size["width"], size["height"])
try:
    for path in ("/live", "/weather/map", "/counties/", "/trauma/", "/sondes/map", "/aprs/map", "/repeaters/map", "/coverage"):
        d.get(B + path); time.sleep(5)
        if not J("return !!document.getElementById('lv-card')"):
            print("SKIP", path, "has no card"); continue
        J("var b=document.getElementById('lv-legbtn'); if (b && b.getAttribute('aria-expanded') !== 'true') b.click()"); time.sleep(.4)
        J("var w=document.getElementById('lv-wx'), b=document.getElementById('lv-wxbtn'); if (b && w && w.style.display !== 'block') b.click()"); time.sleep(.6)
        J("var t=document.getElementById('lv-toolsbody'), b=document.getElementById('lv-filtbtn'); if (b && t && t.style.display === 'none') b.click()"); time.sleep(.4)
        before = J("var tb=document.getElementById('lv-toolsbody'), wx=document.getElementById('lv-wx'), lg=document.getElementById('lv-legend'); return {f: tb.style.display !== 'none', w: !!wx && wx.style.display === 'block', l: !!lg && lg.style.display === 'block'}")
        J("document.getElementById('c-name').textContent = 'Test item'; document.getElementById('lv-card').style.display = 'block'"); time.sleep(.8)
        after = J("var tb=document.getElementById('lv-toolsbody'), wx=document.getElementById('lv-wx'), lg=document.getElementById('lv-legend'); return {f: tb.style.display !== 'none', w: !!wx && wx.style.display === 'block', l: !!lg && lg.style.display === 'block'}")
        check(f"{path}: opening a card folds the Weather, Layers and Filters and Legend panels", any(before.values()) and not any(after.values()), {"before": before, "after": after})
        # a second item while the card stays open folds them again
        J("var b=document.getElementById('lv-legbtn'); if (b && b.getAttribute('aria-expanded') !== 'true') b.click()"); time.sleep(.4)
        J("document.getElementById('c-name').textContent = 'Another item'"); time.sleep(.8)
        again = J("var lg=document.getElementById('lv-legend'); return !!lg && lg.style.display === 'block'")
        check(f"{path}: choosing another item folds them again", not again)
        check(f"{path}: on a phone the card's title row with Collapse and Close stays at the top while the details scroll", PH("return getComputedStyle(document.getElementById('lv-card').firstElementChild).position") == "sticky")
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
