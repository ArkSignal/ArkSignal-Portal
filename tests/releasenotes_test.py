import json, re, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
data = json.load(open(__import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "..", *r"app\release_notes.json".split("\\")), encoding="utf-8"))
vers = [e["version"] for e in data]
check("the notes file starts with the 'next' entry and has no duplicate versions", vers[0] == "next" and len(set(vers)) == len(vers), vers[:4])
nums = [tuple(int(x) for x in v.split(".")) for v in vers[1:]]
check("released versions are newest first", nums == sorted(nums, reverse=True), vers)
check("every entry says something in at least one category", all(any(e.get(k) for k in ("added", "improved", "fixed", "security")) for e in data))
check("every released entry has a date", all(e.get("released") for e in data if e["version"] != "next"))
check("1.3.0, 1.2.0 and 1.1.0 are there", "1.3.0" in vers and "1.2.0" in vers and "1.1.0" in vers, vers[:5])
check("1.0.0 through 1.0.33 are all there (1.0.15 and 1.0.17 were never published)", set(vers) >= {"1.0.%d" % i for i in range(0, 34)} - {"1.0.15", "1.0.17"}, vers)
pg = requests.get(B + "/release-notes")
check("the release notes page is public", pg.status_code == 200 and "Release" in pg.text)
check("it lists the versions and the coming-next section", "Coming Next" in pg.text and 'id="v1.0.23"' in pg.text and 'id="v1.0.0"' in pg.text)
check("it shows categories", "Added" in pg.text and "Fixed" in pg.text and "Security" in pg.text)
check("it shows dates", re.search(r"\d{4}-\d\d-\d\d", pg.text) is not None)
api = requests.get(B + "/api/release-notes.json").json()
check("the JSON has the same releases", len(api["releases"]) == len(data) and api["releases"][0]["version"] == "next", api["running"])
check("the footer links to it", 'href="/release-notes"' in requests.get(B + "/").text)
print("ALL PASS" if ok_all else "SOME FAILED")
