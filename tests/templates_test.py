"""Every template compiles. A page whose template has a nesting mistake (a stray endwith, a missing endblock) only fails when somebody opens it, and only with a 500; this finds it without opening anything."""
import glob, os
import jinja2
here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "templates")
env = jinja2.Environment(loader=jinja2.FileSystemLoader(here))
bad = []
files = sorted(glob.glob(os.path.join(here, "**", "*.html"), recursive=True))
for f in files:
    try:
        env.parse(open(f, encoding="utf-8").read())
    except jinja2.TemplateSyntaxError as e:
        bad.append((os.path.relpath(f, here), e.lineno, str(e)[:100]))
print(("PASS" if not bad else "FAIL") + f" all {len(files)} templates compile", bad if bad else "")
print("ALL PASS" if not bad else "SOME FAILED")
