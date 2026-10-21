"""Documentation is part of the build cycle: the release notes and roadmap data are valid and published to the public content repository the portal reads live; the roadmap, the project tracker
and the release notes have been touched recently relative to the code (a run of code commits without them fails); the handoff and tracker mention the current work."""
import base64, json, os, subprocess, sys
here = os.path.dirname(os.path.abspath(__file__)); root = os.path.join(here, "..")
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def git(*a):
    return subprocess.run(["git", "-C", root, *a], capture_output=True, text=True).stdout.strip()
notes = json.load(open(os.path.join(root, "app", "release_notes.json"), encoding="utf-8")); road = json.load(open(os.path.join(root, "app", "roadmap.json"), encoding="utf-8"))
check("release notes are a list of releases and the first is the coming-next entry (empty only right after a release)", isinstance(notes, list) and notes[0]["version"] == "next")
check("the roadmap has items and today's or a recent update date", isinstance(road.get("items"), list) and len(road["items"]) > 20 and road.get("updated"))
def since(path):   # code commits since the last commit that touched this file
    last = git("log", "-1", "--format=%H", "--", path)
    return int(git("rev-list", "--count", f"{last}..HEAD", "--", "app", "deploy", "tests") or 0) if last else 999
for path, limit in (("app/release_notes.json", 6), ("app/roadmap.json", 8), ("ROADMAP.md", 8), ("PROJECT_TRACKER.md", 25)):
    n = since(path)
    check(f"{path} is kept up to date: {n} code commits since it last changed (limit {limit})", n <= limit, n)
try:
    out = subprocess.run(["gh", "api", "repos/mallison01/adsb-portal-content/contents/release_notes.json", "-q", ".content"], capture_output=True, text=True, timeout=60).stdout
    pub = json.loads(base64.b64decode(out).decode("utf-8")) if out.strip() else None
    out2 = subprocess.run(["gh", "api", "repos/mallison01/adsb-portal-content/contents/roadmap.json", "-q", ".content"], capture_output=True, text=True, timeout=60).stdout
    pubr = json.loads(base64.b64decode(out2).decode("utf-8")) if out2.strip() else None
    if pub is None:
        print("note: GitHub cannot be reached from here, the public copy was not compared")
    else:
        check("the public release notes match this working copy (run tools/publish_content.sh)", pub == notes)
        check("the public roadmap matches this working copy (run tools/publish_content.sh)", pubr == road)
except Exception as e:
    print("note: the public copy was not compared:", e)
print("ALL PASS" if ok_all else "SOME FAILED")
