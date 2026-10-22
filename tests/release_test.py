import re, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
adm = sess("marty", PW); anon = requests.Session()
# public About page
pub = anon.get(B + "/about"); ph = pub.text
check("About is public (no sign-in) and about the project", pub.status_code == 200 and "The Network" in ph and "Your Privacy" in ph and "Built With Open Source" in ph)
check("About credits the open-source projects and their licenses", all(x in ph for x in ("Leaflet", "OpenStreetMap", "OurAirports", "Grafana", "GPL", "ODbL")))
check("About shows no version, branch, commit or update information", not any(x in ph for x in (">Branch<", ">Commit<", "Check Now", "Update Now", "newer release")))
check("the footer links About for visitors", 'href="/about"' in anon.get(B + "/").text)
check("the old signed-in About address leads to the public page", anon.get(B + "/my/about", allow_redirects=False).status_code == 301 and adm.get(B + "/my/about", allow_redirects=False).headers["Location"].endswith("/about"))
# admin Updates page
ab = adm.get(B + "/admin/updates").text
check("Admin > Updates shows the version, branch and a release check", "Version" in ab and "Branch" in ab and "Release Check" in ab)
check("administrators get a Check Now button", "/admin/updates/check" in ab and "Check Now" in ab)
check("Update Now is shown (disabled with the reason when the update service is not set up)", "Update Now" in ab)
check("the Updates tab is in the admin bar", 'href="/admin/updates"' in adm.get(B + "/admin/").text)
tk = re.search(r'name="csrf_token" value="([^"]+)"', ab).group(1)
r = adm.post(B + "/admin/updates/check", data={"csrf_token": tk}, allow_redirects=False)
check("Check Now runs the check and returns to Updates", r.status_code == 302 and r.headers["Location"].endswith("/admin/updates"), r.status_code)
ab2 = adm.get(B + "/admin/updates").text
check("the result is saved and shown (this local build is not a numbered release)", "Last checked" in ab2 and "not made as a numbered release" in ab2, re.findall(r"Release Check.{0,400}", ab2, re.S)[:1])
r = adm.post(B + "/admin/updates/now", data={"csrf_token": tk}, headers={"X-Requested-With": "XMLHttpRequest"})
check("Update Now says plainly when the update service is not set up", r.status_code == 400 and "not set up" in r.json()["message"], r.text[:120])
check("the Admin overview loads", adm.get(B + "/admin/").status_code == 200)
st = adm.get(B + "/admin/settings").text
check("update check settings exist", "Check for a New Portal Release Every" in st and "Image Repository to Check" in st)
check("the About page introduction is a setting", "About Page Introduction" in st)
check("a visitor cannot run the check", anon.post(B + "/admin/updates/check").status_code in (302, 400, 401, 403))
check("a visitor cannot open Updates", anon.get(B + "/admin/updates", allow_redirects=False).status_code in (302, 401, 403))
print("ALL PASS" if ok_all else "SOME FAILED")
