"""Regression test: End Trip pressed OFFLINE, delivered later (app queues it,
uploads the buffered fixes first, then POST /trips/{id}/end {"ended_at": <when
the driver pressed End>}).

  1  back-compat: /end with no body, and with {}  -> normal end at server time
  2  late End, trip still active on the server   -> ended_at = pressed time
  3  sweeper auto-closed it, the backlog reopens it, then the late End
  4  sweeper auto-closed it, backlog inside the 90 s grace (network_lost_resumed),
     then the late End revises the guess to normal; a repeat End changes nothing
  5  sweeper auto-closed it, no backlog at all, late End revises 'uncertain'
  6  clamping: an End time in the future / before the start
  7  a cancelled trip still answers 409

Runs the sweeper IN-PROCESS (local code, live DB, qa_ trips only). Point QA_BASE
at a server running the code under test (local uvicorn with LIFECYCLE_SWEEP=0)."""
import os
import sys
from datetime import datetime, timedelta, timezone

from qa_lib import ROOT, api, iso, load_ledger, save_ledger, sb, utc_now

sys.path.insert(0, str(ROOT / "fleet-management"))
os.environ.setdefault("LIFECYCLE_SWEEP", "0")
import trip_lifecycle  # noqa: E402

led = load_ledger()
o = led["orgs"]["acme-bus-co"]
RESULTS = []


def login(u):
    return api("POST", "/auth/login", body={"login": f"{u['username']}@acme-bus-co", "password": u["password"]})[1]["access_token"]


TOK = login(o["drv2"])
MT = login(o["manager"])


def pdt(v):
    return datetime.fromisoformat(str(v).replace("Z", "+00:00")) if v else None


def same(a, b, tol=1.5):
    return a is not None and b is not None and abs((a - b).total_seconds()) <= tol


def check(name, ok, info=""):
    RESULTS.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{info}]" if info else ""))


def fresh_trip(minutes_ago):
    for t in sb("GET", f"trips?select=id&driver_id=eq.{o['drv2']['id']}&status=eq.active"):
        api("POST", f"/trips/{t['id']}/cancel", MT)
    c, d, _ = api("POST", "/trips/start", TOK, {"assignment_id": o["asg_drv2"]["id"]})
    trip = (d.get("trip") or d).get("id")
    o.setdefault("trips_extra", []).append(trip)
    save_ledger(led)
    started = utc_now() - timedelta(minutes=minutes_ago)
    sb("PATCH", f"trips?id=eq.{trip}", {"started_at": iso(started)})
    return trip, started


T0 = utc_now()


def fixes(trip, times):
    """Post one batch of fixes (as the app's buffer flush does). The position is
    a function of the time (~100 m per minute, northwards), so every batch is
    physically consistent with the previous one."""
    batch = [{"lat": 30.0300 + 0.0009 * ((t - T0).total_seconds() / 60 + 120), "lng": 31.4700, "speed": 1.7,
              "recorded_at": iso(t), "battery": 80, "net_state": "offline"} for t in times]
    return api("POST", f"/trips/{trip}/pings", TOK, batch)


def went_silent_at(trip, t_off):
    """Make the server believe nothing ARRIVED after t_off (arrival = created_at)."""
    for p in sb("GET", f"location_pings?select=id,recorded_at&trip_id=eq.{trip}"):
        sb("PATCH", f"location_pings?id=eq.{p['id']}", {"created_at": p["recorded_at"]})
    sb("PATCH", f"trips?id=eq.{trip}", {"last_signal_at": iso(t_off), "last_heartbeat_at": None})


def row(trip):
    return sb("GET", f"trips?select=status,started_at,ended_at,end_reason,end_detail&id=eq.{trip}")[0]


def ended_alerts(trip):
    return sb("GET", f"alerts?select=id,occurred_at,detail,meta&trip_id=eq.{trip}&type=eq.trip_ended")


def status_seen_by_app(trip):
    return api("GET", f"/trips/{trip}/status", TOK)[1].get("status")


def main():
    R = utc_now()
    mins = lambda m: R + timedelta(minutes=m)  # noqa: E731

    # 1 ── back-compat (current APK sends {}; scripts send nothing)
    for label, body in (("no body", None), ("{}", {})):
        trip, _ = fresh_trip(5)
        fixes(trip, [mins(-4), mins(-3)])
        c, d, _ = api("POST", f"/trips/{trip}/end", TOK, body)
        r, al = row(trip), ended_alerts(trip)
        check(f"1 back-compat {label}: normal end at server time",
              c == 200 and r["status"] == "completed" and r["end_reason"] == "normal"
              and abs((pdt(r["ended_at"]) - utc_now()).total_seconds()) < 60
              and len(al) == 1 and "offline" not in al[0]["detail"], f"{c} {r['end_reason']} {r['ended_at']}")

    # 2 ── offline End, delivered 6 min later; the server never closed the trip
    trip, _ = fresh_trip(20)
    fixes(trip, [mins(-20), mins(-19), mins(-18)])                      # online part
    fixes(trip, [mins(-17 + i) for i in range(12)])                     # buffered backlog up to End (-6)
    c, d, _ = api("POST", f"/trips/{trip}/end", TOK, {"ended_at": iso(mins(-6))})
    r, al = row(trip), ended_alerts(trip)
    check("2 late End on an active trip: ended_at = pressed time, normal, 1 log point with the delivery note",
          c == 200 and r["status"] == "completed" and r["end_reason"] == "normal" and same(pdt(r["ended_at"]), mins(-6))
          and len(al) == 1 and same(pdt(al[0]["occurred_at"]), mins(-6)) and "offline; delivered" in al[0]["detail"]
          and (al[0].get("meta") or {}).get("reported_at"), f"{c} {r['ended_at']} {al[0]['detail'][:110] if al else None}")
    check("2 app status watcher sees completed", status_seen_by_app(trip) == "completed")

    # 3 ── silent 50 min -> sweeper auto-closes; the backlog reopens it; then the late End
    trip, _ = fresh_trip(60)
    fixes(trip, [mins(-60 + i) for i in range(11)])                     # online -60 .. -50
    went_silent_at(trip, mins(-50))
    trip_lifecycle.sweep_once(only_trip_ids={trip}, now=R)
    r0 = row(trip)
    check("3 sweeper auto-closed it with a guessed reason", r0["status"] == "completed" and r0["end_reason"] in trip_lifecycle.INFERRED_REASONS,
          f"{r0['status']} {r0['end_reason']} {r0['ended_at']}")
    c, d, _ = fixes(trip, [mins(-49 + i) for i in range(10)])           # backlog -49 .. -40 (End pressed at -40)
    reopened = c == 201 and (d.get("detection") or {}).get("trip_reopened")
    check("3 the uploaded backlog reopened it", reopened and row(trip)["status"] == "active", f"{c} {str(d)[:160]}")
    c, d, _ = api("POST", f"/trips/{trip}/end", TOK, {"ended_at": iso(mins(-40))})
    r, al = row(trip), ended_alerts(trip)
    restored = sb("GET", f"alerts?select=id&trip_id=eq.{trip}&type=eq.connection_restored")
    check("3 late End after the reopen: normal at the pressed time, exactly 1 trip_ended, connection_restored logged",
          c == 200 and r["status"] == "completed" and r["end_reason"] == "normal" and same(pdt(r["ended_at"]), mins(-40))
          and len(al) == 1 and same(pdt(al[0]["occurred_at"]), mins(-40)) and len(restored) == 1
          and (r.get("end_detail") or {}).get("reported_at"), f"{c} {r['end_reason']} {r['ended_at']} alerts={len(al)}")
    check("3 app status watcher sees completed", status_seen_by_app(trip) == "completed")

    # 4 ── auto-closed; backlog inside the 90 s grace -> network_lost_resumed; late End revises it
    trip, _ = fresh_trip(60)
    fixes(trip, [mins(-60 + i) for i in range(11)])
    went_silent_at(trip, mins(-50))
    trip_lifecycle.sweep_once(only_trip_ids={trip}, now=R)
    guessed = pdt(row(trip)["ended_at"])
    c, d, _ = fixes(trip, [mins(-49.5)])
    r1 = row(trip)
    check("4 backlog inside the grace keeps it closed, reason -> network_lost_resumed",
          r1["status"] == "completed" and r1["end_reason"] == "network_lost_resumed", f"{c} {r1['status']} {r1['end_reason']}")
    c, d, _ = api("POST", f"/trips/{trip}/end", TOK, {"ended_at": iso(mins(-49.4))})
    r, al = row(trip), ended_alerts(trip)
    det = r.get("end_detail") or {}
    check("4 late End revises the guess: normal at the pressed time, 1 trip_ended, audit trail kept",
          c == 200 and "Recorded your End Trip" in (d.get("message") or "") and r["end_reason"] == "normal"
          and same(pdt(r["ended_at"]), mins(-49.4)) and len(al) == 1 and same(pdt(al[0]["occurred_at"]), mins(-49.4))
          and det.get("revised_from") == "network_lost_resumed" and same(pdt(det.get("guessed_ended_at")), guessed)
          and "offline; delivered" in al[0]["detail"], f"{c} {d.get('message') if isinstance(d, dict) else d} {r['ended_at']}")
    c, d, _ = api("POST", f"/trips/{trip}/end", TOK, {"ended_at": iso(mins(-30))})
    r2, al2 = row(trip), ended_alerts(trip)
    check("4 a repeat End changes nothing (a real End is never rewritten)",
          c == 200 and "already completed" in (d.get("message") or "") and r2["ended_at"] == r["ended_at"] and len(al2) == 1)

    # 5 ── auto-closed, nothing buffered at all, late End revises 'uncertain'
    trip, _ = fresh_trip(60)
    fixes(trip, [mins(-60 + i) for i in range(11)])
    went_silent_at(trip, mins(-50))
    trip_lifecycle.sweep_once(only_trip_ids={trip}, now=R)
    prev = row(trip)["end_reason"]
    c, d, _ = api("POST", f"/trips/{trip}/end", TOK, {"ended_at": iso(mins(-45))})
    r, al = row(trip), ended_alerts(trip)
    check("5 late End with no backlog revises the inferred reason to normal",
          c == 200 and prev in trip_lifecycle.INFERRED_REASONS and r["end_reason"] == "normal"
          and same(pdt(r["ended_at"]), mins(-45)) and len(al) == 1
          and (r.get("end_detail") or {}).get("revised_from") == prev, f"{prev} -> {r['end_reason']} {r['ended_at']}")

    # 6 ── clamping
    trip, started = fresh_trip(5)
    c, d, _ = api("POST", f"/trips/{trip}/end", TOK, {"ended_at": iso(mins(60))})
    r = row(trip)
    check("6 End time in the future is clamped to the server's now",
          c == 200 and abs((pdt(r["ended_at"]) - utc_now()).total_seconds()) < 60, r["ended_at"])
    trip, started = fresh_trip(5)
    c, d, _ = api("POST", f"/trips/{trip}/end", TOK, {"ended_at": iso(mins(-120))})
    r = row(trip)
    check("6 End time before the start is clamped to started_at", c == 200 and same(pdt(r["ended_at"]), pdt(r["started_at"])),
          f"{r['ended_at']} vs {r['started_at']}")

    # 7 ── cancelled stays 409
    trip, _ = fresh_trip(5)
    api("POST", f"/trips/{trip}/cancel", MT)
    c, d, _ = api("POST", f"/trips/{trip}/end", TOK, {"ended_at": iso(mins(-1))})
    check("7 cancelled trip -> 409", c == 409, str(c))

    bad = [n for n, ok in RESULTS if not ok]
    print(f"\nRESULT {'PASS' if not bad else 'FAIL'}  {len(RESULTS) - len(bad)}/{len(RESULTS)}")
    sys.exit(0 if not bad else 1)


if __name__ == "__main__":
    main()
