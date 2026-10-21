import os
"""Per-feeder filter end to end: a fake second feeder sends real (CRC-valid) ADS-B messages to the test hub's Beast input with its
own receiver id, and the portal must show that aircraft only for that feeder. Needs the test hub (port 30004) and ssh to the test box."""
import re, socket, subprocess, time, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name, "" if cond else extra); ok_all &= bool(cond)

def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""],
                          capture_output=True, text=True).stdout

def crc24(bits_bytes):
    poly, crc = 0xFFF409, 0
    for byte in bits_bytes:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1000000 | poly
        crc &= 0xFFFFFF
    return crc

def df17_ident(icao, call):
    chars = "#ABCDEFGHIJKLMNOPQRSTUVWXYZ#####_###############0123456789######"
    bits = 0
    for ch in call.ljust(8, "_"):
        bits = (bits << 6) | chars.index(ch)
    me = (4 << 3 | 3).to_bytes(1, "big") + bits.to_bytes(6, "big")
    body = bytes([17 << 3 | 5]) + bytes.fromhex(icao) + me
    return body + crc24(body).to_bytes(3, "big")

def esc(b): return b.replace(b"\x1a", b"\x1a\x1a")

FAKE_UUID, FAKE_ICAO = "deadbeef-0011-2233-4455-66778899aabb", "ab1234"
MY_UUID = "606ea81c-a527-4a74-bbdc-7cfc1bab631f"        # the real test feeder (Sheridan)
adm = sess("marty", PW)
psql(f"DELETE FROM feeders WHERE uuid='{FAKE_UUID}'")
psql(f"INSERT INTO feeders (uuid, station_id, name, owner_id, online) VALUES ('{FAKE_UUID}', 'FAKE-1', 'Fake Station', (SELECT id FROM users WHERE username='marty'), TRUE)")
psql(f"UPDATE feeders SET station_id='KD5VCF', name='Sheridan Test', owner_id=(SELECT id FROM users WHERE username='marty') WHERE uuid='{MY_UUID}'")

s = socket.create_connection((os.environ.get("TEST_HOST", "test-box.invalid"), 30004), timeout=10)
s.sendall(b"\x1a\xe3" + esc(bytes.fromhex(FAKE_UUID.replace("-", "")[:16])))
for i in range(40):                                    # a steady trickle so the hub treats the aircraft as live
    msg = df17_ident(FAKE_ICAO, "TEST123")
    s.sendall(b"\x1a\x33" + esc(bytes(6)) + b"\x28" + esc(msg))
    time.sleep(0.25)
    if i == 25:
        break

def fetch(q=""):
    return requests.get(B + "/api/aircraft.json" + q, timeout=15).json()

hexes = lambda d: {a["hex"] for a in d["aircraft"]}
everyone = fetch()
check("receiver feed is connected", fetch("?feeder=FAKE-1")["filter"]["available"] is True)
check("the fake aircraft is in the unfiltered feed", FAKE_ICAO in hexes(everyone), sorted(hexes(everyone))[:10])
only_fake = fetch("?feeder=FAKE-1")
check("filtering to the fake feeder shows its aircraft", FAKE_ICAO in hexes(only_fake), only_fake.get("filter"))
check("and only the aircraft it heard", hexes(only_fake) <= {FAKE_ICAO}, hexes(only_fake))
only_real = fetch("?feeder=kd5vcf")
check("the real feeder's list does not contain the fake aircraft", FAKE_ICAO not in hexes(only_real) and len(only_real["aircraft"]) > 0, only_real.get("filter"))
both = fetch("?feeder=FAKE-1,KD5VCF")
check("two feeders together show both sets", FAKE_ICAO in hexes(both) and len(hexes(both)) >= len(hexes(only_real)))
unk = fetch("?feeder=NOPE-9")
check("an unknown station shows nothing and is reported", unk["aircraft"] == [] and unk["filter"]["unknown"] == ["NOPE-9"])
check("filter matches are counted", only_fake["filter"]["matched"] == len(only_fake["aircraft"]))

# station pages at the site root
st = requests.get(B + "/fake-1")
check("station page answers at /<station id> in any case", st.status_code == 200 and "Fake Station" in st.text and '"focus": ["FAKE-1"]' in st.text, st.status_code)
check("unknown station gives 404", requests.get(B + "/nosuch-1").status_code == 404)
check("fixed pages still win over station IDs", requests.get(B + "/feed/").status_code == 200 and "How to Feed" in requests.get(B + "/feed/").text)
live = requests.get(B + "/live?feeder=KD5VCF,bad id")
check("/live accepts a feeder list in the address", '"focus": ["KD5VCF"]' in live.text, re.findall(r'"focus": [^]]*\]', live.text))
check("feeder picker is on the live page", 'id="lv-feeders"' in live.text)
s.close()
psql(f"DELETE FROM feeders WHERE uuid='{FAKE_UUID}'")
psql(f"UPDATE feeders SET owner_id=NULL, station_id=NULL, name=NULL WHERE uuid='{MY_UUID}'")   # leave the real test feeder unclaimed for the other suites
print("ALL PASS" if ok_all else "SOME FAILED")
