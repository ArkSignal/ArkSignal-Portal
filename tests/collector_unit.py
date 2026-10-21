import os
os.environ["DISABLE_BACKUP_SCHEDULER"]="1"; os.environ["HUB_URL"]=""
from datetime import datetime, timedelta, timezone
import psycopg
from psycopg.rows import dict_row
from app import create_app
from app import collector as c
from app.settings import set_setting
app=create_app()
ok_all=True
def check(n, cond, extra=""):
    global ok_all; print(("PASS " if cond else "FAIL ")+n, "" if cond else extra); ok_all &= bool(cond)
# --- parsing
doc={"clients":[
 ["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee","   1.2.3.4 port 5000",1.0,100,10.0,2.0,0,20,50],
 ["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee","   1.2.3.4 port 5001",1.0,10,10.0,2.0,0,20,5],
 ["56927e54-29e0-fa04-0000-000000000000","   127.0.0.1 port 1",0,5,0,0,0,0,0],
 ["not-a-uuid","   9.9.9.9 port 1",0,5,0,0,0,0,0],
 ["11111111-2222-3333-4444-555555555555","   127.0.0.1 port 1",0,5,0,0,0,0,0],
 ["22222222-3333-4444-5555-666666666666","   2001:db8::1 port 3000",2.0,60,5.0,1.0,0,30,60],
 ["short"]]}
rows=c.parse_clients(doc)
check("parse: keeps 2 real feeders", len(rows)==2, [r["uuid"] for r in rows])
a=[r for r in rows if r["uuid"].startswith("aaaa")][0]
check("parse: duplicate UUID keeps newest connection", a["conn"]==10 and a["positions"]==5)
check("parse: ipv6 host extracted", any(r["ip"]=="2001:db8::1" for r in rows))
# --- record
F="99999999-aaaa-bbbb-cccc-dddddddddddd"
url=app.config["DATABASE_URL"]
t0=datetime.now(timezone.utc)-timedelta(hours=1)
def row(conn_s, positions, msgs_total, ip="10.0.0.1"):
    return {"uuid":F,"ip":ip,"kbit":5.0,"conn":conn_s,"msgs":msgs_total/conn_s,"pos":positions/conn_s,"rtt":20.0,"positions":positions}
with psycopg.connect(url, row_factory=dict_row) as conn:
    conn.execute("DELETE FROM feeders WHERE uuid=%s",(F,))
    c.record(conn,[row(100,1000,10000)],t0,timedelta(seconds=90))
    c.record(conn,[row(130,1300,13000)],t0+timedelta(seconds=30),timedelta(seconds=90))
    s=conn.execute("SELECT msg_per_s,pos_per_s FROM feeder_samples WHERE feeder_uuid=%s ORDER BY ts DESC LIMIT 1",(F,)).fetchone()
    check("rates derived from deltas (pos 10/s, msg 100/s)", abs(s["pos_per_s"]-10)<0.01 and abs(s["msg_per_s"]-100)<0.5, dict(s))
    first=conn.execute("SELECT pos_per_s FROM feeder_samples WHERE feeder_uuid=%s ORDER BY ts LIMIT 1",(F,)).fetchone()
    check("first sample uses connection averages", abs(first["pos_per_s"]-10)<0.01, dict(first))
    f=conn.execute("SELECT online FROM feeders WHERE uuid=%s",(F,)).fetchone(); check("online after polls", f["online"])
    c.record(conn,[],t0+timedelta(seconds=60),timedelta(seconds=90))
    check("still online after 30s gap (under 90s)", conn.execute("SELECT online FROM feeders WHERE uuid=%s",(F,)).fetchone()["online"])
    c.record(conn,[],t0+timedelta(seconds=200),timedelta(seconds=90))
    check("offline after 90s+ absent", not conn.execute("SELECT online FROM feeders WHERE uuid=%s",(F,)).fetchone()["online"])
    ev=[e["event"] for e in conn.execute("SELECT event FROM feeder_events WHERE feeder_uuid=%s ORDER BY id",(F,)).fetchall()]
    check("events: first_seen, connect, disconnect", ev==["first_seen","connect","disconnect"], ev)
    c.record(conn,[row(5,50,500,"10.0.0.2")],t0+timedelta(seconds=300),timedelta(seconds=90))
    ev=[(e["event"],e["detail"]) for e in conn.execute("SELECT event, detail FROM feeder_events WHERE feeder_uuid=%s ORDER BY id",(F,)).fetchall()]
    check("reconnect logs connect and ip_change", ev[-2:]==[("connect","10.0.0.2"),("ip_change","10.0.0.1 -> 10.0.0.2")] or ("ip_change","10.0.0.1 -> 10.0.0.2") in ev, ev)
    new=conn.execute("SELECT pos_per_s FROM feeder_samples WHERE feeder_uuid=%s ORDER BY ts DESC LIMIT 1",(F,)).fetchone()
    check("new connection (counters reset) doesn't produce negative rate", new["pos_per_s"]>=0, dict(new))
    # rollup: old raw samples become hourly rows
    old=datetime.now(timezone.utc)-timedelta(days=40)
    for i in range(6):
        conn.execute("INSERT INTO feeder_samples (feeder_uuid, ts, msg_per_s, pos_per_s) VALUES (%s,%s,%s,%s)",(F,old+timedelta(minutes=i*5),100+i,10+i))
    conn.commit()
with app.app_context():
    set_setting("collector_last_rollup","")
c.rollup_daily(app)
with psycopg.connect(url, row_factory=dict_row) as conn:
    h=conn.execute("SELECT samples, msg_per_s, pos_per_s FROM feeder_hourly WHERE feeder_uuid=%s",(F,)).fetchall()
    check("rollup created hourly rows with averages", len(h)>=1 and sum(x["samples"] for x in h)==6 and abs(h[0]["pos_per_s"]-12.5)<0.6, [dict(x) for x in h])
    check("rollup removed old raw samples", conn.execute("SELECT COUNT(*) AS n FROM feeder_samples WHERE feeder_uuid=%s AND ts < now() - interval '30 days'",(F,)).fetchone()["n"]==0)
    conn.execute("DELETE FROM feeders WHERE uuid=%s",(F,)); conn.commit()
    check("deleting a feeder cascades its data", conn.execute("SELECT COUNT(*) AS n FROM feeder_events WHERE feeder_uuid=%s",(F,)).fetchone()["n"]==0)
print("ALL PASS" if ok_all else "SOME FAILED")
