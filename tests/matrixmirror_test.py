"""The Discord mirror of a standing room (runs matrixmirror_inner.py inside the portal container)."""
import subprocess
r = subprocess.run(["sh", "run_unit.sh", "matrixmirror_inner.py"], capture_output=True, text=True, timeout=300)
print(r.stdout)
if r.returncode != 0 and "ALL PASS" not in r.stdout:
    print("FAIL the in-container script did not finish", r.stderr[-300:])
