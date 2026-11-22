"""Delete QA fixtures (service role, FK-safe order). DRY-RUN by default; pass
--apply to delete. Targets ONLY:
  * profiles whose username starts with 'qa_' or contains 'qa17' (Sept QA leftovers)
  * routes / vehicles / passengers whose name/bus_number starts with 'qa_' or 'QA-qa17'
  * everything hanging off them (trips + pings/alerts/visits/events/heartbeats/
    performance/attendance, assignments, route_stops)
  * the qa_ super admin and the auth users of all of the above
Also: --trip <id> deletes ONE trip and its dependent rows (e.g. a superseded demo trip).
Prints counts of NON-qa rows before/after so real data is provably untouched.
"""
import argparse
import json

from qa_lib import auth_admin, load_ledger, sb, sb_count

TRIP_CHILDREN = ["location_pings", "alerts", "stop_events", "stop_visits", "trip_heartbeats", "trip_performance", "attendance"]


def ids(path):
    return [r["id"] for r in sb("GET", path)]


def inlist(xs):
    return "(" + ",".join(xs) + ")"


def delete_trip(tid, apply):
    n = {t: sb_count(f"{t}?select=id&trip_id=eq.{tid}") for t in TRIP_CHILDREN}
    print(f"  trip {tid[:8]}: " + ", ".join(f"{k}={v}" for k, v in n.items() if v))
    if apply:
        for t in TRIP_CHILDREN:
            sb("DELETE", f"{t}?trip_id=eq.{tid}")
        sb("DELETE", f"trips?id=eq.{tid}")


def real_counts():
    return {
        "profiles(non-qa)": sb_count("profiles?select=id&username=not.ilike.qa_*"),
        "routes(non-qa)": sb_count("routes?select=id&name=not.ilike.qa_*"),
        "vehicles(non-qa)": sb_count("vehicles?select=id&bus_number=not.ilike.qa_*"),
        "trips(all)": sb_count("trips?select=id"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--trip")
    a = ap.parse_args()
    if a.trip:
        delete_trip(a.trip, a.apply)
        print("APPLIED" if a.apply else "DRY-RUN")
        return
    before = real_counts()
    profs = sb("GET", "profiles?select=id,username,name,org_id,role&or=(username.ilike.qa_*,username.ilike.*qa17*)")
    pids = [p["id"] for p in profs]
    routes = sb("GET", "routes?select=id,name&or=(name.ilike.qa_*,name.ilike.*qa17*)")
    rids = [r["id"] for r in routes]
    vehs = sb("GET", "vehicles?select=id,bus_number&or=(bus_number.ilike.qa_*,bus_number.ilike.*qa17*)")
    vids = [v["id"] for v in vehs]
    pax = sb("GET", "passengers?select=id,name,parent_id&or=(name.ilike.qa_*,name.ilike.*qa17*)")
    trips = []
    if pids:
        trips += ids(f"trips?select=id&driver_id=in.{inlist(pids)}")
    if rids:
        trips += ids(f"trips?select=id&route_id=in.{inlist(rids)}")
    if vids:
        trips += ids(f"trips?select=id&vehicle_id=in.{inlist(vids)}")
    trips = sorted(set(trips))
    asg = []
    for col, xs in (("driver_id", pids), ("route_id", rids), ("vehicle_id", vids)):
        if xs:
            asg += ids(f"assignments?select=id&{col}=in.{inlist(xs)}")
    asg = sorted(set(asg))
    led = load_ledger()
    sa = led.get("super_admin") or {}
    print("PROFILES:", [(p["username"], p["role"]) for p in profs])
    print("ROUTES:", [r["name"] for r in routes])
    print("VEHICLES:", [v["bus_number"] for v in vehs])
    print("PASSENGERS:", [p["name"] for p in pax])
    print(f"TRIPS: {len(trips)}  ASSIGNMENTS: {len(asg)}  SUPER_ADMIN: {sa.get('email')}")
    print("NON-QA COUNTS BEFORE:", json.dumps(before))
    for t in trips:
        delete_trip(t, a.apply)
    if a.apply:
        if pids:
            for t in ("alerts", "location_pings", "trip_heartbeats", "trip_performance"):
                sb("DELETE", f"{t}?driver_id=in.{inlist(pids)}")
            sb("DELETE", f"driver_group_members?driver_id=in.{inlist(pids)}")
            sb("DELETE", f"notification_reads?user_id=in.{inlist(pids)}")
        if asg:
            sb("DELETE", f"assignments?id=in.{inlist(asg)}")
        for p in pax:
            sb("DELETE", f"passengers?id=eq.{p['id']}")
        if rids:
            sb("DELETE", f"route_stops?route_id=in.{inlist(rids)}")
            sb("DELETE", f"routes?id=in.{inlist(rids)}")
        if vids:
            sb("DELETE", f"vehicles?id=in.{inlist(vids)}")
        # passenger/parent auth accounts (qa_pax_*@example.com) have a profile row too
        pax_profiles = sb("GET", "profiles?select=id,email&email=ilike.qa_pax_*")
        for p in profs + pax_profiles:
            sb("DELETE", f"profiles?id=eq.{p['id']}")
            try:
                auth_admin("DELETE", f"users/{p['id']}")
            except Exception as exc:
                print("  auth delete", p["id"][:8], str(exc)[:80])
        if sa.get("id"):
            sb("DELETE", f"super_admins?id=eq.{sa['id']}")
            try:
                auth_admin("DELETE", f"users/{sa['id']}")
            except Exception as exc:
                print("  auth delete super admin", str(exc)[:80])
        left = {
            "profiles": sb_count("profiles?select=id&or=(username.ilike.qa_*,username.ilike.*qa17*,email.ilike.qa_*)"),
            "routes": sb_count("routes?select=id&or=(name.ilike.qa_*,name.ilike.*qa17*)"),
            "vehicles": sb_count("vehicles?select=id&or=(bus_number.ilike.qa_*,bus_number.ilike.*qa17*)"),
            "passengers": sb_count("passengers?select=id&or=(name.ilike.qa_*,name.ilike.*qa17*)"),
            "super_admins": sb_count("super_admins?select=id&email=ilike.qa_*"),
        }
        print("QA ROWS LEFT:", json.dumps(left))
        print("NON-QA COUNTS AFTER:", json.dumps(real_counts()))
    print("APPLIED" if a.apply else "DRY-RUN (nothing deleted)")


if __name__ == "__main__":
    main()
