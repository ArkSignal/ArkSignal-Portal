"""Runs inside the portal container: docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/receivers_unit.py"""
import random
from app.receivers import Parser, receiver_key

ok_all = True
def check(name, cond):
    global ok_all
    print(("PASS " if cond else "FAIL ") + name); ok_all &= bool(cond)

def esc(b):  # double any 0x1a inside frame data
    return b.replace(b"\x1a", b"\x1a\x1a")
def frame(t, data):
    return b"\x1a" + bytes([t]) + esc(data)
RID1, RID2 = bytes.fromhex("606ea81ca5274a74"), bytes.fromhex("1a2b3c4d5e6f7081")   # RID2 contains 0x1a
def adsb(icao):  # DF17, then address, then 10 more bytes
    return bytes([17 << 3 | 5]) + bytes.fromhex(icao) + bytes(10)
def tail(msg): return bytes(6) + b"\x00" + msg
stream = (frame(0x31, bytes(9)) + frame(0xE3, RID1) + frame(0x33, tail(adsb("a403df"))) + frame(0x33, tail(adsb("1a1a1a"))) +
          frame(0xE3, RID2) + frame(0x33, tail(adsb("ac5312"))) + frame(0x32, tail(bytes([5 << 3]) + bytes.fromhex("aabbcc") + bytes(3))) +
          frame(0x33, tail(bytes([11 << 3]) + bytes.fromhex("123456") + bytes(10))))
want = [(RID1.hex(), "a403df", False), (RID1.hex(), "1a1a1a", False), (RID2.hex(), "ac5312", False), (RID2.hex(), "123456", False)]
p = Parser(); got = p.feed(stream)
check("parses receivers, addresses, escaped bytes, skips parity-overlaid formats", got == want)
for trial in range(200):          # split the stream at random places: same answer
    p, got, i = Parser(), [], 0
    while i < len(stream):
        k = random.randint(1, 9); got += p.feed(stream[i:i + k]); i += k
    if got != want: break
check("same result for 200 random splits of the stream", got == want)
p = Parser(); got = p.feed(b"\x00\x01garbage\x1a" + stream[:20] + stream)
check("recovers from garbage and a cut-off frame", want[0] in got and want[-1] in got)
check("receiver key is the first half of the UUID", receiver_key("606EA81C-A527-4A74-BBDC-7CFC1BAB631F") == "606ea81ca5274a74")
print("ALL PASS" if ok_all else "SOME FAILED")
