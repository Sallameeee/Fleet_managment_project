"""DEV-ONLY: serve the real backend app on an IN-MEMORY database, seeded through
the real pipeline, so the dashboard's Trip debug / Full View can be looked at
without touching production. Never deploy this; it accepts any token as a
qa_ admin.

Seeds (qa_ org, university):
  * a synthetic realistic 6th of October -> El Mahalla run over the real
    `test22` stops (read-only GET from production for the stop coordinates)
  * the REAL heartbeat timeline of the 24 Nov trip e491bc37 with its zero fixes
  * a LIVE trip that keeps sending heartbeats but no GPS (online_no_gps, amber)

Usage: python qa/debug_view_server.py [port]   (default 8765)
"""

from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "qa"))
sys.path.insert(0, str(ROOT / "fleet-management"))
sys.path.insert(0, str(ROOT / "fleet-management" / "tests"))
os.environ["LIFECYCLE_SWEEP"] = "0"

import qa_lib as q  # noqa: E402  (READ-ONLY: GET)
import fake_supabase  # noqa: E402

DB = fake_supabase.install()

import auth  # noqa: E402
import gps_sim as g  # noqa: E402
import main  # noqa: E402
import trip_lifecycle  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ORG = "9a000000-0000-4000-8000-0000000000a1"
ADMIN = {"id": "9a000000-0000-4000-8000-00000000ad01", "org_id": ORG, "role": "owner", "permissions": {},
         "is_active": True, "name": "qa_ Debug Admin", "username": "qa_debug_admin"}
DRIVERS = {f"d{i}": {"id": f"9a000000-0000-4000-8000-00000000d00{i}", "org_id": ORG, "role": "driver", "permissions": {},
                     "is_active": True, "name": f"qa_ Driver {i}"} for i in (1, 2, 3)}
PROFILES = {"admin": ADMIN, **DRIVERS}


def _user(request: Request) -> dict:
    who = request.headers.get("x-qa-user", "admin")
    return PROFILES.get(who, ADMIN)


main.app.dependency_overrides[auth.get_current_user] = _user
auth._verify_token = lambda creds: __import__("types").SimpleNamespace(id=PROFILES.get(creds.credentials, ADMIN)["id"])
C = TestClient(main.app)


def H(who):
    return {"x-qa-user": who, "Authorization": f"Bearer {who}"}


def GET(path):
    r = q.client.get(q.SB_URL + "/rest/v1/" + path, headers=q._SB_H)
    r.raise_for_status()
    return r.json()


def seed():
    stops = GET("route_stops?route_id=eq.2777a6db-ee1e-40bd-8408-647b4b40fda6&select=stop_order,name,lat,lng,dwell_minutes&order=stop_order")
    route = "9a000000-0000-4000-8000-0000000000r1"
    t = DB.tables
    t["organizations"] = [{"id": ORG, "name": "qa_ Debug University", "slug": "qa-debug-uni", "module": "university",
                           "status": "active", "log_settings": {}, "enabled_features": None, "long_stop_minutes": 5}]
    t["profiles"] = [dict(p) for p in PROFILES.values()]
    t["routes"] = [{"id": route, "org_id": ORG, "name": "qa_ 6th of October → El Mahalla", "geometry": None}]
    t["route_stops"] = [{"id": f"9a-stop-{s['stop_order']}", "route_id": route, **s} for s in stops]
    t["vehicles"] = [{"id": f"9a-veh-{i}", "org_id": ORG, "bus_number": f"QA-{i}", "share_token": f"qa{i}"} for i in (1, 2, 3)]
    for name in ("alerts", "stop_events", "stop_visits", "location_pings", "trip_heartbeats", "trip_events", "assignments", "alert_rules"):
        t[name] = []
    now = datetime.now(timezone.utc)
    pts = [(s["lat"], s["lng"]) for s in stops]
    km = sum(g.dist_m(a, b) for a, b in zip(pts, pts[1:])) / 1000
    t0 = now - timedelta(hours=km / 95 + 1)
    trips = [
        ("9a000000-0000-4000-8000-0000000000t1", "d1", "9a-veh-1", t0),
        ("9a000000-0000-4000-8000-0000000000t2", "d2", "9a-veh-2", datetime(2026, 11, 24, 16, 48, 30, tzinfo=timezone.utc)),
        ("9a000000-0000-4000-8000-0000000000t3", "d3", "9a-veh-3", now - timedelta(minutes=12)),
    ]
    t["trips"] = [{"id": tid, "org_id": ORG, "driver_id": DRIVERS[d]["id"], "route_id": route, "vehicle_id": v,
                   "assignment_id": f"qa-{d}", "status": "active", "started_at": st.isoformat(), "ended_at": None,
                   "progress_version": 0} for tid, d, v, st in trips]
    # 1. synthetic realistic run
    tr = g.Track(g.offset(pts[0], 0, -500), t0 + timedelta(seconds=4), seed=24, sigma_m=25, clip_m=40)
    tr.drive(pts[0], 25).dwell(60)
    for i, (a, b) in enumerate(zip(pts, pts[1:])):
        if g.dist_m(a, b) > 20_000:
            tr.drive(g.lerp(a, b, 0.04), 45).drive(g.lerp(a, b, 0.5), 115).spike(2500).drive(g.lerp(a, b, 0.96), 105).drive(b, 35)
        else:
            tr.drive(g.lerp(a, b, 0.3), 60).drive(g.lerp(a, b, 0.6), 15).drive(b, 70)
        tr.dwell(45 if i % 2 == 0 else 5)
    for i in range(0, len(tr.fixes), 6):
        C.post(f"/trips/{trips[0][0]}/pings", json=tr.fixes[i:i + 6], headers=H("d1"))
        if i % 72 == 0:
            C.post(f"/trips/{trips[0][0]}/heartbeat", json={"sent_at": tr.fixes[i]["recorded_at"], "battery": 70, "net_state": "online"}, headers=H("d1"))
    C.post(f"/trips/{trips[0][0]}/events", headers=H("d1"), json={"app_version": "2.0.0", "build": "42", "platform": "android 14", "events": [
        {"id": "a1", "type": "perm.location", "at": t0.isoformat(), "message": "location permission: always", "data": {"value": "always"}},
        {"id": "a2", "type": "svc.started", "at": t0.isoformat(), "message": "foreground service started (type=location)"},
        {"id": "a3", "type": "gate.spike_drop", "at": (t0 + timedelta(minutes=30)).isoformat(), "message": "live marker skipped a 2.5 km jump", "data": {"implied_kmh": 1800}}]})
    C.post(f"/trips/{trips[0][0]}/end", json={}, headers=H("d1"))
    # 2. real 24 Nov heartbeat timeline (zero fixes)
    hbs = GET("trip_heartbeats?trip_id=eq.e491bc37-4d70-4fc9-96b7-0d8edf496a3c&select=sent_at,battery,net_state&order=sent_at")
    for h in hbs:
        C.post(f"/trips/{trips[1][0]}/heartbeat", json=h, headers=H("d2"))
    C.post(f"/trips/{trips[1][0]}/end", json={"ended_at": "2026-11-24T17:46:52Z"}, headers=H("d2"))
    print(f"seeded: synthetic {len(tr.fixes)} fixes over {km:.0f} km; 24 Nov replay {len(hbs)} heartbeats; live no-GPS trip", flush=True)
    return trips[2][0]


def keep_alive(trip_id):
    while True:
        now = datetime.now(timezone.utc)
        C.post(f"/trips/{trip_id}/heartbeat", json={"sent_at": now.isoformat(), "battery": 64, "net_state": "online"}, headers=H("d3"))
        trip_lifecycle.sweep_once(now=now)
        time.sleep(30)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    live = seed()
    threading.Thread(target=keep_alive, args=(live,), daemon=True).start()
    uvicorn.run(main.app, host="127.0.0.1", port=port, log_level="warning")
