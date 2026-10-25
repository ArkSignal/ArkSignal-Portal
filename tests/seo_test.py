"""Being found on Google: robots.txt, sitemap.xml, the description, canonical address and social-card tags on public pages, noindex on private ones (a meta tag and a header), the search-engine switch, and the Search Console and Bing
verification codes (home page only, escaped). Needs the test box."""
import re, subprocess, requests
import xml.etree.ElementTree as ET
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
def setting(k, v):
    psql(f"INSERT INTO settings (key, value) VALUES ('{k}','{v}') ON CONFLICT (key) DO UPDATE SET value = '{v}'")
psql("DELETE FROM settings WHERE key LIKE 'seo_%' OR key = 'main_address'")
anon = requests.Session()
rb = anon.get(B + "/robots.txt")
check("robots.txt is served as plain text, keeps the private areas out and names the sitemap", rb.status_code == 200 and rb.headers["Content-Type"].startswith("text/plain") and all(x in rb.text for x in ("User-agent: *", "Disallow: /admin/", "Disallow: /my/", "Disallow: /chat/", "Disallow: /api/")) and re.search(r"Sitemap: https?://[^/]+/sitemap.xml", rb.text) and "Disallow: /\n" not in rb.text, rb.text)
sm = anon.get(B + "/sitemap.xml")
root = ET.fromstring(sm.content)
locs = [e.text for e in root.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
paths = [re.sub(r"^https?://[^/]+", "", l) for l in locs]
check("the sitemap is valid XML listing the public pages: home, the menu's pages, About, Release Notes and Roadmap", sm.status_code == 200 and "xml" in sm.headers["Content-Type"] and {"/", "/about", "/roadmap", "/release-notes", "/aircraft"} <= set(paths) and len(paths) == len(set(paths)), paths[:8])
check("and none of the private areas", not any(p.startswith(("/admin", "/my", "/chat", "/login", "/members", "/api")) for p in paths), [p for p in paths if p.startswith(("/admin", "/my", "/chat"))])
home = anon.get(B + "/").text
check("the home page has a description, a canonical address and the social-card tags", all(x in home for x in ('<meta name="description" content="', '<link rel="canonical" href="', 'property="og:title"', 'property="og:description"', 'property="og:url"', 'name="twitter:card"')) and 'name="robots"' not in home.split("</head>")[0])
check("a public page carries no noindex header", "X-Robots-Tag" not in anon.get(B + "/about").headers)
lg = anon.get(B + "/my/login")
check("private pages say noindex in the page and in a header", 'name="robots" content="noindex, nofollow"' in lg.text and lg.headers.get("X-Robots-Tag") == "noindex, nofollow" and anon.get(B + "/admin/", allow_redirects=False).headers.get("X-Robots-Tag") == "noindex, nofollow")
about = anon.get(B + "/about").text
check("each page's own address is its canonical one", 'rel="canonical" href="' in about and about.split('rel="canonical" href="')[1].split('"')[0].endswith("/about"))
setting("seo_google_verification", "abcDEF123_-xyz<script>alert(1)</script>")
setting("seo_bing_verification", "BING1234567890ABCD")
h2 = anon.get(B + "/").text
check("the Search Console and Bing codes are put on the home page, escaped, and nowhere else", 'name="google-site-verification" content="abcDEF123_-xyz&lt;script&gt;' in h2 and 'name="msvalidate.01" content="BING1234567890ABCD"' in h2 and "<script>alert(1)" not in h2 and "google-site-verification" not in anon.get(B + "/about").text)
setting("seo_description", "A custom description for search results.")
check("the description is a setting", 'content="A custom description for search results."' in anon.get(B + "/").text)
adm = sess("marty", PW)
st = adm.get(B + "/admin/settings").text
check("the settings are in Settings > Public Pages", all(x in st for x in ("Search Engines", "Search Result Description", "Google Search Console Code", "Bing Webmaster Tools Code")))
setting("seo_enabled", "0")
rb0 = anon.get(B + "/robots.txt").text
check("switched off: every crawler is told to stay away, the sitemap is gone and pages say noindex", "Disallow: /\n" in rb0 and anon.get(B + "/sitemap.xml").status_code == 404 and 'name="robots" content="noindex, nofollow"' in anon.get(B + "/").text and anon.get(B + "/about").headers.get("X-Robots-Tag") == "noindex, nofollow")
psql("DELETE FROM settings WHERE key LIKE 'seo_%'")
print("ALL PASS" if ok_all else "SOME FAILED")
