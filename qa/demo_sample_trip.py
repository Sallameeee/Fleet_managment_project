"""Drive ONE realistic finished sample trip for the university demo, in REAL TIME,
through the same API calls the driver app makes: real login as the demo driver,
start today's demo assignment, a fix every 5 s along the road geometry
(30 km/h in town, 65 km/h on the main road), a heartbeat every 60 s, stop
arrival/departure visits with an 80 s dwell, then End Trip at the campus gate.
Runs ~45 min; progress -> qa/out/sample_trip.log.
"""
import json
import math
import sys
import time
from datetime import datetime, timezone

from qa_lib import OUT, api, iso, sb

demo = json.loads((OUT / "demo.json").read_text(encoding="utf-8"))
LOG = OUT / "sample_trip.log"


def log(msg):
    line = f"{datetime.now(timezone.utc).isoformat()[:19]}Z {msg}"
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def hav(a, b):
    R = 6371000.0
    la1, lo1, la2, lo2 = map(math.radians, (a[1], a[0], b[1], b[0]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def main():
    route = sb("GET", f"routes?select=geometry,total_km&id=eq.{demo['route']['id']}")[0]
    coords = route["geometry"]["coordinates"]  # [lng, lat]
    stops = sb("GET", f"route_stops?select=id,name,lat,lng,stop_order&route_id=eq.{demo['route']['id']}&order=stop_order")
    cum = [0.0]
    for a, b in zip(coords, coords[1:]):
        cum.append(cum[-1] + hav(a, b))
    total = cum[-1]
    # position of each stop along the line (nearest vertex)
    stop_pos = []
    for s in stops:
        i = min(range(len(coords)), key=lambda k: hav(coords[k], (s["lng"], s["lat"])))
        stop_pos.append(cum[i])

    def at(dist):
        dist = max(0.0, min(total, dist))
        k = next(i for i in range(1, len(cum)) if cum[i] >= dist) if dist < total else len(cum) - 1
        a, b = coords[k - 1], coords[k]
        seg = cum[k] - cum[k - 1] or 1.0
        f = (dist - cum[k - 1]) / seg
        lng, lat = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
        heading = (math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) + 360) % 360
        return lat, lng, heading

    code, d, _ = api("POST", "/auth/login", body={"login": demo["driver"]["login"], "password": demo["driver"]["password"]})
    if code != 200:
        log(f"login failed {code} {d}")
        sys.exit(1)
    tok, refresh = d["access_token"], d.get("refresh_token")
    code, d, _ = api("POST", "/trips/start", tok, {"assignment_id": demo["asg_today"]["id"]})
    trip = (d.get("trip") or d).get("id") if isinstance(d, dict) else None
    log(f"start {code} trip={trip} route_km={total/1000:.1f}")
    if not trip:
        sys.exit(1)
    demo["sample_trip"] = {"id": trip}
    (OUT / "demo.json").write_text(json.dumps(demo, indent=2), encoding="utf-8")

    dist, battery, last_hb, last_refresh = 0.0, 92.0, 0.0, time.time()
    next_stop = 0
    visited = {}
    backlog = []  # like the app: fixes that failed to upload are kept and resent (original timestamps)

    def call(method, path, body=None, tries=6):
        """api() that survives a backend restart/timeout (a deploy) like the app does."""
        for k in range(tries):
            try:
                return api(method, path, tok, body)
            except Exception as exc:
                log(f"{method} {path.split('/')[-1]} network error ({type(exc).__name__}); retry {k + 1}")
                time.sleep(5)
        return 0, None, 0

    def send(lat, lng, heading, speed):
        nonlocal tok
        body = {"lat": round(lat, 7), "lng": round(lng, 7), "speed": round(speed, 2), "heading": round(heading, 1),
                "recorded_at": iso(datetime.now(timezone.utc)), "battery": int(battery), "net_state": "mobile"}
        backlog.append(body)
        c, r, _ = call("POST", f"/trips/{trip}/pings", list(backlog), tries=1)
        if c == 401 and refresh:
            c2, r2, _ = api("POST", "/auth/refresh", body={"refresh_token": refresh})
            if c2 == 200:
                tok = r2["access_token"]
                c, r, _ = call("POST", f"/trips/{trip}/pings", list(backlog), tries=1)
        if c in (200, 201):
            backlog.clear()
        else:
            log(f"ping {c} {str(r)[:120]} (backlog {len(backlog)})")

    def visit(i, arrival=None, departure=None):
        body = {"stop_id": stops[i]["id"], "arrival_time": iso(arrival)}
        if departure:
            body["departure_time"] = iso(departure)
        c, r, _ = call("POST", f"/trips/{trip}/stop-visits", body)
        log(f"stop-visit {stops[i]['name']} {'dep' if departure else 'arr'} {c}")

    def dwell(i, seconds):
        arr = datetime.now(timezone.utc)
        visit(i, arrival=arr)
        lat, lng, h = at(stop_pos[i])
        t_end = time.time() + seconds
        while time.time() < t_end:
            send(lat, lng, h, 0.0)
            time.sleep(5)
        visit(i, arrival=arr, departure=datetime.now(timezone.utc))

    # first stop: board for 80 s, then depart
    dwell(0, 80)
    next_stop = 1
    while dist < total:
        town = dist < 4500 or dist > total - 4000
        v = (30 if town else 65) / 3.6
        dist = min(total, dist + v * 5)
        if next_stop < len(stops) and dist >= stop_pos[next_stop]:
            dist = stop_pos[next_stop]
            lat, lng, h = at(dist)
            send(lat, lng, h, v * 0.3)
            if next_stop == len(stops) - 1:
                visit(next_stop, arrival=datetime.now(timezone.utc))
                break
            dwell(next_stop, 80)
            next_stop += 1
            continue
        lat, lng, h = at(dist)
        send(lat, lng, h, v)
        battery = max(15.0, battery - 0.02)
        if time.time() - last_hb >= 60:
            call("POST", f"/trips/{trip}/heartbeat", {"sent_at": iso(datetime.now(timezone.utc)), "battery": int(battery), "net_state": "mobile"})
            last_hb = time.time()
            log(f"progress {dist/1000:.1f}/{total/1000:.1f} km")
        time.sleep(5)
    time.sleep(5)
    if backlog:
        call("POST", f"/trips/{trip}/pings", list(backlog))
    code, d, _ = call("POST", f"/trips/{trip}/end")
    log(f"end {code} {str(d)[:160]}")


if __name__ == "__main__":
    main()
