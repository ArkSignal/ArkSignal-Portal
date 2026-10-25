"""Mesh hardware names (runs meshhw_unit.py inside the mesh container on the test box; needs sh tests/deploy_mesh.sh)."""
import subprocess
DK = "echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker"
subprocess.run(["scp", "-q", "meshhw_unit.py", "nastemp:adsb-portal/meshhw_unit.py"], check=True)
r = subprocess.run(["ssh", "-q", "nastemp", f"{DK} cp ~/adsb-portal/meshhw_unit.py mesh:/tmp/meshhw_unit.py && {DK} exec mesh python /tmp/meshhw_unit.py"], capture_output=True, text=True, timeout=300)
print(r.stdout)
if "ALL PASS" not in r.stdout:
    print("FAIL the hardware names check did not pass", r.stderr[-300:])
