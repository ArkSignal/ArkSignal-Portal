import os, subprocess
PORTAL = f"http://{os.environ.get('TEST_HOST', 'test-box.invalid')}:18430"
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
HS = "http://test-box.invalid:18008"
for k, v in (("matrix_enabled", "1"), ("matrix_element_url", "http://test-box.invalid:18009"), ("matrix_public_url", HS), ("matrix_server_name", "matrix-test.local"), ("matrix_issuer", PORTAL),
             ("matrix_oidc_internal", "http://adsb-portal:8430"), ("matrix_redirect_uris", HS + "/_synapse/client/oidc/callback"), ("matrix_url_previews", "0")):
    psql(f"INSERT INTO settings (key, value) VALUES ('{k}', '{v}') ON CONFLICT (key) DO UPDATE SET value = '{v}'")
if __name__ == "__main__":
    exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
    adm = sess("marty", PW)
    print(act(adm, "/admin/matrix/apply", "/admin/matrix")[:2])
