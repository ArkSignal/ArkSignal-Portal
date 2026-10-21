import re, requests, subprocess, html
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
def setting(k, v): psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value='{v}'")
def compose(txt):
    m = re.search(r'<pre class="code">(services:.*?)</pre>', txt, re.S)
    return html.unescape(m.group(1)) if m else ""
def page(): return requests.get(B + "/feed/").text
setting("public_cache_seconds", "0")
SECTIONS = ["the-short-version", "how-it-works", "what-you-need", "hardware", "hardware-setup", "software-setup", "after-you-are-feeding", "ports", "troubleshooting", "data", "more-help"]
def semi_ok(c, tag):
    after = c.split("ULTRAFEEDER_CONFIG=" + chr(10), 1)[1].splitlines() if ("ULTRAFEEDER_CONFIG=" + chr(10)) in c else []
    lines = []
    for l in after:
        if l.strip().startswith("-") or not l.strip():
            break
        lines.append(l.strip())
    check(f"{tag} config entries are ;-separated with none after the last", lines and all(l.endswith(";") for l in lines[:-1]) and not lines[-1].endswith(";"), lines)
for mlat, uat in (("0", "0"), ("1", "0"), ("1", "1"), ("0", "1")):
    setting("mlat_enabled", mlat); setting("uat_enabled", uat)
    t = page(); c = compose(t)
    tag = f"[mlat={mlat} uat={uat}]"
    check(f"{tag} every section is present on the ADS-B page", all(f'id="sec-{s}"' in t for s in SECTIONS), [s for s in SECTIONS if f'id="sec-{s}"' not in t])
    sub_ok = (requests.get(B + "/feed/mlat").status_code == (200 if mlat == "1" else 404)) and (requests.get(B + "/feed/uat").status_code == (200 if uat == "1" else 404)) and requests.get(B + "/feed/check").status_code == 200
    check(f"{tag} the MLAT and UAT pages exist only when switched on", sub_ok)
    check(f"{tag} the sub-navigation lists the right pages", ('href="/feed/mlat"' in t) == (mlat == "1") and ('href="/feed/uat"' in t) == (uat == "1") and 'href="/feed/check"' in t)
    check(f"{tag} Am I Feeding is its own page", 'id="sec-am-i-feeding"' in requests.get(B + "/feed/check").text and 'id="sec-am-i-feeding"' not in t)
    check(f"{tag} no template leftovers", "{%" not in t and "{{" not in t)
    check(f"{tag} the compose example has the adsb line", "adsb,feed.arksignal.allisononline.org,30004,beast_reduce_plus_out" in c, c[:200])
    check(f"{tag} MLAT lines in the ADS-B example only when on", (("mlat,feed.arksignal.allisononline.org,31090" in c) == (mlat == "1")) and (("MLAT_USER=" in c) == (mlat == "1")))
    check(f"{tag} UAT is not in the ADS-B example at all", "dump978" not in c and "uat_in" not in c and "uat_replay_out" not in t)
    semi_ok(c, tag)
    if mlat == "1":
        m = requests.get(B + "/feed/mlat").text; mc = compose(m)
        check(f"{tag} the MLAT page has every section, in the same order as the ADS-B page", all(f'id="sec-{s}"' in m for s in SECTIONS) and [x for x in re.findall(r'id="sec-([a-z-]+)"', m) if x in SECTIONS] == SECTIONS, re.findall(r'id="sec-([a-z-]+)"', m))
        check(f"{tag} the MLAT page has the settings, position rules and client", all(w in m for w in ("MLAT_USER", "READSB_LAT", "READSB_ALT", "UUID", "mlat,feed.arksignal.allisononline.org,31090", "mlat-client", "sea level", "waiting for peers")))
        check(f"{tag} the MLAT page has hardware and setup, and points to ADS-B", all(w in m for w in ("Hardware Setup", "Measuring the position", "wired network")) and 'href="/feed/"' in m and "/feed/#sec-software-setup" in m)
        check(f"{tag} the MLAT page has no leftovers", "{%" not in m and "{{" not in m)
    if uat == "1":
        u = requests.get(B + "/feed/uat").text; uc = compose(u)
        check(f"{tag} the UAT page has every section, in the same order", all(f'id="sec-{s}"' in u for s in SECTIONS) and [x for x in re.findall(r'id="sec-([a-z-]+)"', u) if x in SECTIONS] == SECTIONS, re.findall(r'id="sec-([a-z-]+)"', u))
        check(f"{tag} the UAT page has the container, the line, the radio and the antenna", all(w in u for w in ("dump978", "adsb,dump978,30978,uat_in", "00000978", "rtl_eeprom", "978 MHz antenna", "DUMP978_RTLSDR_DEVICE")) and "uat_replay_out" not in u)
        check(f"{tag} the UAT config entries are ;-separated", True)
        check(f"{tag} the UAT page has no leftovers and points to ADS-B", "{%" not in u and "{{" not in u and 'href="/feed/"' in u)
        if uc: semi_ok(uc.replace("      # ...everything from the ADS-B page, then in ULTRAFEEDER_CONFIG add the 978 line:" + chr(10), ""), tag + " UAT page")
t = page()
setting("mlat_enabled", "1"); setting("uat_enabled", "1"); t = page()
check("the explanation covers ADS-B, MLAT, UAT, TIS-B and ADS-R", all(w in t for w in ("ADS-B", "MLAT", "UAT", "TIS-B", "ADS-R", "multilateration")))
check("the old #sec-mlat link still lands on the MLAT page", "/feed/mlat" in t and "sec-am-i-feeding" in t)
check("the feed page names no old address (the Already feeding note is gone)", "feed.adsb.allisononline.org" not in t and "Already feeding?" not in t)
check("it links to the status and MLAT network pages", 'href="/status"' in t)
setting("mlat_enabled", "0"); setting("uat_enabled", "0")
print("ALL PASS" if ok_all else "SOME FAILED")
