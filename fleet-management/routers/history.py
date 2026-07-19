"""Trip history for the manager History section (org-scoped, view_tracking).

Uses the DRIVER-STARTED trips (backend trips), not movement auto-splitting.
Returns, for a selected driver/vehicle (or all) over a date range, each trip
with its route + vehicle and its ordered pings (the actual path).

Query plan (bounded — never a full pings scan):
  1. trips in the range (started_at within the local-day bounds), org-scoped,
     optionally filtered to one driver or one vehicle.
  2. ONE pings query filtered by `trip_id IN (those trips)`, ordered by trip
     then recorded_at — so ping volume is bounded by the SELECTED trips only.
  3. batched name/geometry lookups for the referenced drivers/routes/vehicles.
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth import require_permission
from capacity_logic import org_module
from database import supabase

router = APIRouter(prefix="/history", tags=["history"])

LOCAL_TZ = timezone(timedelta(hours=2))  # Egypt (UTC+2)


def _local_hour(iso) -> int:
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(LOCAL_TZ).hour
    except Exception:
        return 8


def _derive_school_log(trip: dict, visits: list) -> dict:
    """Trip-log times derived from the trip + stop visits (no manual entry):
      * morning  → pickup_time (first stop arrival) + school_arrival_time (last
        stop arrival, else trip end)
      * afternoon → home_arrival_time (last stop arrival, else trip end)
    Session is inferred from the trip's start hour (before noon local = morning)."""
    started, ended = trip.get("started_at"), trip.get("ended_at")
    session = "morning" if _local_hour(started) < 12 else "afternoon"
    arrivals = sorted(v["arrival_time"] for v in visits if v.get("arrival_time"))
    first = arrivals[0] if arrivals else None
    last = arrivals[-1] if arrivals else None
    if session == "morning":
        return {"session": "morning", "pickup_time": first or started, "school_arrival_time": last or ended, "home_arrival_time": None}
    return {"session": "afternoon", "pickup_time": started, "school_arrival_time": None, "home_arrival_time": last or ended}


@router.get("")
def get_history(
    current_user: dict = Depends(require_permission("view_tracking")),
    kind: str = Query("drivers", description="drivers|vehicles"),
    subject_id: Optional[str] = Query(None, description="A specific driver/vehicle id, or omit for all."),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
):
    org_id = current_user["org_id"]
    if kind not in ("drivers", "vehicles"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="kind must be drivers|vehicles.")

    # Default range = last 7 days (local) if not supplied.
    today = datetime.now(LOCAL_TZ).date()
    d_from = date_from or (today - timedelta(days=7))
    d_to = date_to or today
    if d_to < d_from:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="date_to is before date_from.")
    start_utc = datetime.combine(d_from, time.min, tzinfo=LOCAL_TZ).astimezone(timezone.utc)
    end_utc = datetime.combine(d_to + timedelta(days=1), time.min, tzinfo=LOCAL_TZ).astimezone(timezone.utc)

    # --- 1. trips in range (+ optional subject filter) ---
    q = (
        supabase.table("trips")
        .select("id, driver_id, vehicle_id, route_id, status, started_at, ended_at")
        .eq("org_id", org_id)
        .gte("started_at", start_utc.isoformat())
        .lt("started_at", end_utc.isoformat())
    )
    if subject_id:
        q = q.eq("driver_id", subject_id) if kind == "drivers" else q.eq("vehicle_id", subject_id)
    trips = q.order("started_at", desc=False).execute().data
    if not trips:
        return {"trips": []}

    trip_ids = [t["id"] for t in trips]
    driver_ids = list({t["driver_id"] for t in trips if t.get("driver_id")})
    vehicle_ids = list({t["vehicle_id"] for t in trips if t.get("vehicle_id")})
    route_ids = list({t["route_id"] for t in trips if t.get("route_id")})

    names = {}
    if driver_ids:
        names = {p["id"]: p["name"] for p in supabase.table("profiles").select("id, name").in_("id", driver_ids).execute().data}
    buses = {}
    if vehicle_ids:
        buses = {v["id"]: v["bus_number"] for v in supabase.table("vehicles").select("id, bus_number").in_("id", vehicle_ids).execute().data}
    routes = {}
    if route_ids:
        for r in supabase.table("routes").select("id, name, geometry, color").in_("id", route_ids).execute().data:
            routes[r["id"]] = r

    # --- 2. pings for exactly these trips (bounded by selected trips) ---
    # PostgREST caps a single response at ~1000 rows. A busy trip (5s pings ≈
    # 720/hour) or several trips together easily exceed that, which previously
    # truncated the drawn polyline mid-route. We PAGE through with .range() until
    # a short page signals the end, so EVERY ping is returned and drawn.
    pings_by_trip = defaultdict(list)
    PAGE = 1000
    offset = 0
    while True:
        chunk = (
            supabase.table("location_pings")
            .select("trip_id, lat, lng, recorded_at")
            .in_("trip_id", trip_ids)
            .order("trip_id", desc=False)
            .order("recorded_at", desc=False)
            .range(offset, offset + PAGE - 1)
            .execute()
        ).data
        for p in chunk:
            pings_by_trip[p["trip_id"]].append(
                {"lat": p["lat"], "lng": p["lng"], "recorded_at": p["recorded_at"]}
            )
        if len(chunk) < PAGE:
            break
        offset += PAGE

    # --- 2b. stop-visits for these trips (arrival + waiting time per stop) ---
    # Embedded here (not a separate /trips/{id}/stop-visits call) so it shares the
    # History view_tracking permission and one round-trip. Tolerant of the table
    # not existing yet (older DBs) and of trips with no visits (older trips).
    visits_by_trip = defaultdict(list)
    try:
        sv_rows = (
            supabase.table("stop_visits")
            .select("trip_id, stop_id, stop_order, arrival_time, departure_time, planned_dwell_seconds, actual_dwell_seconds")
            .in_("trip_id", trip_ids)
            .order("trip_id", desc=False)
            .order("stop_order", desc=False)
            .execute()
        ).data
        sv_stop_ids = list({v["stop_id"] for v in sv_rows if v.get("stop_id")})
        sv_names = {}
        if sv_stop_ids:
            sv_names = {
                r["id"]: r["name"]
                for r in supabase.table("route_stops").select("id, name").in_("id", sv_stop_ids).execute().data
            }
        for v in sv_rows:
            visits_by_trip[v["trip_id"]].append(
                {
                    "stop_id": v.get("stop_id"),
                    "stop_name": sv_names.get(v.get("stop_id")),
                    "stop_order": v.get("stop_order"),
                    "arrival_time": v.get("arrival_time"),
                    "departure_time": v.get("departure_time"),
                    "planned_dwell_seconds": v.get("planned_dwell_seconds"),
                    "actual_dwell_seconds": v.get("actual_dwell_seconds"),
                }
            )
    except Exception:
        pass  # no stop_visits table / query failed → history still returns trips+pings

    is_school = org_module(org_id) == "school"
    out = []
    for t in trips:
        r = routes.get(t.get("route_id")) or {}
        visits = visits_by_trip.get(t["id"], [])
        out.append(
            {
                "trip_id": t["id"],
                "driver_id": t.get("driver_id"),
                "driver_name": names.get(t.get("driver_id")),
                "vehicle_id": t.get("vehicle_id"),
                "vehicle_bus_number": buses.get(t.get("vehicle_id")),
                "route_id": t.get("route_id"),
                "route_name": r.get("name"),
                "route_geometry": r.get("geometry"),
                "route_color": r.get("color"),
                "status": t.get("status"),
                "started_at": t.get("started_at"),
                "ended_at": t.get("ended_at"),
                "pings": pings_by_trip.get(t["id"], []),
                "stop_visits": visits,
                # School trip log: pickup / school-arrival / home-arrival, derived.
                "school_log": _derive_school_log(t, visits) if is_school else None,
            }
        )
    # Group-friendly order: by driver name, then time.
    out.sort(key=lambda x: ((x["driver_name"] or "").lower(), x["started_at"] or ""))
    return {"trips": out}
