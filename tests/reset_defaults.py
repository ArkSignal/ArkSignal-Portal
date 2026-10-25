"""Not a test: puts the throwaway test site's switches back to the built-in defaults between suites (see testsite_on.py --reset), so a suite that leaves a feature on cannot change what the next one sees."""
import subprocess, sys
r = subprocess.run([sys.executable, "testsite_on.py", "--reset"], capture_output=True, text=True, timeout=300)
print(("PASS " if r.returncode == 0 else "FAIL ") + "the throwaway site's switches are back to the defaults", r.stdout.strip()[-80:], r.stderr.strip()[-200:])
print("ALL PASS" if r.returncode == 0 else "SOME FAILED")
