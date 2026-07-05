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
from database import supabase

router = APIRouter(prefix="/history", tags=["history"])

LOCAL_TZ = timezone(timedelta(hours=2))  # Egypt (UTC+2)


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

    # --- 2. ONE pings query for exactly these trips (bounded by selected trips) ---
    pings_by_trip = defaultdict(list)
    pings = (
        supabase.table("location_pings")
        .select("trip_id, lat, lng, recorded_at")
        .in_("trip_id", trip_ids)
        .order("trip_id", desc=False)
        .order("recorded_at", desc=False)
        .execute()
    ).data
    for p in pings:
        pings_by_trip[p["trip_id"]].append(
            {"lat": p["lat"], "lng": p["lng"], "recorded_at": p["recorded_at"]}
        )

    out = []
    for t in trips:
        r = routes.get(t.get("route_id")) or {}
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
            }
        )
    # Group-friendly order: by driver name, then time.
    out.sort(key=lambda x: ((x["driver_name"] or "").lower(), x["started_at"] or ""))
    return {"trips": out}
