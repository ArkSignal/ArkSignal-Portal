import re, requests, subprocess
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
adm = sess("marty", PW)
psql("DELETE FROM settings WHERE key LIKE 'support_%'")
def save(**kw):
    data = dict(enabled="1", kofi="", paypal="", other="", other_label="", intro="Hello.\n\nSecond paragraph.", costs="", equipment="", raised="0", supporters="", thanks="Thanks")
    data.update(kw); data["csrf_token"] = tok(adm, "/admin/support")
    j = adm.post(B + "/admin/support/save", data=data, headers={"X-Requested-With": "XMLHttpRequest"}).json(); return j["ok"], j["message"]
check("the page is hidden until it is set up", requests.get(B + "/support").status_code == 404 and 'href="/support"' not in requests.get(B + "/").text)
ok, msg = save()
check("switching it on without a link is refused", not ok and "donation link" in msg, msg)
ok, msg = save(kofi="http://ko-fi.com/me")
check("a link that is not https is refused", not ok and "https" in msg, msg)
ok, msg = save(kofi="https://example.com/me")
check("a Ko-fi link on another site is refused", not ok and "ko-fi.com" in msg, msg)
ok, msg = save(kofi="https://ko-fi.com/me", costs="Cloudflare email | abc")
check("a cost with a bad amount is refused", not ok and "not an amount" in msg, msg)
ok, msg = save(kofi="https://ko-fi.com/me", equipment="Antenna | 85 | 10 | wrongstatus")
check("equipment with a bad status is refused", not ok and "Status must be" in msg, msg)
ok, msg = save(kofi="https://ko-fi.com/me", paypal="https://paypal.me/me", costs="Cloudflare email | 5\nClaude Pro plan | 20\nExtra credits | 25", raised="12.50",
               equipment="978 MHz receiver kit | 85 | 40 | needed | For the Conway site\nHilltop mesh node | 120 | 120 | funded", supporters="K5ABC\nW5XYZ")
check("a complete setup saves and goes live", ok and "/support" in msg, msg)
p = requests.get(B + "/support")
t = p.text
check("the public page shows the words, both buttons and no payment form", p.status_code == 200 and "Support on Ko-fi" in t and "Donate with PayPal" in t and "<input" not in t.replace('<input type="checkbox" id="pf-deco">', "") and "Second paragraph." in t)
check("it totals the monthly cost and shows progress", "$50.00/month" in t and "$12.50 received this month" in t and 'style="width:25%"' in t, re.findall(r"width:\d+%", t)[:3])
check("the equipment fund lists items with their state", "978 MHz receiver kit" in t and "$40 funded" in t and "Funded" in t and "For the Conway site" in t)
check("supporters are listed", "K5ABC" in t and "W5XYZ" in t)
check("links open in a new tab safely", 'target="_blank" rel="noopener noreferrer"' in t)
check("the footer now links to it", 'href="/support"' in requests.get(B + "/").text)
check("the admin page needs sign-in", requests.get(B + "/admin/support", allow_redirects=False).status_code in (302, 401, 403, 404))
plain = sess("plain1", "plainpassword1")
check("an ordinary user cannot change it", plain.post(B + "/admin/support/save", data={}, allow_redirects=False).status_code in (302, 400, 401, 403, 404))
psql("DELETE FROM settings WHERE key LIKE 'support_%'")
check("switched off again, the page is gone", requests.get(B + "/support").status_code == 404)
print("ALL PASS" if ok_all else "SOME FAILED")
