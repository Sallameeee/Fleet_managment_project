"""REPLAY real trips through the REAL tracking pipeline — read-only.

Production is only READ (GET with the service key, via qa_lib). Each trip's
real fixes and heartbeats are re-posted, in their original arrival order and
batching, to the real FastAPI app running on an in-memory database
(fleet-management/tests/fake_supabase.py), so the new GPS filter, stop
progression, live state and decision log run exactly as in production —
without writing anything anywhere but the output folder.

Also drives a synthetic full-length run over a real route's stops (default:
the 6th of October -> El Mahalla route `test22`) with realistic GPS: +/-20-40 m
noise, ~5 s cadence, highway at 100-120 km/h, slow town traffic.

Usage:
  python qa/replay_real_trips.py OUT_DIR [trip_id ...] [--synthetic ROUTE_ID]
Outputs one JSON per trip (summary, stop decisions, decision-log counts, the
Trip debug diagnosis) into OUT_DIR — keep it OUTSIDE the repo (real data).
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "qa"))
sys.path.insert(0, str(ROOT / "fleet-management"))
sys.path.insert(0, str(ROOT / "fleet-management" / "tests"))
os.environ["LIFECYCLE_SWEEP"] = "0"

import qa_lib as q  # noqa: E402  (READ-ONLY use: GET only below)
import fake_supabase  # noqa: E402

DB = fake_supabase.install()

import auth  # noqa: E402
import gps_filter  # noqa: E402
import gps_sim as g  # noqa: E402
import main  # noqa: E402
import trip_events  # noqa: E402
import trip_lifecycle  # noqa: E402
from fastapi import Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ADMIN_ID = "qa-replay-admin"
_profiles: dict = {}


def _user(request: Request) -> dict:
    return _profiles[request.headers["x-replay-user"]]


main.app.dependency_overrides[auth.get_current_user] = _user
auth._verify_token = lambda creds: __import__("types").SimpleNamespace(id=_profiles[creds.credentials]["id"])
CLIENT = TestClient(main.app)


def GET(path: str) -> list:
    """Paged read-only GET against production."""
    out, off = [], 0
    while True:
        r = q.client.get(q.SB_URL + "/rest/v1/" + path, headers={**q._SB_H, "Range": f"{off}-{off + 999}"})
        r.raise_for_status()
        page = r.json()
        out += page
        if len(page) < 1000:
            return out
        off += 1000


def _h(who: str) -> dict:
    return {"x-replay-user": who, "Authorization": f"Bearer {who}"}


def _seed(trip: dict, org: dict, stops: list, route: dict | None) -> None:
    DB.reset()
    trip_events._probe.update(ok=None, at=0.0)
    trip_events._module_cache.clear()
    trip_lifecycle._probe.clear()
    _profiles.clear()
    _profiles["driver"] = {"id": trip["driver_id"], "org_id": trip["org_id"], "role": "driver", "permissions": {}, "is_active": True}
    _profiles["admin"] = {"id": ADMIN_ID, "org_id": trip["org_id"], "role": "owner", "permissions": {}, "is_active": True}
    t = DB.tables
    t["organizations"] = [{**org, "log_settings": org.get("log_settings") or {}}]
    t["profiles"] = [dict(p) for p in _profiles.values()]
    t["routes"] = [route] if route else []
    t["route_stops"] = [dict(s) for s in stops]
    t["trips"] = [{k: trip.get(k) for k in ("id", "org_id", "driver_id", "route_id", "vehicle_id", "assignment_id", "started_at")}
                  | {"status": "active", "ended_at": None, "progress_version": 0}]
    for name in ("alerts", "stop_events", "stop_visits", "location_pings", "trip_heartbeats", "trip_events",
                 "assignments", "alert_rules", "vehicles"):
        t[name] = []


def _batches(rows: list, key: str, gap_s: float = 2.0) -> list:
    """Group rows that ARRIVED together (same request) by their created_at."""
    rows = sorted(rows, key=lambda r: (r[key], r.get("recorded_at") or r.get("sent_at")))
    out, cur, last = [], [], None
    for r in rows:
        t = gps_filter.parse_dt(r[key])
        if last is not None and (t - last).total_seconds() > gap_s:
            out.append(cur)
            cur = []
        cur.append(r)
        last = t
    if cur:
        out.append(cur)
    return out


def _report(trip_id: str, label: str, extra: dict) -> dict:
    d = CLIENT.get(f"/trip-debug/{trip_id}", headers=_h("admin")).json()
    ev = DB.tables["trip_events"]
    visits = sorted(DB.tables["stop_visits"], key=lambda v: v.get("stop_order") or 0)
    return {
        "label": label, "trip_id": trip_id, **extra,
        "fixes": d["counts"]["fixes"], "accepted": d["counts"]["accepted"],
        "rejected_by_reason": d["counts"]["rejected_by_reason"],
        "stop_decisions": [{"order": v.get("stop_order"), "status": v["status"], "closest_m": v.get("closest_m")} for v in visits],
        "stop_messages": [e["message"] for e in ev if e["type"] in ("stop_arrived", "stop_passed", "stop_skipped", "stop_upgraded")],
        "events_by_type": dict(Counter(e["type"] for e in ev)),
        "diagnosis": d["diagnosis"],
    }


def replay(trip_id: str) -> dict:
    trip = GET(f"trips?id=eq.{trip_id}&select=*")[0]
    org = GET(f"organizations?id=eq.{trip['org_id']}&select=id,name,slug,module,log_settings")[0]
    stops = GET(f"route_stops?route_id=eq.{trip['route_id']}&select=id,route_id,stop_order,name,lat,lng,dwell_minutes&order=stop_order") if trip.get("route_id") else []
    route = (GET(f"routes?id=eq.{trip['route_id']}&select=id,org_id,name,geometry") or [None])[0] if trip.get("route_id") else None
    pings = GET(f"location_pings?trip_id=eq.{trip_id}&select=lat,lng,speed,heading,recorded_at,created_at,battery,net_state&order=created_at")
    hbs = GET(f"trip_heartbeats?trip_id=eq.{trip_id}&select=sent_at,created_at,battery,net_state&order=created_at")
    _seed(trip, org, stops, route)
    # Interleave ping and heartbeat requests in their real arrival order.
    reqs = [("pings", b) for b in _batches(pings, "created_at")] + [("heartbeat", b) for b in _batches(hbs, "created_at")]
    reqs.sort(key=lambda x: x[1][0]["created_at"])
    statuses = Counter()
    for kind, batch in reqs:
        if kind == "pings":
            body = [{k: p[k] for k in ("lat", "lng", "speed", "heading", "recorded_at", "battery", "net_state") if p.get(k) is not None} for p in batch]
        else:
            body = [{k: h[k] for k in ("sent_at", "battery", "net_state") if h.get(k) is not None} for h in batch]
        r = CLIENT.post(f"/trips/{trip_id}/{kind}", json=body, headers=_h("driver"))
        statuses[f"{kind}:{r.status_code}"] += 1
    end_at = trip.get("ended_at")
    if end_at:
        CLIENT.post(f"/trips/{trip_id}/end", json={"ended_at": end_at}, headers=_h("driver"))
    return _report(trip_id, f"real {org['module']} trip {trip_id[:8]} ({org['name']})",
                   {"requests": dict(statuses), "real_pings": len(pings), "real_heartbeats": len(hbs),
                    "started_at": trip.get("started_at"), "ended_at": end_at})


def synthetic(route_id: str, seed: int = 24) -> dict:
    """Full-length realistic run over a REAL route's stops (read-only)."""
    route = GET(f"routes?id=eq.{route_id}&select=id,org_id,name,geometry")[0]
    org = GET(f"organizations?id=eq.{route['org_id']}&select=id,name,slug,module,log_settings")[0]
    stops = GET(f"route_stops?route_id=eq.{route_id}&select=id,route_id,stop_order,name,lat,lng,dwell_minutes&order=stop_order")
    pts = [(s["lat"], s["lng"]) for s in stops]
    total_km = sum(g.dist_m(a, b) for a, b in zip(pts, pts[1:])) / 1000
    now = datetime.now(timezone.utc)
    t0 = now - timedelta(hours=total_km / 95 + 0.5)
    trip = {"id": "5e000000-0000-4000-8000-000000000024", "org_id": org["id"], "driver_id": "qa-synthetic-driver",
            "route_id": route_id, "vehicle_id": None, "assignment_id": "qa-synthetic", "started_at": t0.isoformat()}
    _seed(trip, org, stops, route)
    tr = g.Track(g.offset(pts[0], 0, -500), t0 + timedelta(seconds=4), seed=seed, sigma_m=25, clip_m=40)
    tr.drive(pts[0], 25).dwell(60)
    for i, (a, b) in enumerate(zip(pts, pts[1:])):
        leg = g.dist_m(a, b)
        mid = g.lerp(a, b, 0.5)
        if leg > 20_000:          # intercity highway: town exit, 115 km/h, slow approach
            tr.drive(g.lerp(a, b, 0.04), 45).drive(mid, 115).spike(2500).drive(g.lerp(a, b, 0.96), 105).drive(b, 35)
        else:                     # town / ring road with traffic
            tr.drive(g.lerp(a, b, 0.3), 60).drive(g.lerp(a, b, 0.6), 15).drive(b, 70)
        tr.dwell(45 if i % 2 == 0 else 5)
    tr.dwell(60)
    for i in range(0, len(tr.fixes), 6):
        CLIENT.post(f"/trips/{trip['id']}/pings", json=tr.fixes[i:i + 6], headers=_h("driver"))
        if i % 72 == 0:
            CLIENT.post(f"/trips/{trip['id']}/heartbeat", json={"sent_at": tr.fixes[i]["recorded_at"], "battery": 70,
                                                                 "net_state": "online"}, headers=_h("driver"))
    CLIENT.post(f"/trips/{trip['id']}/end", json={}, headers=_h("driver"))
    return _report(trip["id"], f"synthetic realistic run over real route '{route['name']}' ({total_km:.0f} km)",
                   {"synthetic_fixes": len(tr.fixes), "route_km": round(total_km, 1), "spikes_injected": len(pts) - 1 if total_km else 0})


if __name__ == "__main__":
    args = sys.argv[1:]
    out = Path(args.pop(0))
    out.mkdir(parents=True, exist_ok=True)
    syn = None
    if "--synthetic" in args:
        i = args.index("--synthetic")
        syn = args[i + 1]
        del args[i:i + 2]
    results = [replay(t) for t in args]
    if syn:
        results.append(synthetic(syn))
    for r in results:
        (out / f"replay_{r['trip_id'][:8]}.json").write_text(json.dumps(r, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        print(json.dumps({k: r[k] for k in ("label", "fixes", "accepted", "rejected_by_reason", "stop_decisions", "diagnosis")
                          if k in r}, ensure_ascii=False, default=str))
