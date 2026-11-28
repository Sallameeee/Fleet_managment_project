"""TRIP DEBUG — admin-only view of everything the system decided during a trip.

Answers "why did this trip show no path / OFFLINE / the wrong next stop?" from
evidence: the trip decision log (trip_events, server + app), every stored fix
with its accepted / rejected verdict and reason, heartbeat continuity, the stop
progression and the lifecycle log points — plus an automatic diagnosis.

Access: org admins only (owner, or a staff member with manage_settings), and
ONLY trips of the caller's own organization. Each org belongs to exactly one
module, so a University admin can never open a School trip (or the reverse);
every event row is additionally checked to carry the caller's org and module.
No personal data beyond what the diagnosis needs: no names, phones or emails
(drivers appear by id only), no tokens.
"""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response

import gps_filter
import live_status
import progress_runtime
import trip_events
import trip_lifecycle as lifecycle
from auth import require_permission
from capacity_logic import LOCAL_TZ, org_module
from database import supabase

router = APIRouter(prefix="/trip-debug", tags=["trip-debug"])

ADMIN = require_permission("manage_settings")
MAX_PINGS = 20000
HB_GAP_S = live_status.SIGNAL_FRESH.total_seconds()


def _page(builder_fn, limit: int) -> list:
    out, off, size = [], 0, 1000
    while off < limit:
        rows = builder_fn().range(off, off + size - 1).execute().data or []
        out += rows
        if len(rows) < size:
            break
        off += size
    return out


def _load_trip(trip_id: str, org_id: str) -> dict:
    rows = supabase.table("trips").select("*").eq("id", trip_id).eq("org_id", org_id).limit(1).execute().data
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No trip with id '{trip_id}' exists in your organization.")
    return rows[0]


@router.get("/trips")
def list_debug_trips(
    current_user: dict = Depends(ADMIN),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """Trips of the caller's org in a local-date range (default: last 7 days)."""
    org_id = current_user["org_id"]
    today = datetime.now(LOCAL_TZ).date()
    d_from, d_to = date_from or today - timedelta(days=7), date_to or today
    start = datetime.combine(d_from, time.min, tzinfo=LOCAL_TZ).astimezone(timezone.utc)
    end = datetime.combine(d_to + timedelta(days=1), time.min, tzinfo=LOCAL_TZ).astimezone(timezone.utc)
    trips = (supabase.table("trips").select("id, driver_id, route_id, vehicle_id, status, started_at, ended_at")
             .eq("org_id", org_id).gte("started_at", start.isoformat()).lt("started_at", end.isoformat())
             .order("started_at", desc=True).limit(limit).execute().data) or []
    rids = list({t["route_id"] for t in trips if t.get("route_id")})
    vids = list({t["vehicle_id"] for t in trips if t.get("vehicle_id")})
    routes = {r["id"]: r["name"] for r in supabase.table("routes").select("id, name").in_("id", rids).execute().data} if rids else {}
    buses = {v["id"]: v["bus_number"] for v in supabase.table("vehicles").select("id, bus_number").in_("id", vids).execute().data} if vids else {}
    for t in trips:
        t["route_name"] = routes.get(t.get("route_id"))
        t["vehicle_bus_number"] = buses.get(t.get("vehicle_id"))
    return {"count": len(trips), "trips": trips, "module": org_module(org_id)}


def _collect(trip_id: str, org_id: str) -> dict:
    trip = _load_trip(trip_id, org_id)
    module = org_module(org_id)
    tagged = lifecycle.has_col("location_pings", "is_outlier")
    cols = "id, lat, lng, speed, heading, recorded_at, created_at"
    if tagged:
        cols += ", is_outlier, reject_reason, implied_kmh"
    for c in ("battery", "net_state", "accuracy"):
        if lifecycle.has_col("location_pings", c):
            cols += ", " + c
    pings = _page(lambda: supabase.table("location_pings").select(cols).eq("trip_id", trip_id)
                  .order("recorded_at", desc=False).order("id", desc=False), MAX_PINGS)
    # Verdict per fix: the stored ingest tag, re-checked with the CURRENT rule so
    # pings stored before the tags existed are judged too (read-only).
    accepted_ids = {p["id"] for p in gps_filter.clean_pings([dict(p) for p in pings])}
    fixes = []
    for p in pings:
        ok = p["id"] in accepted_ids and not p.get("is_outlier")
        reason = p.get("reject_reason") if p.get("is_outlier") else (None if ok else "filtered_on_read")
        fixes.append({**p, "accepted": ok, "reason": reason,
                      "lag_s": round((gps_filter.parse_dt(p["created_at"]) - gps_filter.parse_dt(p["recorded_at"])).total_seconds(), 1)
                      if p.get("created_at") else None})
    events = [e for e in trip_events.read(trip_id, org_id) if e.get("org_id") == org_id and e.get("module") == module]
    hbs = []
    if lifecycle.has_table("trip_heartbeats"):
        hbs = _page(lambda: supabase.table("trip_heartbeats").select("sent_at, created_at, battery, net_state")
                    .eq("trip_id", trip_id).order("sent_at", desc=False), 20000)
    alerts = (supabase.table("alerts").select("type, occurred_at, detail, lat, lng").eq("trip_id", trip_id)
              .eq("org_id", org_id).order("occurred_at", desc=False).limit(2000).execute().data) or []
    try:
        visits = (supabase.table("stop_visits").select("*").eq("trip_id", trip_id).eq("org_id", org_id)
                  .order("stop_order", desc=False).execute().data) or []
    except Exception:
        visits = []
    return {"trip": trip, "module": module, "fixes": fixes, "events": events, "heartbeats": hbs,
            "alerts": alerts, "visits": visits}


def _hb_summary(hbs: list, trip: dict) -> dict:
    if not hbs:
        return {"count": 0, "gaps": []}
    ts = [gps_filter.parse_dt(h["sent_at"]) for h in hbs]
    gaps = []
    for a, b in zip(ts, ts[1:]):
        g = (b - a).total_seconds()
        if g > HB_GAP_S:
            gaps.append({"from": a.isoformat(), "to": b.isoformat(), "seconds": round(g)})
    lags = sorted((gps_filter.parse_dt(h["created_at"]) - gps_filter.parse_dt(h["sent_at"])).total_seconds() for h in hbs if h.get("created_at"))
    return {"count": len(hbs), "first": ts[0].isoformat(), "last": ts[-1].isoformat(), "gaps": gaps[:100],
            "median_lag_s": round(lags[len(lags) // 2], 1) if lags else None,
            "net_states": dict(Counter(h.get("net_state") for h in hbs))}


def _diagnosis(d: dict, counts: dict, hb: dict) -> list:
    """Plain-language findings, most important first. Evidence-based only."""
    out = []
    n_fix, n_ok = counts["fixes"], counts["accepted"]
    ev_types = counts["events_by_type"]
    if hb["count"] and n_fix == 0:
        out.append(f"The app was alive the whole time ({hb['count']} heartbeats) but NO GPS fix ever reached the server. "
                   "The phone did not deliver fixes (location permission / background location / the app's GPS gate) — check the app events below.")
    elif hb["count"] == 0 and n_fix == 0:
        out.append("Nothing reached the server from the phone after the trip started (no fixes, no heartbeats): offline, or the app was closed.")
    if ev_types.get("request_rejected"):
        out.append(f"{ev_types['request_rejected']} tracking request(s) were rejected by validation (422) — the installed app's payload does not match this backend.")
    if n_fix:
        rej = n_fix - n_ok
        if rej:
            top = Counter(f["reason"] for f in d["fixes"] if not f["accepted"]).most_common(3)
            out.append(f"{rej} of {n_fix} fixes ({round(100 * rej / n_fix)} %) were rejected: " + ", ".join(f"{r} ×{c}" for r, c in top) + ".")
        else:
            out.append(f"All {n_fix} fixes were accepted.")
    if hb.get("gaps"):
        longest = max(hb["gaps"], key=lambda g: g["seconds"])
        out.append(f"{len(hb['gaps'])} heartbeat gap(s) longer than {int(HB_GAP_S)} s (longest {longest['seconds']} s from {longest['from']}): the app or its network was gone.")
    no_gps = [e for e in d["events"] if e["type"] == "live_state" and (e.get("payload") or {}).get("to") == "online_no_gps"]
    if no_gps:
        out.append(f"Live state went to 'online, no GPS fix' {len(no_gps)} time(s) — the dashboard showed the bus online without a fresh position.")
    if not d["events"]:
        out.append("No decision-log rows for this trip (it ran before the decision log existed, or migration 048 is not applied).")
    app_rows = counts["events_by_source"].get("app", 0)
    if d["events"] and not app_rows:
        out.append("No app-side events: the installed app does not send its decision log yet (needs the updated APK).")
    return out


@router.get("/{trip_id}")
def trip_debug(trip_id: str, current_user: dict = Depends(ADMIN)):
    org_id = current_user["org_id"]
    d = _collect(trip_id, org_id)
    trip = d["trip"]
    hb = _hb_summary(d["heartbeats"], trip)
    counts = {
        "fixes": len(d["fixes"]),
        "accepted": sum(1 for f in d["fixes"] if f["accepted"]),
        "rejected_by_reason": dict(Counter(f["reason"] for f in d["fixes"] if not f["accepted"])),
        "events": len(d["events"]),
        "events_by_type": dict(Counter(e["type"] for e in d["events"])),
        "events_by_source": dict(Counter(e["source"] for e in d["events"])),
        "heartbeats": hb["count"],
        "stop_visits_by_status": dict(Counter(v.get("status") or "visited" for v in d["visits"])),
    }
    live = live_status.for_trip(trip)
    try:
        progress = progress_runtime.view(trip)
    except Exception:
        progress = None
    safe_trip = {k: trip.get(k) for k in ("id", "status", "started_at", "ended_at", "end_reason", "end_detail", "route_id",
                                          "vehicle_id", "driver_id", "last_signal_at", "last_heartbeat_at", "last_fix_at",
                                          "last_battery", "last_net_state", "conn_lost_at", "app_closed_at", "live_state",
                                          "live_state_at")}
    route = None
    if trip.get("route_id"):
        r = supabase.table("routes").select("id, name, geometry").eq("id", trip["route_id"]).limit(1).execute().data
        route = r[0] if r else None
    return {
        "trip": safe_trip, "module": d["module"], "route": route,
        "stops": progress_runtime.load_stops(trip.get("route_id")),
        "live": live, "progress": progress, "counts": counts, "heartbeats": hb,
        "diagnosis": _diagnosis(d, counts, hb),
        "fixes": d["fixes"], "events": d["events"], "alerts": d["alerts"], "stop_visits": d["visits"],
        "log_available": trip_events.available(),
        "retention_days": trip_events.RETENTION_DAYS,
        "timezone": "Africa/Cairo",
    }


@router.get("/{trip_id}/export")
def trip_debug_export(trip_id: str, current_user: dict = Depends(ADMIN),
                      format: str = Query("json", pattern="^(json|csv)$")):
    """Download the trip's full timeline (decision log + every fix + heartbeats)."""
    org_id = current_user["org_id"]
    d = _collect(trip_id, org_id)
    name = f"trip-debug-{trip_id[:8]}"
    if format == "json":
        body = json.dumps({"trip_id": trip_id, "module": d["module"], "events": d["events"], "fixes": d["fixes"],
                           "heartbeats": d["heartbeats"], "alerts": d["alerts"], "stop_visits": d["visits"]},
                          ensure_ascii=False, default=str, indent=1)
        return Response(body, media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{name}.json"'})
    rows = []
    for e in d["events"]:
        rows.append({"time_utc": e["occurred_at"], "kind": "event", "source": e["source"], "type": e["type"],
                     "message": e["message"], "lat": (e.get("payload") or {}).get("lat"), "lng": (e.get("payload") or {}).get("lng"),
                     "verdict": "", "reason": (e.get("payload") or {}).get("reason", ""),
                     "payload": json.dumps(e.get("payload") or {}, ensure_ascii=False)})
    for f in d["fixes"]:
        rows.append({"time_utc": f["recorded_at"], "kind": "fix", "source": "app", "type": "fix",
                     "message": f"speed={f.get('speed')} accuracy={f.get('accuracy')} lag_s={f.get('lag_s')}",
                     "lat": f["lat"], "lng": f["lng"], "verdict": "accepted" if f["accepted"] else "rejected",
                     "reason": f.get("reason") or "", "payload": ""})
    for h in d["heartbeats"]:
        rows.append({"time_utc": h["sent_at"], "kind": "heartbeat", "source": "app", "type": "heartbeat",
                     "message": f"battery={h.get('battery')} net={h.get('net_state')}", "lat": "", "lng": "",
                     "verdict": "", "reason": "", "payload": ""})
    rows.sort(key=lambda r: str(r["time_utc"]))
    buf = io.StringIO()
    cols = ["time_utc", "time_cairo", "kind", "source", "type", "message", "lat", "lng", "verdict", "reason", "payload"]
    w = csv.DictWriter(buf, cols)
    w.writeheader()
    for r in rows:
        try:
            r["time_cairo"] = gps_filter.parse_dt(r["time_utc"]).astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            r["time_cairo"] = ""
        w.writerow(r)
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
