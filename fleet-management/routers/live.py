"""Live tracking feed for the manager Full View (org-scoped, view_tracking).

We track the DRIVER; the vehicle is metadata on the trip. This returns one entry
per driver currently running an active trip, with their latest position.
"""

from datetime import datetime, time, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends

from auth import require_permission
from database import supabase

router = APIRouter(prefix="/live", tags=["live"])

ONLINE_WINDOW = timedelta(minutes=2)
# How far back a driver's last activity can be and still show as a dimmed,
# last-known marker on the map.
LAST_KNOWN_LOOKBACK = timedelta(hours=24)
# The manager operates in Egypt (UTC+2); "today" for assignments is a local date.
LOCAL_TZ = timezone(timedelta(hours=2))


def _parse_hm(value: Optional[str]) -> Optional[time]:
    if not value:
        return None
    try:
        parts = str(value).split(":")
        return time(int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        return None


def _pick_current_assignment(assigns: list, now_t: time) -> Optional[dict]:
    """Choose the assignment that best represents 'now' among a driver's set
    for today: (1) the one whose [start,end) window contains now; else (2) the
    next upcoming (smallest start after now); else (3) the most recent earlier
    one (largest end at/before now); else the first."""
    parsed = [(a, _parse_hm(a.get("start_time")), _parse_hm(a.get("end_time"))) for a in assigns]
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


@router.get("/positions")
def driver_positions(current_user: dict = Depends(require_permission("view_tracking"))):
    """One entry per driver with a recent last-known position (active OR offline).

    Query plan (bounded — never a full pings scan):
      1. active trips (status=active) — the online drivers.
      2. recent trips (started_at >= now-24h), newest first — to recover the
         last-known spot of drivers who finished/paused within the day.
      3. most-recent trip per driver = active first, else newest recent trip.
      4. ONE indexed latest-ping query per such driver (order recorded_at desc,
         limit 1). Count is bounded by fleet size, not by ping volume.
    online = the driver's most-recent trip is active AND their last ping is
    within ONLINE_WINDOW; everyone else is a dimmed last-known marker.
    """
    org_id = current_user["org_id"]
    now = datetime.now(timezone.utc)
    cutoff_online = now - ONLINE_WINDOW
    cutoff_recent = now - LAST_KNOWN_LOOKBACK

    active = (
        supabase.table("trips")
        .select("id, driver_id, vehicle_id, route_id, started_at, ended_at")
        .eq("org_id", org_id)
        .eq("status", "active")
        .execute()
    ).data
    recent = (
        supabase.table("trips")
        .select("id, driver_id, vehicle_id, route_id, started_at, ended_at")
        .eq("org_id", org_id)
        .gte("started_at", cutoff_recent.isoformat())
        .order("started_at", desc=True)
        .execute()
    ).data

    # driver_id -> (trip_row, on_trip). Active wins; else the newest recent trip.
    driver_trip: dict = {}
    for t in active:
        driver_trip.setdefault(t["driver_id"], (t, True))
    for t in recent:
        driver_trip.setdefault(t["driver_id"], (t, False))

    if not driver_trip:
        return {"count": 0, "drivers": []}

    driver_ids = list(driver_trip.keys())

    # --- Today's assignments per driver: the source of truth for the ROUTE the
    # card shows (NOT the possibly-stale old trip). One bounded query for the
    # day, filtered to these drivers. ---
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

    # Resolve, per driver, the route/vehicle to display:
    #   - if on an ACTIVE trip → that trip's route + vehicle (what they're running)
    #   - else → today's current/most-relevant assignment's route + vehicle
    #   - else → no current assignment
    chosen: dict = {}  # driver_id -> {route_id, vehicle_id, window, count}
    for did, (tr, on_trip) in driver_trip.items():
        assigns = assigns_by_driver.get(did, [])
        count = len(assigns)
        if on_trip:
            chosen[did] = {"route_id": tr.get("route_id"), "vehicle_id": tr.get("vehicle_id"), "window": None, "count": count}
            continue
        cur = _pick_current_assignment(assigns, now_t)
        if cur:
            s = _parse_hm(cur.get("start_time"))
            e = _parse_hm(cur.get("end_time"))
            window = f"{s.strftime('%H:%M')}–{e.strftime('%H:%M')}" if s and e else None
            chosen[did] = {"route_id": cur.get("route_id"), "vehicle_id": cur.get("vehicle_id"), "window": window, "count": count}
        else:
            chosen[did] = {"route_id": None, "vehicle_id": None, "window": None, "count": 0}

    # Batch name lookups from the RESOLVED ids (not the old trips').
    route_ids = list({c["route_id"] for c in chosen.values() if c.get("route_id")})
    vehicle_ids = list({c["vehicle_id"] for c in chosen.values() if c.get("vehicle_id")})
    names = {
        p["id"]: p["name"]
        for p in supabase.table("profiles").select("id, name").in_("id", driver_ids).execute().data
    }
    buses = {}
    if vehicle_ids:
        buses = {
            v["id"]: v["bus_number"]
            for v in supabase.table("vehicles").select("id, bus_number").in_("id", vehicle_ids).execute().data
        }
    routes = {}
    if route_ids:
        routes = {
            r["id"]: r["name"]
            for r in supabase.table("routes").select("id, name").in_("id", route_ids).execute().data
        }

    out = []
    for did, (tr, on_trip) in driver_trip.items():
        # Position ALWAYS comes from the latest ping (where they physically are),
        # even if that ping belongs to an old trip. Route/vehicle come from the
        # resolved current assignment above — different, correct sources.
        lp = (
            supabase.table("location_pings")
            .select("lat, lng, recorded_at")
            .eq("trip_id", tr["id"])
            .order("recorded_at", desc=True)
            .limit(1)
            .execute()
        ).data
        position = None
        online = False
        if lp:
            p = lp[0]
            position = {"lat": p["lat"], "lng": p["lng"], "recorded_at": p["recorded_at"]}
            try:
                rec = datetime.fromisoformat(str(p["recorded_at"]).replace("Z", "+00:00"))
                online = on_trip and rec >= cutoff_online
            except Exception:
                online = False

        c = chosen[did]
        out.append(
            {
                "driver_id": did,
                "name": names.get(did),
                "vehicle_bus_number": buses.get(c.get("vehicle_id")),
                "route_id": c.get("route_id"),
                "route_name": routes.get(c.get("route_id")),
                "assignment_window": c.get("window"),
                "assignment_count": c.get("count", 0),
                "position": position,
                "online": online,
                "on_trip": on_trip,
                "last_ended_at": tr.get("ended_at") if not on_trip else None,
            }
        )
    # Online first, then by name — a stable, readable list order.
    out.sort(key=lambda d: (not d["online"], (d["name"] or "").lower()))
    return {"count": len(out), "drivers": out}


@router.get("/drivers")
def live_drivers(current_user: dict = Depends(require_permission("view_tracking"))):
    org_id = current_user["org_id"]

    # Only ACTIVE trips — the only place a live driver can be.
    active = (
        supabase.table("trips")
        .select("id, driver_id, vehicle_id, route_id")
        .eq("org_id", org_id)
        .eq("status", "active")
        .execute()
    ).data
    if not active:
        return {"count": 0, "drivers": []}

    driver_ids = list({t["driver_id"] for t in active})
    vehicle_ids = list({t["vehicle_id"] for t in active if t.get("vehicle_id")})
    route_ids = list({t["route_id"] for t in active if t.get("route_id")})

    names = {
        p["id"]: p["name"]
        for p in supabase.table("profiles").select("id, name").in_("id", driver_ids).execute().data
    }
    buses = {}
    if vehicle_ids:
        buses = {
            v["id"]: v["bus_number"]
            for v in supabase.table("vehicles").select("id, bus_number").in_("id", vehicle_ids).execute().data
        }
    routes = {}
    if route_ids:
        routes = {
            r["id"]: r["name"]
            for r in supabase.table("routes").select("id, name").in_("id", route_ids).execute().data
        }

    cutoff = datetime.now(timezone.utc) - ONLINE_WINDOW
    out = []
    seen: set = set()
    for t in active:
        did = t["driver_id"]
        if did in seen:
            continue
        seen.add(did)

        # Latest position: ONE indexed query per active trip (order by recorded_at
        # desc, limit 1). Bounded by the number of active trips — we never scan
        # the pings table and never read completed trips' pings.
        lp = (
            supabase.table("location_pings")
            .select("lat, lng, recorded_at")
            .eq("trip_id", t["id"])
            .order("recorded_at", desc=True)
            .limit(1)
            .execute()
        ).data

        position = None
        online = False
        if lp:
            p = lp[0]
            position = {"lat": p["lat"], "lng": p["lng"], "recorded_at": p["recorded_at"]}
            try:
                rec = datetime.fromisoformat(str(p["recorded_at"]).replace("Z", "+00:00"))
                online = rec >= cutoff
            except Exception:
                online = False

        out.append(
            {
                "driver_id": did,
                "name": names.get(did),
                "vehicle_bus_number": buses.get(t.get("vehicle_id")),
                "route_name": routes.get(t.get("route_id")),
                "position": position,
                "online": online,
            }
        )

    return {"count": len(out), "drivers": out}
