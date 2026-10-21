# build a basic UAT ADS-B downlink frame (18 bytes) for a given address/lat/lon/altitude
def frame(addr=0xABCDEF, lat=34.9, lon=-92.1, alt_ft=3000):
    bits = 0; n = 0
    def put(v, w):
        nonlocal bits, n
        bits = (bits << w) | (v & ((1 << w) - 1)); n += w
    put(0, 5)            # MDB type 0 (basic, 18 bytes)
    put(0, 3)            # address qualifier 0: ADS-B ICAO
    put(addr, 24)
    k = 360.0 / (1 << 24)
    put(int(round(lat / k)), 23)
    put(int(round(lon / k)), 24)
    put(0, 1)            # altitude type: barometric
    put(int((alt_ft + 1000) / 25), 12)
    put(8, 4)            # NIC
    put(2, 2)            # air/ground state: airborne, subsonic
    put(0, 2)
    put(0, 11); put(0, 11)  # NS/EW velocity unavailable-ish
    put(0, 9); put(0, 2)
    bits <<= (144 - n) if n < 144 else 0
    return bits.to_bytes(18, "big").hex()
print(frame())
