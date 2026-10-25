"""A new install: the schema builds on an empty database, and building it again is harmless (runs freshdb_unit.py inside the portal container on a scratch database, which it removes)."""
import subprocess
r = subprocess.run(["sh", "run_unit.sh", "freshdb_unit.py"], capture_output=True, text=True, timeout=600)
print(r.stdout)
if "ALL PASS" not in r.stdout:
    print("FAIL the fresh database check did not pass", r.stderr[-300:])
