"""Regression test for the false 'Stopped 0.0 min' short_stop: one fix PER REQUEST
(like the live app) dwelling ~84 s inside qa_Stop A (required 1 min) must NOT
raise short_stop, and stop_events must record the real dwell. A second pass that
dwells only ~18 s MUST raise short_stop. Uses qa_drv2_u on QA_BASE."""
import sys
from datetime import timedelta

from qa_lib import API, api, iso, load_ledger, sb, utc_now

led = load_ledger()
o = led["orgs"]["acme-bus-co"]
A = (30.0300, 31.4700)


def main():
    c, d, _ = api("POST", "/auth/login", body={"login": f"{o['drv2']['username']}@acme-bus-co", "password": o["drv2"]["password"]})
    tok = d["access_token"]
    for t in sb("GET", f"trips?select=id&driver_id=eq.{o['drv2']['id']}&status=eq.active"):
        mt = api("POST", "/auth/login", body={"login": f"{o['manager']['username']}@acme-bus-co", "password": o["manager"]["password"]})[1]["access_token"]
        api("POST", f"/trips/{t['id']}/cancel", mt)
    c, d, _ = api("POST", "/trips/start", tok, {"assignment_id": o["asg_drv2"]["id"]})
    trip = (d.get("trip") or d).get("id")
    o.setdefault("trips_extra", []).append(trip)
    print("API", API, "start", c, trip[:8])
    t0 = utc_now() - timedelta(minutes=6)
    # (seconds, metres SOUTH of stop A) — realistic speeds (<= 10 m/s), geofence 75 m.
    seq = [(0, 300), (10, 200), (20, 100)]               # approach (outside)
    seq += [(30 + 6 * k, 0) for k in range(15)]          # inside 30..114 s -> 84 s dwell
    seq += [(125, 90), (135, 190), (150, 300)]           # leave
    seq += [(170, 150), (180, 60), (186, 30), (192, 60), (200, 150)]  # 2nd pass: inside 180..192 (12 s)
    for sec, south_m in seq:
        body = {"lat": A[0] - south_m / 111_000.0, "lng": A[1], "speed": 0.5 if south_m == 0 else 8.0,
                "heading": 0.0, "recorded_at": iso(t0 + timedelta(seconds=sec)), "battery": 90, "net_state": "wifi"}
        api("POST", f"/trips/{trip}/pings", tok, body)          # ONE fix per request
    ev = sb("GET", f"stop_events?select=arrived_at,departed_at,was_short&trip_id=eq.{trip}&order=arrived_at")
    al = sb("GET", f"alerts?select=detail&trip_id=eq.{trip}&type=eq.short_stop&order=occurred_at")
    api("POST", f"/trips/{trip}/end", tok)
    from qa_lib import save_ledger
    save_ledger(led)
    from datetime import datetime as _dt
    dw = [((_dt.fromisoformat(e["departed_at"]) - _dt.fromisoformat(e["arrived_at"])).total_seconds() if e["departed_at"] else None,
           e["was_short"]) for e in ev]
    print("stop_events (dwell_s, was_short):", dw)
    print("short_stop alerts:", [a["detail"] for a in al])
    ok = len(dw) == 2 and dw[0][0] >= 80 and dw[0][1] is False and dw[1][1] is True and len(al) == 1
    print("RESULT", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
