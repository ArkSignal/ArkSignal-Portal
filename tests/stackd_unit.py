"""The stack updater's handling of a name conflict (checked on this PC, no Docker needed): a container that holds a name compose needs is removed when it is stopped or managed by the update
service, never when it is the portal, its database, the updater or Watchtower, and never when it is running and not managed by the update service."""
import importlib.util, os, subprocess, sys
spec = importlib.util.spec_from_file_location("stackd", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "deploy", "stack", "stackd.py"))
sd = importlib.util.module_from_spec(spec); spec.loader.exec_module(sd)
ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)
CID = "5b08928e943c67d017e5ba8f305396e45ce0a65634ce35fc273511ea116bbab3"
MSG = f'Error response from daemon: Conflict. The container name "/{{name}}" is already in use by container "{CID}". You have to remove (or rename) that container to be able to reuse that name.'
calls = []
def fake(state):
    def run(cmd, **kw):
        calls.append(cmd)
        class R: returncode = 0; stdout = state; stderr = ""
        return R()
    return run
real = subprocess.run
try:
    for label, state, name, expect in (("a stopped container", "false \n", "grafana", True), ("a running container the update service manages", "true true\n", "grafana", True),
                                       ("a running container nobody manages", "true \n", "grafana", False), ("the portal", "true true\n", "adsb-portal", False), ("Watchtower", "true true\n", "watchtower", False),
                                       ("the updater itself", "false \n", "adsb-stack", False), ("a parked leftover name is left to the older rule", "false \n", "5b08928e943c_grafana", False)):
        calls.clear(); sd.subprocess.run = fake(state)
        got = sd.clear_name_conflicts(MSG.format(name=name))
        removed = any(c[:3] == ["docker", "rm", "-f"] for c in calls)
        check(f"{label}: {'removed' if expect else 'left alone'}", got == expect and removed == expect, (got, calls))
    calls.clear(); sd.subprocess.run = fake("false \n")
    check("text without a conflict removes nothing", sd.clear_name_conflicts("everything is fine") is False and not calls)
    # a stack that fails once because Watchtower recreated the same container is asked again (and starts) instead of staying failed
    import tempfile, json as _j
    tmp = tempfile.mkdtemp(); comp = os.path.join(tmp, "c.yaml"); open(comp, "w").write("services: {}" + chr(10))
    runs = []
    def flaky(cmd, **kw):
        runs.append(cmd)
        class R: pass
        r = R(); r.stdout = ""; r.stderr = "Error response from daemon: No such container: 392d54d4" if len(runs) < 2 else ""; r.returncode = 1 if len(runs) < 2 else 0
        return r
    sd.subprocess.run = flaky; naps = []; real_sleep = sd.time.sleep; sd.time.sleep = lambda n: naps.append(n)
    try:
        res = sd.apply_stack(tmp, {"name": "portal", "dir": "portal", "file": comp, "env": "none"})
    finally:
        sd.time.sleep = real_sleep
    check("a stack that failed once because another updater took its container is tried again and comes up", res["ok"] is True and len(runs) == 2 and naps == [20], (res, len(runs), naps))
    runs.clear(); sd.subprocess.run = lambda cmd, **kw: (runs.append(cmd), type("R", (), {"stdout": "", "stderr": "boom", "returncode": 1})())[1]; naps.clear(); sd.time.sleep = lambda n: naps.append(n)
    try:
        res = sd.apply_stack(tmp, {"name": "portal", "dir": "portal", "file": comp, "env": "none"})
    finally:
        sd.time.sleep = real_sleep
    check("one that keeps failing is tried three times in all, then reported as failed", res["ok"] is False and len(runs) == 3 and naps == [20, 40], (len(runs), naps))
finally:
    sd.subprocess.run = real
print("ALL PASS" if ok_all else "SOME FAILED")
