"""Known radio frequencies in one list: the amateur calling and APRS frequencies, FRS, GMRS, MURS, weather radio and the other services people ask about (mesh networks join it when they are added).
The built-in rows are widely published US allocations and conventions; an editor can add, change or remove rows on the page (rows keep a `builtin` flag so an update never overwrites an edit)."""
from .db import get_db

SERVICES = [("ham", "Amateur Radio"), ("aprs", "APRS"), ("frs", "FRS"), ("gmrs", "GMRS"), ("murs", "MURS"), ("wx", "Weather Radio"), ("marine", "Marine and Aviation"), ("mesh", "Mesh and LoRa"), ("other", "Other")]

def _r(service, name, mhz, mode, notes=""):
    return (service, name, round(mhz * 1e6), mode, notes)

BUILTIN = [
    _r("aprs", "APRS (United States, Arkansas)", 144.390, "FM, 1200 baud AFSK", "The one APRS frequency used across the United States, including Arkansas. Trackers and weather stations beacon here."),
    _r("aprs", "APRS from the International Space Station", 145.825, "FM, 1200 baud AFSK", "ISS digipeater: send a packet through the station as it passes."),
    _r("ham", "2 m National Simplex Calling", 146.520, "FM", "Where hams call each other without a repeater, and the first place to listen on a trip."),
    _r("ham", "70 cm National Simplex Calling", 446.000, "FM", "The 70 cm simplex calling frequency."),
    _r("ham", "6 m FM Simplex Calling", 52.525, "FM", ""),
    _r("ham", "6 m SSB Calling", 50.125, "USB", "The DX calling frequency for 6 meters."),
    _r("ham", "1.25 m (220) Simplex Calling", 223.500, "FM", ""),
    _r("ham", "33 cm Simplex Calling", 927.500, "FM", ""),
    _r("ham", "10 m FM Calling", 29.600, "FM", ""),
    _r("ham", "10 m SSB Calling", 28.400, "USB", ""),
    _r("ham", "2 m SSB and CW Calling", 144.200, "USB", "Weak signal calling frequency."),
    _r("ham", "70 cm SSB and CW Calling", 432.100, "USB", "Weak signal calling frequency."),
    _r("ham", "DMR Simplex", 446.500, "DMR", "A common DMR simplex frequency: color code 1, time slot 1, talkgroup 99."),
    _r("ham", "ISS Voice Downlink", 145.800, "FM", "Amateur voice from the International Space Station when it is on."),
    _r("ham", "FT8 on 40 m", 7.074, "USB digital", "Dial frequency."),
    _r("ham", "FT8 on 20 m", 14.074, "USB digital", "Dial frequency."),
    _r("ham", "FT8 on 80 m", 3.573, "USB digital", "Dial frequency."),
    _r("wx", "NOAA Weather Radio 1", 162.400, "NFM", ""), _r("wx", "NOAA Weather Radio 2", 162.425, "NFM", ""), _r("wx", "NOAA Weather Radio 3", 162.450, "NFM", ""),
    _r("wx", "NOAA Weather Radio 4", 162.475, "NFM", ""), _r("wx", "NOAA Weather Radio 5", 162.500, "NFM", ""), _r("wx", "NOAA Weather Radio 6", 162.525, "NFM", ""), _r("wx", "NOAA Weather Radio 7", 162.550, "NFM", ""),
    _r("murs", "MURS 1", 151.820, "NFM", "No license needed; low power."), _r("murs", "MURS 2", 151.880, "NFM", ""), _r("murs", "MURS 3", 151.940, "NFM", ""),
    _r("murs", "MURS Blue Dot", 154.570, "NFM", ""), _r("murs", "MURS Green Dot", 154.600, "NFM", ""),
    _r("marine", "Marine VHF Channel 16", 156.800, "FM", "Distress, safety and calling."),
    _r("marine", "Aviation Emergency (Guard)", 121.500, "AM", "International aircraft emergency frequency."),
    _r("mesh", "Meshtastic LongFast (United States)", 906.875, "LoRa", "The default channel of Meshtastic in the US 902 to 928 MHz band."),
]
_FRS = [462.5625, 462.5875, 462.6125, 462.6375, 462.6625, 462.6875, 462.7125, 467.5625, 467.5875, 467.6125, 467.6375, 467.6625, 467.6875, 467.7125, 462.5500, 462.5750, 462.6000, 462.6250, 462.6500, 462.6750, 462.7000, 462.7250]
for _i, _f in enumerate(_FRS, start=1):
    BUILTIN.append(_r("frs", f"FRS Channel {_i}", _f, "NFM" if _i > 7 and _i < 15 else "FM", "FRS needs no individual license (FCC Part 95 Subpart B)" + (", 0.5 W only" if 8 <= _i <= 14 else (", up to 2 W on channels 1 to 7 and 15 to 22" if _i <= 7 or _i >= 15 else "")) + "."))
    if _i <= 7 or _i >= 15:
        BUILTIN.append(_r("gmrs", f"GMRS Channel {_i}", _f, "FM", "An FCC GMRS license is required (one license covers your immediate family; no exam)" + (", shared with FRS" if _i <= 7 or _i >= 15 else "") + "." + (" Repeater output." if _i >= 15 else "")))
for _i, _f in enumerate([467.550, 467.575, 467.600, 467.625, 467.650, 467.675, 467.700, 467.725], start=15):
    BUILTIN.append(_r("gmrs", f"GMRS Repeater Input {_i}R", _f, "FM", f"The input of GMRS repeater channel {_i} (its output is 462.xxx). An FCC GMRS license is required."))


def seed():
    """Adds any built-in row that is not there yet (by service, name and frequency); never touches an edited row. Called at start."""
    d = get_db()
    for service, name, hz, mode, notes in BUILTIN:
        if not d.execute("SELECT 1 FROM frequencies WHERE service=%s AND name=%s", (service, name)).fetchone():
            d.execute("INSERT INTO frequencies (service, name, freq_hz, mode, notes, builtin) VALUES (%s,%s,%s,%s,%s,TRUE)", (service, name, hz, mode, notes))
        else:   # a built-in row follows the current wording (an administrator's own rows are never touched)
            d.execute("UPDATE frequencies SET notes=%s, mode=%s, freq_hz=%s WHERE service=%s AND name=%s AND builtin AND (notes <> %s OR mode <> %s OR freq_hz <> %s)", (notes, mode, hz, service, name, notes, mode, hz))
    d.commit()


def search(service="", q=""):
    where, args = [], []
    if service in dict(SERVICES):
        where.append("service=%s"); args.append(service)
    if q:
        where.append("(name ILIKE %s OR notes ILIKE %s OR mode ILIKE %s OR (freq_hz::numeric / 1000000)::text LIKE %s)"); like = "%" + q.replace("%", "").replace("_", "") + "%"; args += [like, like, like, q.replace("%", "") + "%"]
    return get_db().execute("SELECT * FROM frequencies" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY service, freq_hz, name", args).fetchall()


def mhz(hz):
    return ("%.4f" % (hz / 1e6)).rstrip("0").rstrip(".") if hz % 1000 == 0 and False else "%.4f" % (hz / 1e6)
