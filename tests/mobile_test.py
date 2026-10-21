"""Phone-width check: every page loaded at 375x812 in headless Chrome must fit the width (no sideways scroll),
and the menu drawer must open. Needs selenium and Chrome on the PC. Screenshots go to MOBILE_SHOTS (default tests/shots).
Set MOBILE_BASE to check another instance (for example the persistent portal on :18431)."""
import os, re, sys, time
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
except ImportError:
    print("SKIP mobile_test: selenium not installed"); sys.exit(0)
B = os.environ.get("MOBILE_BASE", f"http://{os.environ.get('TEST_HOST', 'test-box.invalid')}:18430")
PW = re.search(r"password: (\S+)", open(os.path.join(os.path.dirname(__file__), "..", "TEST_CREDENTIALS.md")).read()).group(1)
SHOTS = os.environ.get("MOBILE_SHOTS", os.path.join(os.path.dirname(__file__), "shots"))
os.makedirs(SHOTS, exist_ok=True)
PAGES = ["/", "/live", "/flight/", "/history/", "/feeders/", "/feed/", "/feed/check", "/airports/", "/airports/KLIT", "/my/login", "/my/register", "/contact",
         "/my/", "/my/password", "/my/sessions", "/my/two-factor", "/my/profile", "/my/alerts", "/about",
         "/admin/", "/admin/users", "/admin/roles", "/admin/feeders", "/admin/audit", "/admin/signins", "/admin/settings", "/admin/smtp",
         "/admin/airports", "/admin/backups", "/admin/updates", "/admin/tools", "/admin/layers", "/admin/contacts", "/view/statistics"]
o = webdriver.ChromeOptions()
o.add_argument("--headless=new"); o.add_argument("--hide-scrollbars")
o.add_experimental_option("mobileEmulation", {"deviceMetrics": {"width": 375, "height": 812, "pixelRatio": 2}, "userAgent":
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"})
d = webdriver.Chrome(options=o)
bad = []; ok = 0
def check(name, cond, detail=""):
    global ok
    if cond: ok += 1
    else: bad.append(f"{name} {detail}")
    print(("PASS " if cond else "FAIL ") + name, detail if not cond else "")
OVER = """var w=document.documentElement.clientWidth,out=[];
document.querySelectorAll('body *').forEach(function(e){var r=e.getBoundingClientRect();
 if(r.width>0&&r.right>w+1&&getComputedStyle(e).position!=='fixed'){var p=e.parentElement,clipped=false;
  while(p&&p!==document.body){var s=getComputedStyle(p);if(/(auto|scroll|hidden)/.test(s.overflowX)){clipped=true;break;}p=p.parentElement;}
  if(!clipped)out.push(e.tagName+(e.id?'#'+e.id:'')+(e.className&&e.className.baseVal===undefined?'.'+String(e.className).split(' ')[0]:'')+' '+Math.round(r.right));}});
return {sw:document.documentElement.scrollWidth,w:w,out:out.slice(0,4)};"""
try:
    d.get(B + "/my/login")
    d.find_element(By.NAME, "username").send_keys("marty"); d.find_element(By.NAME, "password").send_keys(PW)
    d.find_element(By.CSS_SELECTOR, "form button[type=submit], form .btn").click(); time.sleep(1.5)
    for p in PAGES:
        d.get(B + p); time.sleep(1.2 if p in ("/live", "/") else 0.5)
        r = d.execute_script(OVER)
        check(f"{p} fits 375px", r["sw"] <= r["w"] + 1 and not r["out"], f"scrollWidth {r['sw']} vs {r['w']}: {r['out']}")
        d.save_screenshot(os.path.join(SHOTS, p.strip("/").replace("/", "_") or "home") + ".png")
    # live map on a phone: filters folded, list hidden, selecting an aircraft opens the bottom card
    d.get(B + "/live"); time.sleep(3)
    check("live map starts with the filters folded", not d.find_element(By.ID, "lv-toolsbody").is_displayed())
    check("live map starts with the list hidden", not d.find_element(By.ID, "lv-list").is_displayed())
    d.find_element(By.ID, "lv-hide").click(); time.sleep(0.5)
    check("the List button opens the aircraft list", d.find_element(By.ID, "lv-list").is_displayed())
    d.save_screenshot(os.path.join(SHOTS, "live_list.png"))
    rows = d.find_elements(By.CSS_SELECTOR, "#lv-tbl tr.it")
    if rows:
        d.execute_script("document.querySelector('#lv-tbl tr.it').click()"); time.sleep(2)   # the list redraws every few seconds: click the fresh row
        check("selecting an aircraft shows the card and hides the list", d.find_element(By.ID, "lv-card").is_displayed() and not d.find_element(By.ID, "lv-list").is_displayed())
        r = d.execute_script("var c=document.getElementById('lv-card').getBoundingClientRect(),m=document.getElementById('lv').getBoundingClientRect();return [c.bottom<=m.bottom+1,c.height<m.height*0.6]")
        check("the card sits at the bottom and leaves the map visible", all(r), str(r))
        d.save_screenshot(os.path.join(SHOTS, "live_card.png"))
    else:
        print("note: no aircraft right now, card check skipped")
    d.get(B + "/feeders/")
    d.find_element(By.ID, "nav-toggle").click(); time.sleep(0.3)
    check("menu drawer opens and lists the links", d.find_element(By.CSS_SELECTOR, "nav.open a.mapbtn").is_displayed())
    d.save_screenshot(os.path.join(SHOTS, "menu_open.png"))
finally:
    d.quit()
print("ALL PASS" if not bad else "SOME FAILED: %d" % len(bad))
sys.exit(0 if not bad else 1)
