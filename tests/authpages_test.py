"""Sign-in, register and forgot-password hold very little: a small column centred on the page, not the width of the window."""
import sys, time
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
try:
    from selenium import webdriver
except ImportError:
    print("SKIP: selenium not installed"); sys.exit(0)
bad = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n, "" if c else x)
    if not c: bad.append(n)
for w, h, label in ((1700, 1000, "wide"), (3400, 1000, "ultra-wide"), (390, 800, "phone")):
    o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument(f"--window-size={w},{h}")
    d = webdriver.Chrome(options=o)
    try:
        for path, cap in (("/my/login", 480), ("/my/register", 480), ("/my/forgot", 620)):
            d.get(B + path); time.sleep(1)
            g = d.execute_script("""var a=document.querySelector('.authpage'); if(!a) return null; var r=a.getBoundingClientRect(), vw=document.documentElement.clientWidth, f=a.querySelector('form, details'); var fr=f.getBoundingClientRect();
                return {w: Math.round(r.width), left: Math.round(r.left), right: Math.round(vw - r.right), fw: Math.round(fr.width), vw: vw, over: document.documentElement.scrollWidth - vw}""")
            if g is None:
                check(f"{label} {path}: the page has the small centred column", False, "no .authpage"); continue
            if w >= 700:
                check(f"{label} {path}: a small column (at most {cap} px) centred on the page", g["w"] <= cap and abs(g["left"] - g["right"]) <= 3 and g["fw"] <= cap, g)
            else:
                check(f"{label} {path}: it uses the phone's width and nothing scrolls sideways", g["w"] >= g["vw"] - 60 and g["over"] <= 1, g)
    finally:
        d.quit()
print("ALL PASS" if not bad else "SOME FAILED")
