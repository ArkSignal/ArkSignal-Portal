"""Runs inside the mesh container (tests/meshhw_test.py): hardware names. The names that were missing now resolve, an unknown model says so plainly, the project's own list (a stand-in here) adds new models
without replacing the names we keep, and an unreachable source changes nothing."""
import http.server, json, sys, threading
sys.path.insert(0, "/app")
import meshd
fails = 0
def check(n, c, x=""):
    global fails
    print(("PASS " if c else "FAIL ") + n, "" if c else x)
    fails += 0 if c else 1
check("the models that showed as a number now have names", all(meshd.hw_name(i) for i in (71, 89, 99, 100, 102, 103, 105, 113, 122, 124)), [i for i in (71, 89, 99, 100, 102, 103, 105, 113, 122, 124) if not meshd.hw_name(i)])
check("a few of them are right", meshd.hw_name(71) == "Tracker T1000-E" and meshd.hw_name(99) == "Seeed Wio Tracker L1" and meshd.hw_name(89) == "Thinknode M1")
r = meshd.node_row({"node_id": 0xa1b2c3d4, "hw_model": 250, "role": 0, "region": 1, "modem_preset": 0})
check("a model nobody has listed says so in words, with its number", r["hardware"] == "Unlisted hardware (#250)", r["hardware"])
check("a known model shows its name and no model shows nothing", meshd.node_row({"node_id": 1, "hw_model": 71, "role": 0})["hardware"] == "Tracker T1000-E" and meshd.node_row({"node_id": 1, "hw_model": None, "role": 0})["hardware"] == "")
SAMPLE = """syntax = "proto3";
enum Other { A = 1; }
enum HardwareModel {
  // unset
  UNSET = 0;
  TLORA_V2 = 1;
  RAK4631 = 9;
  HELTEC_MESH_NODE_T114 = 69;
  NEW_THING_X1 = 200;
  TBEAM_BPF2 = 201;
  PRIVATE_HW = 255;
}
enum After { B = 2; }
"""
got = meshd.parse_hw_enum(SAMPLE)
check("the project's list is read: models only, not UNSET, not the private one, not other enums", got == {1: "TLoRa V2", 9: "RAK4631", 69: "Heltec Mesh Node T114", 200: "New Thing X1", 201: "T-Beam BPF2"}, got)
check("names are spelled the way their makers do", meshd.pretty_hw("RAK3401") == "RAK3401" and meshd.pretty_hw("TBEAM") == "T-Beam" and meshd.pretty_hw("SEEED_WIO_TRACKER_L1") == "Seeed Wio Tracker L1")
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(SAMPLE.replace("RAK4631 = 9", "SOMETHING_ELSE = 9").encode())
srv = http.server.HTTPServer(("127.0.0.1", 18999), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
orig = meshd.cfg
meshd.cfg = lambda k: "http://127.0.0.1:18999/mesh.proto" if k == "hw_source" else orig(k)
n = meshd.refresh_hardware()
check("a refresh adds the models this service did not know and keeps the names it has", n >= 2 and meshd.hw_name(200) == "New Thing X1" and meshd.hw_name(9) == "RAK4631" and meshd.hw_name(201) == "T-Beam BPF2", (n, meshd.hw_name(9)))
check("a model that was only in the project's list now names itself on a node", meshd.node_row({"node_id": 2, "hw_model": 200, "role": 0})["hardware"] == "New Thing X1")
srv.shutdown()
meshd.cfg = lambda k: "http://127.0.0.1:1/none" if k == "hw_source" else orig(k)
before = dict(meshd.HW_EXTRA)
check("an unreachable source changes nothing", meshd.refresh_hardware() == -1 and meshd.HW_EXTRA == before)
print("ALL PASS" if not fails else "SOME FAILED")
