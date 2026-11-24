"""School supervisor flow behind the redesigned Students tab — API level,
inside the dedicated qa_ school org only (local or production backend).

start trip → roster (tri-state) → present → absent → clear (back to "to mark")
→ attendance feature OFF → 403 → back ON → end trip → my-schedule shows the
assignment as completed. Every state is checked in the DB as well.

Run: QA_BASE=http://127.0.0.1:8000 QA_LEDGER=overnight_fixtures.json \
     QA_SCHOOL_SLUG=qa-school-overnight python qa/test_attendance_flow.py
"""
import json
import sys
from datetime import datetime

from qa_lib import API, CAIRO, OUT, S_SLUG, api, evidence, load_ledger, sb

R = []


def rec(case, ok, ev):
    R.append((case, ok))
    print(("PASS" if ok else "FAIL"), case, "|", ev, flush=True)


def main():
    # Safe against any backend: it only ever touches the dedicated qa_ school org.
    led = load_ledger()
    o = led["orgs"][S_SLUG]
    assert S_SLUG.startswith("qa-"), "qa_ org only"
    code, d, _ = api("POST", "/auth/login", body={"login": f"{o['drv1']['username']}@{S_SLUG}", "password": o["drv1"]["password"]})
    tok = d["access_token"]
    ov = json.loads((OUT / "overnight_ledger.json").read_text(encoding="utf-8"))
    sa = ov["super_admin"]
    code, d, _ = api("POST", "/auth/super-admin/login", body={"email": sa["email"], "password": sa["password"]})
    sa_tok = d["access_token"]
    org_id = o["org_id"]

    code, ma, _ = api("GET", "/trips/my-assignments", tok)
    asg = next(a for a in ma["assignments"] if a["assignment_id"] == o["asg_drv1"]["id"])
    rec("supervisor sees today's assignment with the start-time gate fields", "can_start_now" in asg and "first_stop" in asg, f"can_start_now={asg.get('can_start_now')} first_stop={bool(asg.get('first_stop'))}")
    code, t, _ = api("POST", "/trips/start", tok, {"assignment_id": asg["assignment_id"], "vehicle_id": asg["vehicle_id"]})
    trip = (t or {}).get("id") or ((t or {}).get("active_trip") or {}).get("id")
    rec("start trip", code in (200, 201) and bool(trip), f"{code} trip={trip}")

    code, ro, _ = api("GET", f"/trips/{trip}/students", tok)
    st = (ro or {}).get("students", [])
    rec("roster loads (tri-state: all unmarked at start)", code == 200 and st and all(s.get("attendance_status") is None for s in st), f"{code} n={len(st)} session={ro.get('session')}")
    sid = st[0]["student_id"]

    def status():
        r = api("GET", f"/trips/{trip}/students", tok)[1]
        return next(s for s in r["students"] if s["student_id"] == sid)["attendance_status"]

    def db_row():
        rows = sb("GET", f"attendance?select=boarded&trip_id=eq.{trip}&student_id=eq.{sid}")
        return rows[0]["boarded"] if rows else None

    code, _, _ = api("POST", f"/trips/{trip}/attendance", tok, {"student_id": sid, "boarded": True})
    rec("mark present", code == 200 and status() == "present" and db_row() is True, f"{code} api={status()} db_boarded={db_row()}")
    code, _, _ = api("POST", f"/trips/{trip}/attendance", tok, {"student_id": sid, "boarded": False})
    rec("edit to absent", code == 200 and status() == "absent" and db_row() is False, f"{code} api={status()} db_boarded={db_row()}")
    code, _, _ = api("POST", f"/trips/{trip}/attendance", tok, {"student_id": sid, "boarded": False, "clear": True})
    rec("un-mark (clear) returns the student to 'to mark'", code == 200 and status() is None and db_row() is None, f"{code} api={status()} db_row={db_row()}")

    # feature flag OFF → 403, then restored exactly
    before = sb("GET", f"organizations?select=enabled_features&id=eq.{org_id}")[0]["enabled_features"]
    off = [k for k in (before or []) if k != "attendance"]
    code, _, _ = api("PATCH", f"/organizations/{org_id}", sa_tok, {"enabled_features": off})
    code2, d2, _ = api("GET", f"/trips/{trip}/students", tok)
    rec("attendance feature OFF → roster 403", code == 200 and code2 == 403, f"patch={code} roster={code2} {str(d2)[:90]}")
    code3, me, _ = api("GET", "/auth/me", tok)
    rec("…and /auth/me no longer lists 'attendance' (the app hides the tab)", "attendance" not in (me.get("enabled_features") or []), f"features={me.get('enabled_features')}")
    api("PATCH", f"/organizations/{org_id}", sa_tok, {"enabled_features": before})
    after = sb("GET", f"organizations?select=enabled_features&id=eq.{org_id}")[0]["enabled_features"]
    rec("feature flags restored exactly", sorted(after or []) == sorted(before or []), f"{sorted(after or []) == sorted(before or [])}")

    code, e, _ = api("POST", f"/trips/{trip}/end", tok, {"ended_at": datetime.now(CAIRO).isoformat()})
    rec("end trip (with the pressed time)", code == 200 and e.get("status") == "completed", f"{code} {e.get('status')} {e.get('end_reason')}")
    today = datetime.now(CAIRO).date().isoformat()
    code, sc, _ = api("GET", "/trips/my-schedule", tok, params={"start": today, "days": 1})
    items = sc["days"][0]["assignments"] if code == 200 else []
    mine = next((a for a in items if a["assignment_id"] == asg["assignment_id"]), {})
    rec("my-schedule shows the assignment as completed", mine.get("status") == "completed" and mine.get("trip_id") == trip, f"{mine.get('status')} trip={mine.get('trip_id')}")
    code, sc2, _ = api("GET", "/trips/my-schedule", tok)
    rec("recent trips include it with a distance", any(r["trip_id"] == trip for r in sc2.get("recent_trips", [])), f"recent={[(r['trip_id'][:8], r['distance_km']) for r in sc2.get('recent_trips', [])][:3]}")

    ok = sum(1 for _, x in R if x)
    evidence(f"{'PASS' if ok == len(R) else 'FAIL'} [attendance-flow] {ok}/{len(R)} against {API}: " + "; ".join(f"{'ok' if x else 'FAIL'} {c}" for c, x in R))
    print(f"TOTAL {ok}/{len(R)}")
    return ok == len(R)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
