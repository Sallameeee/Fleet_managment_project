"""Public passenger tracking — the ONLY unauthenticated endpoint in the system.

A passenger opens a vehicle's permanent share_token link and sees the live
position of whatever active trip is running on that bus, but ONLY during the
org's working hours, and ONLY a whitelisted minimum of data.

Security posture (no token, anonymous viewer):
  * No auth dependency at all.
  * Uses the service client but SELECTs only whitelisted columns and never
    returns org details, driver identity, internal ids, alerts, or scores.
  * Unknown token -> generic 404 that reveals nothing.
"""

from datetime import datetime, time, timedelta, timezone

from fastapi import APIRouter, HTTPException, status

from database import supabase

router = APIRouter(prefix="/track", tags=["tracking (public)"])

# Working hours are evaluated in the org's local time. Egypt is UTC+2 (fixed
# offset, matching the reports module). NULL hours => always-on.
LOCAL_TZ = timezone(timedelta(hours=2))


def _parse_time(value):
    """DB time value ('07:00:00') -> datetime.time, or None."""
    if value is None:
        return None
    if isinstance(value, time):
        return value
    return time.fromisoformat(str(value))


def _within_hours(start_t: time, end_t: time, now_t: time) -> bool:
    """Is now_t inside [start, end]? Handles an overnight window (start > end)."""
    if start_t <= end_t:
        return start_t <= now_t <= end_t
    return now_t >= start_t or now_t <= end_t  # wraps past midnight


@router.get("/{share_token}")
def track(share_token: str):
    # --- 1. Resolve the vehicle by its permanent share_token. Generic 404 if no
    # match — never reveal whether a token format is valid or which org it's in.
    vehicle_rows = (
        supabase.table("vehicles")
        .select("id, org_id, bus_number")
        .eq("share_token", share_token)
        .limit(1)
        .execute()
    ).data
    if not vehicle_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tracking link not found.",
        )
    vehicle = vehicle_rows[0]

    # --- 2. Working hours (org local time). A window is enforced only when BOTH
    # bounds are set; otherwise tracking is always-on.
    org_rows = (
        supabase.table("organizations")
        .select("tracking_start_time, tracking_end_time")
        .eq("id", vehicle["org_id"])
        .limit(1)
        .execute()
    ).data
    org = org_rows[0] if org_rows else {}
    start_t = _parse_time(org.get("tracking_start_time"))
    end_t = _parse_time(org.get("tracking_end_time"))

    if start_t is not None and end_t is not None:
        now_local = datetime.now(LOCAL_TZ).time()
        if not _within_hours(start_t, end_t, now_local):
            # Outside hours: only the resume time, nothing sensitive.
            return {"status": "outside_hours", "resumes_at": start_t.isoformat()}

    # --- 3. Current ACTIVE trip on this vehicle (most recent if more than one).
    trip_rows = (
        supabase.table("trips")
        .select("id, route_id")
        .eq("vehicle_id", vehicle["id"])
        .eq("org_id", vehicle["org_id"])
        .eq("status", "active")
        .order("started_at", desc=True)
        .limit(1)
        .execute()
    ).data
    if not trip_rows:
        return {"status": "not_in_service"}
    trip = trip_rows[0]

    # --- 4. Latest position (most recent ping). Whitelist lat/lng/recorded_at.
    ping_rows = (
        supabase.table("location_pings")
        .select("lat, lng, recorded_at")
        .eq("trip_id", trip["id"])
        .order("recorded_at", desc=True)
        .limit(1)
        .execute()
    ).data
    position = None
    if ping_rows:
        p = ping_rows[0]
        position = {"lat": p["lat"], "lng": p["lng"], "recorded_at": p["recorded_at"]}

    # --- 5. Route name + stops so the map can draw the line and markers.
    route = None
    if trip.get("route_id"):
        r_rows = (
            supabase.table("routes")
            .select("name")
            .eq("id", trip["route_id"])
            .limit(1)
            .execute()
        ).data
        stops = (
            supabase.table("route_stops")
            .select("name, lat, lng, stop_order")
            .eq("route_id", trip["route_id"])
            .order("stop_order", desc=False)
            .execute()
        ).data
        route = {
            "name": r_rows[0]["name"] if r_rows else None,
            "stops": [
                {"name": s["name"], "lat": s["lat"], "lng": s["lng"], "order": s["stop_order"]}
                for s in stops
            ],
        }

    # Minimal live payload: position, route/stops, and the bus number (visible on
    # the bus itself). No driver, org, ids, alerts, or scores.
    return {
        "status": "live",
        "bus_number": vehicle["bus_number"],
        "position": position,
        "route": route,
    }
