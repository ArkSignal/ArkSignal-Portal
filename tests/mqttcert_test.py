"""The mesh broker finds its TLS certificate by itself: it takes the one that covers the feed name from the proxy's certificate store (here a folder with two self-signed certificates), opens the
8883 listener, ignores a certificate for another name, and follows a renewal (a newer certificate for the same name) without anyone copying or restarting anything.
Needs the sandbox with Docker and the image from `sh tests/deploy_mesh.sh` (adsb-mesh-mqtt:test)."""
import subprocess, time

ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def ssh(cmd, timeout=120):
    r = subprocess.run(["ssh", "-q", "nastemp", cmd], capture_output=True, text=True, timeout=timeout)
    return (r.stdout + r.stderr).strip()

HOST = "feed.certtest.example"
CLEAN = "docker rm -f certtest >/dev/null 2>&1; docker run --rm -v /tmp:/t --entrypoint sh adsb-mesh-mqtt:test -c 'rm -rf /t/le /t/mqttcfg'"
ssh(CLEAN + "; mkdir -p /tmp/le/live/npm-1 /tmp/le/live/npm-2 /tmp/mqttcfg")
def make(dirname, cn, days):
    ssh(f"openssl req -x509 -newkey rsa:2048 -nodes -days {days} -subj /CN={cn} -addext subjectAltName=DNS:{cn} -keyout /tmp/le/live/{dirname}/privkey.pem -out /tmp/le/live/{dirname}/fullchain.pem 2>/dev/null; chmod 644 /tmp/le/live/{dirname}/*.pem")
def serial():
    return ssh("docker exec certtest sh -c \"echo | openssl s_client -connect 127.0.0.1:8883 2>/dev/null | openssl x509 -noout -serial -subject\"")
make("npm-2", "other.example", 90)   # a certificate for another name must be ignored
ssh(f"docker run -d --restart unless-stopped --name certtest -e MESH_CERT_HOST={HOST} -e CERT_CHECK_SECONDS=4 -v /tmp/mqttcfg:/mosquitto/config -v /tmp/le:/letsencrypt:ro adsb-mesh-mqtt:test >/dev/null")
time.sleep(5)
check("with no certificate for the name the broker runs without the TLS listener (and for another name's certificate)", "serial=" not in serial() and ssh("docker exec certtest sh -c 'test -s /mosquitto/config/certs/fullchain.pem && echo has || echo none'") == "none")
make("npm-1", HOST, 30)
ok = False
for _ in range(25):
    time.sleep(2)
    s1 = serial()
    if HOST in s1:
        ok = True; break
check("when a certificate for the name appears the broker finds it, restarts and opens the TLS listener", ok, s1 if ok is False else "")
check("the certificate was copied for the broker only (private key not world readable)", ssh("docker exec certtest sh -c 'stat -c %a /mosquitto/config/certs/privkey.pem'") in ("640", "600"), ssh("docker exec certtest sh -c 'stat -c %a /mosquitto/config/certs/privkey.pem'"))
make("npm-1", HOST, 60)   # a renewal: a different certificate for the same name
renewed = False
for _ in range(25):
    time.sleep(2)
    s2 = serial()
    if HOST in s2 and s2 != s1:
        renewed = True; break
check("a renewal is followed by itself (the new certificate is served)", renewed, (s1, s2))
check("the broker still answers its internal listener after all that", "rc=0" in ssh("docker exec certtest sh -c 'mosquitto_pub -h 127.0.0.1 -u mesh-admin -P \"$(cat /mosquitto/config/admin.pass)\" -t t/x -m hi; echo rc=$?'"))
ssh(CLEAN)
print("ALL PASS" if ok_all else "SOME FAILED")
