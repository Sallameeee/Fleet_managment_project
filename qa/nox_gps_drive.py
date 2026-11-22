"""Scripted mock GPS for NoxPlayer via the Appium Settings LocationService.
Drives back and forth ALONG THE ROAD GEOMETRY of the qa_ university route (as
stored in the DB), at a realistic speed, one fix every 5 s, for --minutes.
Every sent point is logged (UTC) to qa/out/gps_<tag>.csv.

Usage: python qa/nox_gps_drive.py --minutes 20 --tag s2b [--kmh 30]
"""
import argparse
import csv
import math
import subprocess
import time
from datetime import datetime, timezone

from qa_lib import OUT, load_ledger, sb

ADB = r"D:\Program Files\Nox\bin\nox_adb.exe"
SERIAL = "127.0.0.1:62001"


def hav(a, b):
    R = 6371000.0
    la1, lo1, la2, lo2 = map(math.radians, (a[1], a[0], b[1], b[0]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def set_location(lat, lng, speed_mps):
    cmd = (f"am startservice --user 0 -n io.appium.settings/.LocationService "
           f"--es longitude {lng:.7f} --es latitude {lat:.7f} --es altitude 70 --es speed {speed_mps:.2f}")
    subprocess.run([ADB, "-s", SERIAL, "shell", cmd], capture_output=True, timeout=20)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--tag", default="run")
    ap.add_argument("--kmh", type=float, default=30)
    a = ap.parse_args()
    route_id = load_ledger()["orgs"]["acme-bus-co"]["route"]["id"]
    coords = sb("GET", f"routes?select=geometry&id=eq.{route_id}")[0]["geometry"]["coordinates"]  # [lng, lat]
    cum = [0.0]
    for p, q in zip(coords, coords[1:]):
        cum.append(cum[-1] + hav(p, q))
    total = cum[-1]

    def at(dist):
        dist = max(0.0, min(total, dist))
        k = next((i for i in range(1, len(cum)) if cum[i] >= dist), len(cum) - 1)
        p, q = coords[k - 1], coords[k]
        f = (dist - cum[k - 1]) / ((cum[k] - cum[k - 1]) or 1.0)
        return p[1] + (q[1] - p[1]) * f, p[0] + (q[0] - p[0]) * f

    v = a.kmh / 3.6
    t_end = time.time() + a.minutes * 60
    pos, direction = 0.0, 1
    with (OUT / f"gps_{a.tag}.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["utc", "lat", "lng", "speed_mps"])
        while time.time() < t_end:
            lat, lng = at(pos)
            set_location(lat, lng, v)
            w.writerow([datetime.now(timezone.utc).isoformat(), f"{lat:.7f}", f"{lng:.7f}", f"{v:.2f}"])
            fh.flush()
            pos += direction * v * 5
            if pos >= total or pos <= 0:
                direction *= -1
                pos = max(0.0, min(total, pos))
            time.sleep(5)


if __name__ == "__main__":
    main()
