import os, json, base64
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
os.environ["DISABLE_MESH_SYNC"] = "1"
from app import create_app
app = create_app()
from app import mesh_channels as mc
out = {}
def rv(b, i):
    n = s = 0
    while True:
        c = b[i]; i += 1; n |= (c & 0x7F) << s; s += 7
        if not c & 0x80: return n, i
def parse(b):
    f, i = [], 0
    while i < len(b):
        k, i = rv(b, i); num, wire = k >> 3, k & 7
        if wire == 0: v, i = rv(b, i)
        elif wire == 2:
            n, i = rv(b, i); v = b[i:i + n]; i += n
        else: raise ValueError("wire %d" % wire)
        f.append((num, v))
    return f
def decode(url):
    frag = url.split("#", 1)[1]
    cs = parse(base64.urlsafe_b64decode(frag + "=" * (-len(frag) % 4)))
    return [parse(v) for n, v in cs if n == 1]
# the app's own well-known default-channel address is CgMSAQE: one channel whose psk is the single byte 1
known = base64.urlsafe_b64encode(mc._bytes_field(1, mc._bytes_field(2, b"\x01"))).decode().rstrip("=")
out["matches_the_apps_default_channel_address"] = known == "CgMSAQE"
psk = bytes(range(32))
u = mc.address(psk, "ArkFriends", True, False, 14)
ch = decode(u)
d = dict(ch[0])
out["round_trip"] = len(ch) == 1 and d[2] == psk and d[3] == b"ArkFriends" and d[5] == 1 and 6 not in d and parse(d[7]) == [(1, 14)]
out["add_and_replace_addresses"] = u.startswith("https://meshtastic.org/e/?add=true#") and mc.address(psk, "A", add=False).startswith("https://meshtastic.org/e/#") and "=" not in u.split("#")[1]
out["no_options_means_no_fields"] = set(dict(decode(mc.address(psk, "A"))[0])) == {2, 3}
out["default_channel_address_is_the_apps_own"] = mc.address_of([mc.DEFAULT_SETTINGS]).endswith("#CgMSAQE")
two = decode(mc.address_of([mc.DEFAULT_SETTINGS, mc.build_settings(psk, "Second", False, True, 16)], False))
out["several_channels_in_one_address"] = len(two) == 2 and dict(two[0])[2] == b"" and 3 not in dict(two[0]) and dict(two[1])[3] == b"Second" and dict(two[1])[6] == 1 and mc.address_of([mc.DEFAULT_SETTINGS], False).startswith("https://meshtastic.org/e/#")
svg = mc.qr_svg(u)
out["qr_is_an_svg"] = svg.lstrip().startswith("<svg") and "path" in svg
print(json.dumps(out))
