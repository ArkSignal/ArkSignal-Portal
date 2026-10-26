"""Coverage Map: each layer can be limited to one or more areas (counties by their real outline, cities and ZIP codes by a radius); the choice shows on the layer, is remembered, and the point test is right."""
import os, sys, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
cv = requests.get(B + "/coverage").text
check("the Coverage Map is the shared map page with the area filter", all(x in cv for x in ('id="lvmap"', "/static/areafilter.js", "Limit to an area", "Coverage Layers")))
js = requests.get(B + "/static/areafilter.js").text
check("the area filter knows counties (outlines), cities and ZIP codes (radius) and remembers the choice", all(x in js for x in ("inGeom", "/api/counties/shapes.json", "/api/places/search.json", "localStorage.setItem(\"lv.area.\"")))
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
except ImportError:
    print("SKIP the browser part: selenium is not installed"); print("ALL PASS" if ok_all else "SOME FAILED"); sys.exit(0)
o = webdriver.ChromeOptions(); o.add_argument("--headless=new"); o.add_argument("--window-size=1400,900")
d = webdriver.Chrome(options=o)
try:
    d.get(B + "/coverage"); d.execute_script("try{localStorage.clear()}catch(e){}"); d.get(B + "/coverage"); time.sleep(4)
    n = d.execute_script("return document.querySelectorAll('details.areaf-d').length")
    check("every coverage layer has a Limit to an area control", n >= 3, n)
    sq = d.execute_script("""var sq={type:'Polygon',coordinates:[[[-93,34],[-92,34],[-92,35],[-93,35],[-93,34]]]}; var ring={type:'Polygon',coordinates:[[[-93,34],[-92,34],[-92,35],[-93,35],[-93,34]],[[-92.6,34.4],[-92.4,34.4],[-92.4,34.6],[-92.6,34.6],[-92.6,34.4]]]};
      return [ArkArea.inGeom(34.5,-92.5,sq), ArkArea.inGeom(36,-92.5,sq), ArkArea.inGeom(34.5,-92.5,ring), ArkArea.inGeom(34.1,-92.9,ring)]""")
    check("the point-in-outline test is right, holes included", sq == [True, False, False, True], sq)
    d.execute_script("var x=document.querySelector('details.areaf-d'); for (var e=x; e; e=e.parentElement) { if (e.tagName==='DETAILS') e.open=true; if (e.hidden) e.hidden=false; }")   # the control sits inside the map's folded panels
    inp = d.find_element(By.CSS_SELECTOR, "details.areaf-d .areaf-q")
    d.execute_script("arguments[0].scrollIntoView({block:'center'})", inp); time.sleep(.5)
    try:
        inp.send_keys("Conway")
    except Exception:   # the panel is folded away on this window size: type through the page instead
        d.execute_script("var i=arguments[0]; i.focus(); i.value='Conway'; i.dispatchEvent(new Event('input', {bubbles: true}))", inp)
    time.sleep(1.5)
    picks = d.find_elements(By.CSS_SELECTOR, "details.areaf-d .areaf-pick")
    check("typing a place lists matches", len(picks) >= 1, len(picks))
    cty = [p for p in picks if "County" in p.text]
    if cty:
        d.execute_script("arguments[0].click()", cty[0]); time.sleep(2)
        chip = d.execute_script("return document.querySelector('details.areaf-d .areaf-chips').textContent")
        check("picking a county adds it as a chip and says so on the layer", "Conway County" in chip and d.execute_script("return document.querySelector('details.areaf-d summary').textContent").startswith("Limited to"), chip)
        d.get(B + "/coverage"); time.sleep(3)
        check("the choice is remembered", "Conway County" in d.execute_script("return document.querySelector('details.areaf-d .areaf-chips').textContent"))
        d.execute_script("document.querySelector('details.areaf-d .areaf-chips .chip').click()"); time.sleep(.5)
        check("the chip's cross removes it (Everywhere again)", "Everywhere" in d.execute_script("return document.querySelector('details.areaf-d .areaf-chips').textContent"))
finally:
    d.quit()
print("ALL PASS" if ok_all else "SOME FAILED")
