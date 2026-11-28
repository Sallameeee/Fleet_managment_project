"""Shared live-position computation, extracted from routers/live.py so the school
module (all-buses map, parent tracking) reuses the EXACT same source the manager
Full View uses — no duplicate tracking path.

One entry per driver with a recent last-known position (active OR offline within
24h). Position always comes from the latest ping; route/vehicle come from the
resolved current assignment.
"""

from datetime import datetime, time, timedelta, timezone
from typing import Optional

import gps_filter
import live_status
import trip_lifecycle as lifecycle
from database import supabase

ONLINE_WINDOW = timedelta(minutes=2)  # kept for importers; the rule lives in live_status
LAST_KNOWN_LOOKBACK = timedelta(hours=24)
from capacity_logic import LOCAL_TZ  # single DST-aware Africa/Cairo zone


def parse_hm(value: Optional[str]) -> Optional[time]:
    if not value:
        return None
    try:
        parts = str(value).split(":")
        return time(int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        return None


def pick_current_assignment(assigns: list, now_t: time) -> Optional[dict]:
    """The assignment that best represents 'now': the one whose window contains
    now; else the next upcoming; else the most recent earlier; else the first."""
    parsed = [(a, parse_hm(a.get("start_time")), parse_hm(a.get("end_time"))) for a in assigns]
    for a, s, e in parsed:
        if s and e and s <= now_t < e:
            return a
    upcoming = [(a, s) for a, s, _ in parsed if s and s > now_t]
    if upcoming:
        return min(upcoming, key=lambda x: x[1])[0]
    past = [(a, e) for a, _, e in parsed if e and e <= now_t]
    if past:
        return max(past, key=lambda x: x[1])[0]
    return assigns[0] if assigns else None


def driver_live_positions(org_id: str) -> list:
    """One entry per driver with a recent last-known position. Bounded queries:
    active + recent(24h) trips, today's assignments, and ONE latest-ping query per
    driver. Same output the manager Full View renders."""
    now = datetime.now(timezone.utc)
    cutoff_online = now - ONLINE_WINDOW
    cutoff_recent = now - LAST_KNOWN_LOOKBACK

    _cols = "id, driver_id, vehicle_id, route_id, started_at, ended_at"
    if lifecycle.has_col("trips", "conn_lost_at"):  # trip-lifecycle signals (046)
        _cols += ", conn_lost_at, last_battery, last_net_state, end_reason, last_signal_at, last_heartbeat_at"
    active = (
        supabase.table("trips")
        .select(_cols)
        .eq("org_id", org_id)
        .eq("status", "active")
        .execute()
    ).data
    recent = (
        supabase.table("trips")
        .select(_cols)
        .eq("org_id", org_id)
        .gte("started_at", cutoff_recent.isoformat())
        .order("started_at", desc=True)
        .execute()
    ).data

    driver_trip: dict = {}
    for t in active:
        driver_trip.setdefault(t["driver_id"], (t, True))
    for t in recent:
        driver_trip.setdefault(t["driver_id"], (t, False))
    if not driver_trip:
        return []

    driver_ids = list(driver_trip.keys())
    today_local = datetime.now(LOCAL_TZ).date()
    now_t = datetime.now(LOCAL_TZ).time()

    assigns_by_driver: dict = {}
    for a in (
        supabase.table("assignments")
        .select("id, driver_id, route_id, vehicle_id, start_time, end_time")
        .eq("org_id", org_id)
        .eq("trip_date", today_local.isoformat())
        .in_("driver_id", driver_ids)
        .execute()
        .data
    ):
        assigns_by_driver.setdefault(a["driver_id"], []).append(a)

    chosen: dict = {}
    for did, (tr, on_trip) in driver_trip.items():
        assigns = assigns_by_driver.get(did, [])
        count = len(assigns)
        if on_trip:
            chosen[did] = {"route_id": tr.get("route_id"), "vehicle_id": tr.get("vehicle_id"), "window": None, "count": count}
            continue
        cur = pick_current_assignment(assigns, now_t)
        if cur:
            s = parse_hm(cur.get("start_time"))
            e = parse_hm(cur.get("end_time"))
            window = f"{s.strftime('%H:%M')}–{e.strftime('%H:%M')}" if s and e else None
            chosen[did] = {"route_id": cur.get("route_id"), "vehicle_id": cur.get("vehicle_id"), "window": window, "count": count}
        else:
            chosen[did] = {"route_id": None, "vehicle_id": None, "window": None, "count": 0}

    route_ids = list({c["route_id"] for c in chosen.values() if c.get("route_id")})
    vehicle_ids = list({c["vehicle_id"] for c in chosen.values() if c.get("vehicle_id")})
    names = {p["id"]: p["name"] for p in supabase.table("profiles").select("id, name").in_("id", driver_ids).execute().data}
    buses = {}
    if vehicle_ids:
        buses = {v["id"]: v["bus_number"] for v in supabase.table("vehicles").select("id, bus_number").in_("id", vehicle_ids).execute().data}
    routes = {}
    if route_ids:
        routes = {r["id"]: r["name"] for r in supabase.table("routes").select("id, name").in_("id", route_ids).execute().data}

    out = []
    for did, (tr, on_trip) in driver_trip.items():
        # Newest ACCEPTED fix (shared GPS plausibility rule) — never a spike.
        _pos = gps_filter.latest_position(supabase, tr["id"])
        lp = [_pos] if _pos else []
        position = None
        if lp:
            p = lp[0]
            position = {"lat": p["lat"], "lng": p["lng"], "recorded_at": p["recorded_at"]}
        # ONE rule (live_status): online = app alive (heartbeat / any signal),
        # gps_fresh = a recent ACCEPTED fix. A phone that is online but whose
        # fixes are not arriving is "online_no_gps", never "offline".
        live = live_status.for_trip({**tr, "status": "active" if on_trip else "completed"}, now=now,
                                    last_fix_at=position["recorded_at"] if position else None)
        online = bool(on_trip and live["online"])

        c = chosen[did]
        out.append(
            {
                "driver_id": did,
                "name": names.get(did),
                "vehicle_id": c.get("vehicle_id"),
                "vehicle_bus_number": buses.get(c.get("vehicle_id")),
                "route_id": c.get("route_id"),
                "route_name": routes.get(c.get("route_id")),
                "assignment_window": c.get("window"),
                "assignment_count": c.get("count", 0),
                "position": position,
                "online": online,
                "live_state": live["state"] if on_trip else "offline",
                "gps_fresh": bool(on_trip and live["gps_fresh"]),
                "live_reason": live["reason"] if on_trip else None,
                "on_trip": on_trip,
                "last_ended_at": tr.get("ended_at") if not on_trip else None,
                # Trip-lifecycle signals (migration 046; None before it).
                "connection_lost_at": tr.get("conn_lost_at") if on_trip else None,
                "last_battery": tr.get("last_battery") if on_trip else None,
                "last_net_state": tr.get("last_net_state") if on_trip else None,
                "end_reason": tr.get("end_reason") if not on_trip else None,
            }
        )
    out.sort(key=lambda d: (not d["online"], (d["name"] or "").lower()))
    return out
