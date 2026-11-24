"""Regression test: a trip auto-closed by the sweeper for SILENCE must still read
'active' on GET /trips/{id}/status (driver app watcher), so a long-offline phone
does not reset and drop its buffer; its next newer fix reopens the trip.

Runs the sweeper IN-PROCESS with a clock 40 min ahead (local code, live DB, qa
trip only) to auto-close the trip, then checks the API under QA_BASE."""
import sys
from datetime import timedelta
from pathlib import Path

from qa_lib import S_SLUG, U_SLUG, ROOT, api, iso, load_ledger, save_ledger, sb, utc_now

sys.path.insert(0, str(ROOT / "fleet-management"))
import os  # noqa: E402

os.environ.setdefault("LIFECYCLE_SWEEP", "0")
import trip_lifecycle  # noqa: E402

led = load_ledger()
o = led["orgs"][U_SLUG]


def main():
    c, d, _ = api("POST", "/auth/login", body={"login": f"{o['drv2']['username']}@{U_SLUG}", "password": o["drv2"]["password"]})
    tok = d["access_token"]
    mt = api("POST", "/auth/login", body={"login": f"{o['manager']['username']}@{U_SLUG}", "password": o["manager"]["password"]})[1]["access_token"]
    for t in sb("GET", f"trips?select=id&driver_id=eq.{o['drv2']['id']}&status=eq.active"):
        api("POST", f"/trips/{t['id']}/cancel", mt)
    c, d, _ = api("POST", "/trips/start", tok, {"assignment_id": o["asg_drv2"]["id"]})
    trip = (d.get("trip") or d).get("id")
    o.setdefault("trips_extra", []).append(trip)
    save_ledger(led)
    api("POST", f"/trips/{trip}/pings", tok, {"lat": 30.0300, "lng": 31.4700, "speed": 5, "recorded_at": iso(utc_now()),
                                               "battery": 80, "net_state": "online"})
    out = trip_lifecycle.sweep_once(only_trip_ids={trip}, now=utc_now() + timedelta(minutes=40))
    row = sb("GET", f"trips?select=status,end_reason,ended_at&id=eq.{trip}")[0]
    print("after sweep (clock +40 min):", out, row["status"], row["end_reason"])
    c, st, _ = api("GET", f"/trips/{trip}/status", tok)
    print("GET /status as driver:", c, st)
    ok_status = c == 200 and st.get("status") == "active"
    # the phone comes back with a NEWER fix -> the server reopens the trip
    # newer than ended_at + the 90 s grace (as any real post-outage fix is); still
    # inside the server's 10 min clock-skew tolerance so it is not tagged 'future'
    api("POST", f"/trips/{trip}/pings", tok, {"lat": 30.0305, "lng": 31.4706, "speed": 5,
                                               "recorded_at": iso(utc_now() + timedelta(minutes=3)), "battery": 79, "net_state": "online"})
    row2 = sb("GET", f"trips?select=status,end_reason&id=eq.{trip}")[0]
    ended_alerts = sb("GET", f"alerts?select=id&trip_id=eq.{trip}&type=eq.trip_ended")
    print("after newer fix:", row2, "trip_ended alerts:", len(ended_alerts))
    api("POST", f"/trips/{trip}/end", tok)
    ok = ok_status and row["status"] == "completed" and row2["status"] == "active" and not ended_alerts
    print("RESULT", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
