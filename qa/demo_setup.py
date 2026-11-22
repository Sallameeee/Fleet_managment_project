"""Clean UNIVERSITY demo setup in acme-bus-co (names WITHOUT qa_), through the
real API acting as the org owner. Idempotent: ids are kept in qa/out/demo.json
(local only, gitignored) and reused on re-runs. Credentials are written to
D:\\RouteMind_DEMO_CREDENTIALS.txt (outside both repos).

Creates: a 5-stop road-following route (OSRM geometry), a bus, a driver, a
student (password changed from the shared default so no forced reset on stage),
tomorrow's assignment, and today's assignment for the sample trip.
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from qa_lib import CAIRO, OUT, ROOT, api, evidence, mint_impersonation, password, sb

SLUG = "acme-bus-co"
DEMO = OUT / "demo.json"
CREDS = Path("D:/RouteMind_DEMO_CREDENTIALS.txt")
OSRM = Path(sys.argv[1]) if len(sys.argv) > 1 else None

STOPS = [
    ("Nasr City – Abbas El Akkad", 30.0561, 31.3437, "07:30"),
    ("Nasr City – Makram Ebeid", 30.0610, 31.3610, "07:38"),
    ("Cairo Festival City", 30.0290, 31.4080, "07:55"),
    ("Point 90 Mall", 30.0247, 31.4917, "08:15"),
    ("University Main Gate", 30.0190, 31.5000, "08:22"),
]


def ok(r, what):
    code, d, _ = r
    if code not in (200, 201):
        raise SystemExit(f"FAIL {what}: {code} {str(d)[:300]}")
    return d


def main():
    demo = json.loads(DEMO.read_text(encoding="utf-8")) if DEMO.exists() else {}
    org = sb("GET", f"organizations?select=id&slug=eq.{SLUG}")[0]
    owner = sb("GET", f"profiles?select=id&org_id=eq.{org['id']}&role=eq.owner&limit=1")[0]
    tok = mint_impersonation(owner["id"], org["id"])
    demo["org_id"] = org["id"]

    if "route" not in demo:
        geo = json.loads(OSRM.read_text(encoding="utf-8"))["routes"][0]
        stops = [{"name": n, "lat": la, "lng": ln, "stop_order": i + 1, "dwell_minutes": 1, "arrival_time": t}
                 for i, (n, la, ln, t) in enumerate(STOPS)]
        d = ok(api("POST", "/routes", tok, {"name": "Nasr City → Main Campus", "stops": stops,
                                            "geometry": geo["geometry"], "total_km": round(geo["distance"] / 1000, 1),
                                            "est_minutes": round(geo["duration"] / 60), "start_time": "07:30",
                                            "color": "#2E7D32"}), "route")
        demo["route"] = {"id": d.get("id") or d.get("route", {}).get("id")}
        DEMO.write_text(json.dumps(demo, indent=2), encoding="utf-8")
    if "vehicle" not in demo:
        d = ok(api("POST", "/vehicles", tok, {"bus_number": "BUS-07", "plate_number": "ن ص ر 4721", "capacity": 50}), "vehicle")
        demo["vehicle"] = {"id": d.get("id") or d.get("vehicle", {}).get("id")}
        DEMO.write_text(json.dumps(demo, indent=2), encoding="utf-8")
    if "driver" not in demo:
        pw = "Bus07-" + password()[3:9] + "!"
        d = ok(api("POST", "/drivers", tok, {"name": "Mahmoud Hassan", "username": "mahmoud.hassan", "password": pw,
                                             "phone": "01012345678"}), "driver")
        demo["driver"] = {"id": d.get("id") or d.get("driver", {}).get("id"), "login": f"mahmoud.hassan@{SLUG}", "password": pw}
        DEMO.write_text(json.dumps(demo, indent=2), encoding="utf-8")
    if "manager" not in demo:
        pw = "Campus-" + password()[3:9] + "!"
        d = ok(api("POST", "/users", tok, {"name": "Transport Office", "username": "transport.office", "password": pw,
                                           "role": "manager"}), "manager")
        demo["manager"] = {"id": d.get("id") or d.get("user", {}).get("id"), "login": f"transport.office@{SLUG}", "password": pw}
        DEMO.write_text(json.dumps(demo, indent=2), encoding="utf-8")
    if "student" not in demo:
        email = "youssef.adel@example.com"
        d = ok(api("POST", "/passengers", tok, {"name": "Youssef Adel", "email": email, "route_id": demo["route"]["id"],
                                                "university_id": "2023-1047", "student_phone": "01198765432"}), "student")
        default_pw = d.get("default_password")
        # Log in once with the shared default and set a personal password, so the
        # forced first-login reset never interrupts the demo.
        code, lg, _ = api("POST", "/auth/passenger/login", body={"email": email, "password": default_pw})
        new_pw = "Student-" + password()[3:9] + "!"
        c2, d2, _ = api("POST", "/auth/change-password", lg.get("access_token"),
                        {"current_password": default_pw, "new_password": new_pw})
        if c2 not in (200, 201):
            c2, d2, _ = api("POST", "/auth/change-password", lg.get("access_token"), {"new_password": new_pw})
        demo["student"] = {"email": email, "password": new_pw if c2 in (200, 201) else default_pw,
                           "changed": c2 in (200, 201), "change_resp": str(d2)[:120]}
        DEMO.write_text(json.dumps(demo, indent=2), encoding="utf-8")
    today = datetime.now(CAIRO).date()
    for key, day, start, end in (("asg_today", today, (datetime.now(CAIRO) - timedelta(minutes=2)).strftime("%H:%M"), "23:59"),
                                 ("asg_tomorrow", today + timedelta(days=1), "07:30", "09:30")):
        if key not in demo:
            d = ok(api("POST", "/assignments", tok, {"driver_id": demo["driver"]["id"], "route_id": demo["route"]["id"],
                                                     "vehicle_id": demo["vehicle"]["id"], "trip_date": day.isoformat(),
                                                     "start_time": start, "end_time": end, "shift_label": "Morning"}), key)
            demo[key] = {"id": d.get("id") or d.get("assignment", {}).get("id"), "date": day.isoformat(), "start": start}
            DEMO.write_text(json.dumps(demo, indent=2), encoding="utf-8")
    # credentials file (outside both repos)
    CREDS.write_text(
        "RouteMind demo — University org (acme-bus-co)\n"
        f"Created {datetime.now(CAIRO):%Y-%m-%d %H:%M} Cairo\n\n"
        "Dashboard (managers): https://fleet-managment-project.vercel.app/org-login  (Username + Organization 'acme-bus-co' + Password)\n"
        f"Manager login:     {demo['manager']['login']}  /  {demo['manager']['password']}\n"
        f"Driver app login:  {demo['driver']['login']}  /  {demo['driver']['password']}\n"
        f"Student app login: {demo['student']['email']}  /  {demo['student']['password']}\n"
        f"Route: Nasr City → Main Campus (5 stops), bus BUS-07 (ن ص ر 4721)\n"
        f"Tomorrow's assignment: {demo['asg_tomorrow']['date']} 07:30–09:30 (can be started any time after 07:15)\n",
        encoding="utf-8")
    evidence(f"demo setup: route={demo['route']['id'][:8]} bus={demo['vehicle']['id'][:8]} driver={demo['driver']['login']} "
             f"student={demo['student']['email']} (pw changed={demo['student']['changed']}) "
             f"asg_today={demo['asg_today']['id'][:8]} asg_tomorrow={demo['asg_tomorrow']['id'][:8]}")


if __name__ == "__main__":
    main()
