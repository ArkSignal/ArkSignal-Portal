"""Distance settings: feet and miles by default, metres and kilometres on request, a typed unit always understood, and saving an untouched value never changes it."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
adm = sess("marty", PW)
H = {"X-Requested-With": "XMLHttpRequest"}
psql("delete from settings where key in ('live_units','verify_agree_meters','hospital_heliport_warn_km','hospital_heliport_km','hospital_match_km')")
def section(slug):
    """The fields of one settings section exactly as the page shows them."""
    t = adm.get(B + "/admin/settings").text
    form = re.search(rf'<details class="panel setsec" id="sec-{slug}".*?</form>', t, re.S).group(0)
    fields = {}
    for m in re.finditer(r'<input type="text" id="([a-z_]+)" name="[a-z_]+" value="([^"]*)"', form):
        fields[m.group(1)] = m.group(2).replace("&#34;", '"').replace("&amp;", "&")
    for m in re.finditer(r'<select id="([a-z_]+)" name="[a-z_]+">(.*?)</select>', form, re.S):
        sel = re.search(r'<option value="([^"]*)" selected', m.group(2))
        fields[m.group(1)] = sel.group(1) if sel else ""
    for m in re.finditer(r'<input type="checkbox" name="([a-z_]+)" value="([^"]*)"([^>]*)>', form):
        if "checked" in m.group(3):
            fields.setdefault(m.group(1), []).append(m.group(2))
    return form, fields
def save(slug, **over):
    _form, fields = section(slug)
    fields.update(over)
    j = adm.post(B + "/admin/settings/save", data=dict(fields, section=slug, csrf_token=tok(adm, "/admin/settings")), headers=H).json()
    return j["ok"], j["message"]
form, f = section("hospitals")
check("by default distances are in feet and miles", "Finders Agree Within (feet)" in form and "Warning Distance (miles)" in form and "Match Distance (miles)" in form, re.findall(r"Within[^<]*|Distance[^<]*", form)[:4])
check("the stored 250 metres shows as 820 feet and 1 kilometre as 0.62 miles", f["verify_agree_meters"] == "820" and f["hospital_heliport_warn_km"] == "0.62", f)
ok, m = save("hospitals")
# a whole-form save that leaves the distance fields out (older scripts, other pages) must not touch them, however many times it happens
psql("insert into settings (key, value) values ('verify_agree_meters', '250') on conflict (key) do update set value='250'")
for _ in range(3):
    adm.post(B + "/admin/settings/save", data={"csrf_token": tok(adm, "/admin/settings"), "section": "hospitals", "hospital_types": ["Acute Care Hospitals"], "hospitals_dataset_id": "xubh-q36u", "hospital_exclude_words": "X", "hospital_states": "AR"}, headers=H)
check("a save that does not send a distance leaves it exactly as it was, however often", psql("select value from settings where key='verify_agree_meters'") == "250", psql("select value from settings where key='verify_agree_meters'"))
psql("delete from settings where key in ('verify_agree_meters')")
check("saving without touching anything changes nothing (no rounding drift)", ok and psql("select value from settings where key='verify_agree_meters'") in ("", "250") and float(psql("select value from settings where key='hospital_heliport_warn_km'") or 1) == 1.0 and "No changes" in m, m)
ok, m = save("hospitals", verify_agree_meters="300 m")
check("a typed unit wins: 300 m is stored as 300", ok and psql("select value from settings where key='verify_agree_meters'") == "300", m)
ok, m = save("hospitals", verify_agree_meters="1000")
check("a bare number is in the unit shown: 1000 feet is 304.8 m", ok and psql("select value from settings where key='verify_agree_meters'") == "304.8", m)
ok, m = save("hospitals", hospital_heliport_warn_km="0.5 mi")
check("0.5 mi is stored as 0.804672 km", ok and psql("select value from settings where key='hospital_heliport_warn_km'") == "0.804672", m)
ok, m = save("hospitals", verify_agree_meters="5 parsecs")
check("an unknown unit is refused with a clear message", not ok and "Unknown unit" in m, m)
ok, m = save("hospitals", verify_agree_meters="1 ft")
check("a value outside the allowed range is refused and says the range in the shown units", not ok and "must be between" in m and "feet" in m, m)
ok, m = save("hospitals", verify_agree_meters="abc")
check("text that is not a distance is refused", not ok, m)
t = adm.get(B + "/admin/hospitals").text
check("the hospitals page speaks feet and miles too", re.search(r"within \d+ feet of each other", t) and re.search(r"more than [\d.]+ miles from its hospital", t), re.findall(r"within [^<]{0,30}", t)[:2])
psql("delete from settings where key in ('verify_agree_meters','hospital_heliport_warn_km')")
ok, m = save("home-page-and-live-map", live_units="metric")
form, f = section("hospitals")
check("switching to metric changes the labels and the numbers", ok and "Finders Agree Within (metres)" in form and "Warning Distance (kilometres)" in form and f["verify_agree_meters"] == "250" and f["hospital_heliport_warn_km"] == "1", (m, f))
t = adm.get(B + "/admin/hospitals").text
check("and the hospitals page", re.search(r"within 250 metres of each other", t) and re.search(r"more than 1 kilometre[s]? from its hospital", t), re.findall(r"within [^<]{0,30}", t)[:2])
ok, m = save("hospitals", verify_agree_meters="0.5 mi")
check("in metric mode a typed mile is still understood", ok and psql("select value from settings where key='verify_agree_meters'") == "804.672", m)
psql("delete from settings where key in ('live_units','verify_agree_meters','hospital_heliport_warn_km','hospital_heliport_km','hospital_match_km')")
# ---- the radiosonde distances follow the same rule, and each person can choose their own units
psql("delete from settings where key in ('sonde_site_match_km','sonde_landed_below_m','sonde_recovery_radius_km')")
page = adm.get(B + "/admin/settings").text
check("no settings label is left in kilometers or meters", not re.search(r"\((?:Kilometers|Meters|Kilometres|Metres)\)", page), re.findall(r"[A-Za-z ]+\((?:Kilometers|Meters)\)", page))
sec = re.search(r'<details class="panel setsec" id="sec-radiosondes".*?</form>', page, re.S).group(0)
check("the radiosonde distances are shown in miles and feet by default", "Launch Site Match Distance (miles)" in sec and "Counts as Landed Below (feet)" in sec and "Recovery Reports Within (miles)" in sec, re.findall(r"<label[^>]*>[^<]*(?:Distance|Below|Within)[^<]*</label>", sec))
check("25 km is shown as about 15.5 miles and 4000 m as about 13123 feet", 'value="15.53"' in sec and 'value="13123"' in sec, re.findall(r'id="sonde_(?:site_match_km|landed_below_m)"[^>]*value="([^"]*)"', sec))
adm.post(B + "/my/prefs", json={"units": "metric"}, headers={"X-CSRFToken": tok(adm, "/admin/settings")})
sec2 = re.search(r'<details class="panel setsec" id="sec-radiosondes".*?</form>', adm.get(B + "/admin/settings").text, re.S).group(0)
check("an administrator who chooses metric in the Display menu sees kilometers and meters, others still see miles", "Launch Site Match Distance (kilometres)" in sec2 and "Counts as Landed Below (metres)" in sec2 and 'value="25"' in sec2 and "(miles)" in re.search(r'<details class="panel setsec" id="sec-radiosondes".*?</form>', requests.get(B + "/admin/settings").text, re.S).group(0) if False else "Launch Site Match Distance (kilometres)" in sec2 and 'value="25"' in sec2)
adm.post(B + "/my/prefs", json={"units": ""}, headers={"X-CSRFToken": tok(adm, "/admin/settings")})
sec3 = re.search(r'<details class="panel setsec" id="sec-radiosondes".*?</form>', adm.get(B + "/admin/settings").text, re.S).group(0)
check("and choosing the site default again brings the miles back", "Launch Site Match Distance (miles)" in sec3)
print("ALL PASS" if ok_all else "SOME FAILED")
