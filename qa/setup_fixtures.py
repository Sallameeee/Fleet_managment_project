"""Create qa_ fixtures in BOTH orgs through the real API (acting as the org owner
via the same impersonation token super admins use). Every created id goes to the
ledger (qa/out/fixtures.json) so cleanup deletes by id. Idempotent-ish: re-running
reuses fixtures already in the ledger.

Per org: manager (qa_mgr), drivers qa_drv1 / qa_drv2 / qa_drvoff, vehicle,
route (4 stops, New Cairo), 2 assignments for today (Cairo), 1 passenger.
"""
import sys
from datetime import datetime, timedelta

from qa_lib import S_SLUG, U_SLUG, CAIRO, api, evidence, load_ledger, mint_impersonation, password, save_ledger, sb

ORGS = [S_SLUG, U_SLUG]
# New Cairo, ~550 m between stops (lat, lng)
STOPS = [("qa_Stop A", 30.0300, 31.4700), ("qa_Stop B", 30.0335, 31.4745),
         ("qa_Stop C", 30.0370, 31.4790), ("qa_Stop D", 30.0405, 31.4835)]


def ok(code, data, what):
    if code not in (200, 201):
        raise SystemExit(f"FAIL {what}: {code} {str(data)[:300]}")
    return data


def main():
    led = load_ledger()
    led.setdefault("orgs", {})
    for slug in ORGS:
        o = led["orgs"].setdefault(slug, {})
        org = sb("GET", f"organizations?select=id,module,name&slug=eq.{slug}")[0]
        owner = sb("GET", f"profiles?select=id&org_id=eq.{org['id']}&role=eq.owner&limit=1")[0]
        o.update(org_id=org["id"], module=org["module"], owner_id=owner["id"])
        tok = mint_impersonation(owner["id"], org["id"])
        sfx = "s" if org["module"] == "school" else "u"

        if "manager" not in o:
            pw = password()
            d = ok(*api("POST", "/users", tok, {"name": f"qa_ Manager {sfx}", "username": f"qa_mgr_{sfx}",
                                                "password": pw, "role": "manager"})[:2], "create manager")
            o["manager"] = {"id": d.get("id") or d.get("user", {}).get("id"), "username": f"qa_mgr_{sfx}", "password": pw}
            save_ledger(led)
        for key in ("drv1", "drv2", "drvoff"):
            if key not in o:
                pw = password()
                d = ok(*api("POST", "/drivers", tok, {"name": f"qa_ Driver {key} {sfx}", "username": f"qa_{key}_{sfx}",
                                                      "password": pw, "phone": "01000000000"})[:2], "create " + key)
                o[key] = {"id": d.get("id") or d.get("driver", {}).get("id"), "username": f"qa_{key}_{sfx}", "password": pw}
                save_ledger(led)
        for vkey, num in (("vehicle", "1"), ("vehicle2", "2")):
            if vkey not in o:
                d = ok(*api("POST", "/vehicles", tok, {"bus_number": f"qa_BUS{num}_{sfx}" if num == "2" else f"qa_BUS_{sfx}",
                                                       "plate_number": f"QA {sfx.upper()} 12{num}", "capacity": 30})[:2], "create " + vkey)
                o[vkey] = {"id": d.get("id") or d.get("vehicle", {}).get("id")}
                save_ledger(led)
        if "route" not in o:
            stops = [{"name": n, "lat": la, "lng": ln, "stop_order": i + 1, "dwell_minutes": 1}
                     for i, (n, la, ln) in enumerate(STOPS)]
            geom = {"type": "LineString", "coordinates": [[ln, la] for _, la, ln in STOPS]}
            d = ok(*api("POST", "/routes", tok, {"name": f"qa_Route {sfx}", "stops": stops, "geometry": geom,
                                                 "total_km": 1.7, "est_minutes": 10})[:2], "create route")
            o["route"] = {"id": d.get("id") or d.get("route", {}).get("id")}
            save_ledger(led)
        today = datetime.now(CAIRO)
        start = (today - timedelta(minutes=5)).strftime("%H:%M")
        end = (today + timedelta(hours=3)).strftime("%H:%M")
        for key, vkey in (("drv1", "vehicle"), ("drv2", "vehicle2")):
            akey = "asg_" + key
            if akey not in o:
                d = ok(*api("POST", "/assignments", tok, {"driver_id": o[key]["id"], "route_id": o["route"]["id"],
                                                          "vehicle_id": o[vkey]["id"], "trip_date": today.date().isoformat(),
                                                          "start_time": start, "end_time": end,
                                                          "shift_label": "qa_shift"})[:2], "create " + akey)
                o[akey] = {"id": d.get("id") or d.get("assignment", {}).get("id")}
                save_ledger(led)
        if "passenger" not in o:
            email = f"qa_pax_{sfx}@example.com"
            body = {"name": f"qa_ Student {sfx}", "email": email, "route_id": o["route"]["id"]}
            if org["module"] == "university":
                body["university_id"] = "QA-0001"
            code, d, _ = api("POST", "/passengers", tok, body)
            ok(code, d, "create passenger")
            o["passenger"] = {"id": d.get("id") or (d.get("passenger") or {}).get("id"), "email": email,
                              "default_password": d.get("default_password") or "", "raw_keys": sorted(d.keys())}
            save_ledger(led)
        evidence(f"fixtures {slug} ({org['module']}): mgr={o['manager']['username']} drivers=qa_drv1/2/off_{sfx} "
                 f"vehicle={o['vehicle']['id'][:8]} route={o['route']['id'][:8]} asg={o['asg_drv1']['id'][:8]},{o['asg_drv2']['id'][:8]} "
                 f"pax={o['passenger']['email']}")
    save_ledger(led)


if __name__ == "__main__":
    sys.exit(main())
