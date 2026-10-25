"""Switch every feature and service on for the two test sites (the throwaway :18430 and the persistent :18431), point the Tools menu at the test box's own Grafana, hub and Planefence, open the mesh broker's TLS listener with a
self-signed certificate, give the mesh service a set of demo nodes, and press Apply so the broker and the service hold the settings and logins.
The test suites change settings and wipe data, so this runs after a deploy (tests/deploy_test.sh) and at the end of tests/run_all.py. Google sign-in stays off: it needs a real client ID and secret.
Usage: python tests/testsite_on.py"""
import os, re, subprocess, sys
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
SUDO = "echo $TEST_SUDO_PASSWORD | sudo -S -p ''"
D = "/usr/local/bin/docker"


def ssh(cmd, timeout=180):
    return subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=timeout)


def apply_sql():
    subprocess.run(["scp", "-q", os.path.join(HERE, "testsite_on.sql"), "nastemp:testsite_on.sql"], check=True, capture_output=True)
    for db in ("adsb-portal-db", "adsb-portal-persist-db"):
        ssh(f"{SUDO} {D} cp ~/testsite_on.sql {db}:/tmp/testsite_on.sql; {SUDO} {D} exec {db} psql -U portal -d portal -q -f /tmp/testsite_on.sql")


def broker_tls():
    """A self-signed certificate in the test broker's config folder, so its TLS listener (8883, published on 18884) is open; kept if one is already there."""
    v = "/var/lib/docker/volumes/mesh_test_mqtt/_data/certs"
    if ssh(f"{SUDO} test -s {v}/fullchain.pem && echo yes").stdout.strip().endswith("yes"):
        return
    ssh(f"{SUDO} sh -c \"mkdir -p {v}; openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj /CN=feed.arksignal.net -addext subjectAltName=DNS:feed.arksignal.net,IP:test-box.invalid -keyout {v}/privkey.pem -out {v}/fullchain.pem 2>/dev/null; "
        f"chmod 644 {v}/fullchain.pem; chmod 640 {v}/privkey.pem; chown 1883:1883 {v}/privkey.pem {v}/fullchain.pem; {D} restart mesh-mqtt >/dev/null\"")


def apply_mesh():
    pw = re.search(r"password: (\S+)", open(os.path.join(HERE, "..", "TEST_CREDENTIALS.md")).read()).group(1)
    for port in (18430, 18431):
        B = f"http://test-box.invalid:{port}"
        s = requests.Session()
        t = re.search(r'name="csrf_token" value="([^"]+)"', s.get(B + "/my/login", timeout=20).text).group(1)
        s.post(B + "/my/login", data={"csrf_token": t, "username": "marty", "password": pw}, timeout=20)
        page = s.get(B + "/admin/mesh", timeout=20).text
        m = re.search(r'name="csrf_token" value="([^"]+)"', page)
        if m:
            s.post(B + "/admin/mesh/apply", data={"csrf_token": m.group(1)}, headers={"X-Requested-With": "XMLHttpRequest"}, timeout=60)


def reset_throwaway():
    """Back to the built-in defaults for every switch testsite_on.sql sets, on the throwaway site only: the suites assume the defaults (a menu without Mesh, ACARS or MLAT). run_all.py calls this first."""
    keys = re.findall(r"\('([a-z_]+)', '1'\)", open(os.path.join(HERE, "testsite_on.sql"), encoding="utf-8").read())
    keys += ["matrix_enabled"]   # the Matrix suites turn Matrix on for themselves; it replaces the built-in chat, so a leftover would break every chat suite
    ssh(f"{SUDO} {D} exec adsb-portal-db psql -U portal -d portal -q -c \"DELETE FROM settings WHERE key IN ({', '.join(repr(k) for k in keys)})\"")


if __name__ == "__main__":
    if "--reset" in sys.argv:
        reset_throwaway()
        print("throwaway test site: defaults restored")
        raise SystemExit(0)
    apply_sql()
    broker_tls()
    subprocess.run([sys.executable, os.path.join(HERE, "seed_mesh_demo.py")], capture_output=True, text=True, timeout=120)
    apply_mesh()
    print("test sites: every feature on")
