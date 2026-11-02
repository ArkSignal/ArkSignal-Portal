"""In-container checks of the airport weather tiles on the Weather Map: a tile whose kept copy has expired still shows its last airports while a fresh answer is fetched (they used to blink out and back),
a tile nobody has asked for yet is still counted as pending, and a tile the service failed on lately is not waited for.
docker cp to /tmp and run: docker exec -w /app -e PYTHONPATH=/app adsb-portal python /tmp/wxstale_unit.py"""
import os
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"; os.environ["HUB_URL"] = ""
import time
from app import create_app, cache, wxsources as wx

app = create_app()
ok_all = True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ") + n, "" if cond else extra); ok_all &= bool(cond)

kept = {}
cache.peek = lambda key: kept.get(key)
asked = []
wx._load_tile_in_background = lambda app_, t: asked.append(t)
wx._STALE.clear(); wx._FAILED.clear()
with app.app_context():
    bbox = (33.0, -95.0, 34.9, -93.5)   # inside one four degree tile
    tile = (32, -96)
    key = ("metars", tile[0], tile[1], tile[0] + wx.TILE, tile[1] + wx.TILE)
    info = {}
    check("a tile nobody has asked for is pending and fetched in the background", wx._metars(bbox, info) == {} and info.get("pending") == 1 and asked == [tile], (info, asked))
    kept[key] = {"KLIT": {"temp": 20}}
    info = {}
    check("a kept tile is used", wx._metars(bbox, info) == {"KLIT": {"temp": 20}} and not info.get("pending"), info)
    del kept[key]
    asked.clear(); info = {}
    got = wx._metars(bbox, info)
    check("when its kept copy expires its last airports stay, nothing is pending, and a fresh copy is asked for", got == {"KLIT": {"temp": 20}} and not info.get("pending") and asked == [tile], (got, info, asked))
    wx._STALE.clear(); wx._FAILED[tile] = time.time()
    asked.clear(); info = {}
    check("a tile the service failed on lately is not waited for or asked again", wx._metars(bbox, info) == {} and not info.get("pending") and not asked, (info, asked))
    wx._FAILED[tile] = time.time() - 600
    info = {}
    wx._metars(bbox, info)
    check("after a few minutes it is tried again", info.get("pending") == 1 and asked == [tile], (info, asked))
print("ALL PASS" if ok_all else "SOME FAILED")
