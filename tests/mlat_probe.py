"""Fake mlat-client for the MLAT server: handshake as station <user>, hold the connection, print the server's answer. Usage: python3 mlat_probe.py host port user uuid lat lon seconds"""
import json, socket, sys, time
host, port, user, uuid, lat, lon, secs = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], float(sys.argv[5]), float(sys.argv[6]), int(sys.argv[7])
s = socket.create_connection((host, port), timeout=20)
s.sendall((json.dumps({"version": 3, "user": user, "uuid": uuid, "lat": lat, "lon": lon, "alt": 100, "compress": ["none"], "clock_type": "dump1090",
                       "client_version": "probe", "return_results": False, "privacy": False}) + "\n").encode())
print(s.makefile().readline()[:300])
time.sleep(secs)
