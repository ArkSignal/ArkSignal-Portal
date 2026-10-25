"""The mesh broker keeps its logins across a restart even when its config folder was made by Docker on the host (owned by root, the real hub's case): the start script hands the folder to the broker's user, so the dynamic security plugin can save dynsec.json. Before the fix every save failed ("unable to open dynsec.json.new for writing") and every login made while the broker ran was lost at the next restart, so nodes were refused (NOT_AUTHORIZED) until Apply sent the logins again. Needs the mesh broker on the test box (sh tests/deploy_mesh.sh)."""
import subprocess, time
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)
SUDO = "echo $TEST_SUDO_PASSWORD | sudo -S -p ''"
def ssh(cmd):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=180).stdout.strip()
VOL = "/var/lib/docker/volumes/mesh_test_mqtt/_data"
if "mesh-mqtt" not in ssh(f"{SUDO} /usr/local/bin/docker ps --format '{{{{.Names}}}}'"):
    print("SKIP meshpersist_test: the mesh broker is not running on the test box (sh tests/deploy_mesh.sh)")
    raise SystemExit(0)
SCRIPT = """#!/bin/sh
P=$(cat /mosquitto/config/admin.pass)
C="mosquitto_ctrl -u mesh-admin -P $P"
case "$1" in
  add) $C dynsec createClient probe-login -p probe-pass-1 ;;
  list) $C dynsec listClients | tr '\\n' ' ' ;;
  del) $C dynsec deleteClient probe-login ;;
esac
"""
open("meshpersist_probe.sh", "w", newline="\n").write(SCRIPT)
subprocess.run(["scp", "-q", "meshpersist_probe.sh", "nastemp:meshpersist_probe.sh"], check=True, capture_output=True)
D = f"{SUDO} /usr/local/bin/docker"
def restart():
    ssh(f"{D} restart mesh-mqtt >/dev/null; sleep 7; {D} cp ~/meshpersist_probe.sh mesh-mqtt:/tmp/probe.sh")
def probe(act):
    return ssh(f"{D} exec mesh-mqtt sh /tmp/probe.sh {act}")
ssh(f"{SUDO} chown root:root {VOL}")   # what Docker makes of a missing host folder
check("the folder starts out owned by root, as a bind mount made by Docker is", ssh(f"{SUDO} stat -c %u {VOL}") == "0")
restart()
check("starting the broker hands the folder to the broker's own user", ssh(f"{SUDO} stat -c %u {VOL}") == "1883", ssh(f"{SUDO} stat -c %u {VOL}"))
probe("del")
probe("add")
check("a login made while the broker runs is there", "probe-login" in probe("list"), probe("list"))
restart()
check("and is still there after the broker restarts (it was lost before the fix)", "probe-login" in probe("list"), probe("list"))
check("the broker's log has no failed save", "Error saving Dynamic security" not in ssh(f"{D} logs --since 2m mesh-mqtt 2>&1"))
probe("del")
print("ALL PASS" if ok_all else "SOME FAILED")
