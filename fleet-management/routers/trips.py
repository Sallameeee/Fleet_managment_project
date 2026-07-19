"""Trip execution routes — where a driver actually runs an assignment.

A trip is the live run of an assignment: the driver starts it, it goes
`active`, passengers track the bus via the VEHICLE's permanent share_token
(trips no longer carry their own token), and the driver ends it -> `completed`.

Gating split:
  * start / end  -> driver-only (require_role("driver")). Drivers hold no
                    management permissions, so this is keyed on WHO they are.
                    A driver may only act on their OWN assignment / trip.
  * list (GET)   -> manager view, require_permission("manage_trips"), org-scoped.
"""

import math
from datetime import date, datetime, time, timedelta, timezone
from typing import List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

import notifications_logic as notify
from auth import require_permission, require_role
from capacity_logic import org_module
from database import supabase

router = APIRouter(prefix="/trips", tags=["trips"])

SCHEDULE_GRACE_MIN = 5  # arrival within 5 min of the scheduled time counts as on-time

# Geofence radius (meters) for auto arrival/departure detection. Named so it's
# a one-line tune. A ping within this distance of a stop counts as "at" it.
GEOFENCE_RADIUS_M = 75

# The org operates in Egypt (UTC+2); "today" for a driver's assignments is local.
LOCAL_TZ = timezone(timedelta(hours=2))


def _now_iso() -> str:
    """Current UTC instant as an ISO string (for started_at / ended_at)."""
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value) -> datetime:
    """Parse a DB ISO timestamp into a tz-aware UTC datetime."""
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in meters. Sub-meter accurate at geofence scale —
    plenty for a 75m radius, and runs in-process (no DB round-trip)."""
    r = 6371000.0  # earth radius, meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class TripStart(BaseModel):
    assignment_id: str = Field(..., min_length=1)
    # Optional: the driver confirms the ACTUAL bus they're driving. If omitted,
    # we fall back to the vehicle named on the assignment.
    vehicle_id: Optional[str] = None


class PingIn(BaseModel):
    """One GPS sample from the driver's device. org_id/driver_id are NEVER
    taken from here — they come from the trip/token."""
    lat: float
    lng: float
    speed: Optional[float] = None
    heading: Optional[float] = None
    # Device timestamp. Defaults to server now() at insert time if missing.
    recorded_at: Optional[datetime] = None


class StopVisitIn(BaseModel):
    """The app reports reaching a stop (arrival) and, on departure, how long it
    actually stayed. Called once on arrival (departure omitted) and again on
    departure (with departure_time). Idempotent per (trip, stop)."""
    stop_id: str = Field(..., min_length=1)
    arrival_time: datetime
    departure_time: Optional[datetime] = None


def _enrich(trip: dict, driver_name=None, route_name=None,
            vehicle_bus_number=None, share_token=None) -> dict:
    """Shape a trip row for the response, with readable names attached."""
    return {
        "id": trip["id"],
        "assignment_id": trip["assignment_id"],
        "status": trip["status"],
        "started_at": trip["started_at"],
        "ended_at": trip["ended_at"],
        "score": trip.get("score"),
        "driver_id": trip["driver_id"],
        "driver_name": driver_name,
        "route_id": trip["route_id"],
        "route_name": route_name,
        "vehicle_id": trip["vehicle_id"],
        "vehicle_bus_number": vehicle_bus_number,
        "vehicle_share_token": share_token,  # the permanent per-vehicle link
    }


def _org_module(org_id: str) -> str:
    """The org's feature module ('university' | 'school'); defaults to 'university'."""
    try:
        r = supabase.table("organizations").select("module").eq("id", org_id).limit(1).execute()
        if r.data and r.data[0].get("module"):
            return r.data[0]["module"]
    except Exception:
        pass
    return "university"


def _require_school_org(org_id: str) -> None:
    """Students/attendance are SCHOOL-ONLY — University drivers get a clean 403 and
    never see any student list."""
    if _org_module(org_id) != "school":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Student attendance is only available for school organizations.",
        )


@router.get("/my-assignments")
def my_assignments(current_user: dict = Depends(require_role("driver"))):
    """The signed-in DRIVER's own assignments for TODAY (org-scoped), enriched
    with route + vehicle names, plus their current ACTIVE trip if one is running
    (so the app can resume). Driver-facing counterpart to the manager-only
    GET /assignments — a driver holds no management permissions, so this is gated
    on WHO they are (require_role) and always scoped to their own driver_id.
    """
    org_id = current_user["org_id"]
    driver_id = current_user["id"]
    today = datetime.now(LOCAL_TZ).date().isoformat()

    rows = (
        supabase.table("assignments")
        .select("id, route_id, vehicle_id, trip_date, shift_label, start_time, end_time")
        .eq("org_id", org_id)
        .eq("driver_id", driver_id)  # ALWAYS the caller — never from the request
        .eq("trip_date", today)
        .order("start_time", desc=False)
        .execute()
        .data
    )

    route_ids = list({r["route_id"] for r in rows if r.get("route_id")})
    vehicle_ids = list({r["vehicle_id"] for r in rows if r.get("vehicle_id")})
    routes, vehicles = {}, {}
    if route_ids:
        routes = {
            x["id"]: x["name"]
            for x in supabase.table("routes").select("id, name").in_("id", route_ids).execute().data
        }
    if vehicle_ids:
        vehicles = {
            x["id"]: x
            for x in supabase.table("vehicles").select("id, bus_number").in_("id", vehicle_ids).execute().data
        }

    assignments = [
        {
            "assignment_id": r["id"],
            "route_id": r.get("route_id"),
            "route_name": routes.get(r.get("route_id")),
            "vehicle_id": r.get("vehicle_id"),
            "vehicle_bus_number": (vehicles.get(r.get("vehicle_id")) or {}).get("bus_number"),
            "trip_date": r.get("trip_date"),
            "shift_label": r.get("shift_label"),
            "start_time": r.get("start_time"),
            "end_time": r.get("end_time"),
        }
        for r in rows
    ]

    active = (
        supabase.table("trips")
        .select("*")
        .eq("org_id", org_id)
        .eq("driver_id", driver_id)
        .eq("status", "active")
        .limit(1)
        .execute()
        .data
    )
    active_trip = _enrich_one(active[0]) if active else None

    # `module` lets the app show the school-only attendance feature (and never for
    # University drivers).
    return {"date": today, "module": _org_module(org_id), "assignments": assignments, "active_trip": active_trip}


@router.post("/start", status_code=status.HTTP_201_CREATED)
def start_trip(
    body: TripStart,
    current_user: dict = Depends(require_role("driver")),
):
    org_id = current_user["org_id"]
    driver_id = current_user["id"]

    # --- 1. Load the assignment, scoped to the caller's org. ---
    try:
        a_result = (
            supabase.table("assignments")
            .select("*")
            .eq("id", body.assignment_id)
            .eq("org_id", org_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not load assignment: {exc}",
        )

    if not a_result.data:
        # Not in this org (or doesn't exist). Same 404 either way so a driver
        # can't probe other orgs' assignment ids.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No assignment with id '{body.assignment_id}' exists in your organization.",
        )
    assignment = a_result.data[0]

    # --- 2. Ownership: a driver may only start their OWN assignment. ---
    if assignment["driver_id"] != driver_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This assignment belongs to another driver. You can only start your own.",
        )

    # --- 3. Duplicate guard: one active trip per driver at a time. ---
    # If an active trip already exists for this driver OR this assignment, we do
    # NOT create a second one — return 409 with the existing active trip.
    existing = (
        supabase.table("trips")
        .select("*")
        .eq("org_id", org_id)
        .eq("status", "active")
        .or_(f"driver_id.eq.{driver_id},assignment_id.eq.{body.assignment_id}")
        .limit(1)
        .execute()
    )
    if existing.data:
        active = existing.data[0]
        enriched = _enrich_one(active)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "You already have an active trip. End it before starting another.",
                "active_trip": enriched,
            },
        )

    # --- 4. Vehicle confirmation: driver-confirmed vehicle, else assignment's. ---
    if body.vehicle_id:
        v_result = (
            supabase.table("vehicles")
            .select("id, bus_number, share_token")
            .eq("id", body.vehicle_id)
            .eq("org_id", org_id)
            .limit(1)
            .execute()
        )
        if not v_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No vehicle with id '{body.vehicle_id}' exists in your organization.",
            )
        vehicle = v_result.data[0]
    else:
        v_result = (
            supabase.table("vehicles")
            .select("id, bus_number, share_token")
            .eq("id", assignment["vehicle_id"])
            .eq("org_id", org_id)
            .limit(1)
            .execute()
        )
        if not v_result.data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The assignment's vehicle no longer exists. Provide a vehicle_id.",
            )
        vehicle = v_result.data[0]

    # Route name for the response.
    r_result = (
        supabase.table("routes")
        .select("id, name")
        .eq("id", assignment["route_id"])
        .eq("org_id", org_id)
        .limit(1)
        .execute()
    )
    route_name = r_result.data[0]["name"] if r_result.data else None

    # --- 5. Create the active trip. ---
    payload = {
        "org_id": org_id,  # from token
        "assignment_id": assignment["id"],
        "driver_id": driver_id,  # from token
        "route_id": assignment["route_id"],  # from the assignment
        "vehicle_id": vehicle["id"],  # confirmed or assignment's
        "status": "active",
        "started_at": _now_iso(),
    }
    try:
        result = supabase.table("trips").insert(payload).execute()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not start trip: {exc}",
        )

    trip = result.data[0]
    # School only, best-effort: tell each parent whose child is on this bus today
    # that "<child>'s bus has started" (deduped per trip+parent).
    notify.trip_started(trip)
    return _enrich(
        trip,
        driver_name=current_user.get("name"),
        route_name=route_name,
        vehicle_bus_number=vehicle["bus_number"],
        share_token=vehicle["share_token"],
    )


@router.post("/{trip_id}/end")
def end_trip(
    trip_id: str,
    current_user: dict = Depends(require_role("driver")),
):
    org_id = current_user["org_id"]
    driver_id = current_user["id"]

    # Load the trip, scoped to the caller's org.
    t_result = (
        supabase.table("trips")
        .select("*")
        .eq("id", trip_id)
        .eq("org_id", org_id)
        .limit(1)
        .execute()
    )
    if not t_result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No trip with id '{trip_id}' exists in your organization.",
        )
    trip = t_result.data[0]

    # Ownership: only the trip's own driver may end it.
    if trip["driver_id"] != driver_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This trip belongs to another driver. You can only end your own.",
        )

    # Idempotent-ish: if it's already finished, just report that (no error).
    if trip["status"] == "completed":
        enriched = _enrich_one(trip)
        enriched["message"] = "This trip was already completed."
        return enriched
    if trip["status"] == "cancelled":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This trip was cancelled and cannot be ended.",
        )

    # Complete it.
    try:
        upd = (
            supabase.table("trips")
            .update({"status": "completed", "ended_at": _now_iso()})
            .eq("id", trip_id)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not end trip: {exc}",
        )

    # School only, best-effort: compute + persist this trip's performance metrics.
    _compute_trip_performance(upd.data[0])
    return _enrich_one(upd.data[0])


def _compute_trip_performance(trip: dict) -> None:
    """Compute + persist per-trip performance when a trip ends (SCHOOL only,
    best-effort — never breaks trip-end). Reuses existing signals:
      * speeding / off_route → counts of the `alerts` this trip already produced
        (device speed vs the org's speeding rule; nearest-stop distance vs the
        off_route rule — both configured in Alerts / alert_rules).
      * schedule adherence → stop_events.arrived_at vs route_stops.arrival_time,
        on-time within SCHEDULE_GRACE_MIN."""
    try:
        org_id = trip.get("org_id")
        if not org_id or org_module(org_id) != "school":
            return
        trip_id = trip["id"]
        route_id = trip.get("route_id")
        trip_date = (trip.get("started_at") or "")[:10] or datetime.now(LOCAL_TZ).date().isoformat()

        alerts = supabase.table("alerts").select("type").eq("trip_id", trip_id).execute().data
        speeding = sum(1 for a in alerts if a.get("type") == "speeding")
        off_route = sum(1 for a in alerts if a.get("type") == "off_route")

        # Scheduled clock time per stop (route_stops.arrival_time = "HH:MM:SS").
        sched = {}
        if route_id:
            for s in supabase.table("route_stops").select("id, arrival_time").eq("route_id", route_id).execute().data:
                if s.get("arrival_time"):
                    sched[s["id"]] = str(s["arrival_time"])
        events = supabase.table("stop_events").select("stop_id, arrived_at").eq("trip_id", trip_id).execute().data
        total = on_time = late = 0
        delays = []
        for ev in events:
            sid, arr = ev.get("stop_id"), ev.get("arrived_at")
            if sid not in sched or not arr:
                continue
            try:
                dt = datetime.fromisoformat(str(arr).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                loc = dt.astimezone(LOCAL_TZ)
                parts = sched[sid].split(":")
                sched_min = int(parts[0]) * 60 + int(parts[1])
                delay = (loc.hour * 60 + loc.minute) - sched_min  # minutes; <0 = early
            except Exception:
                continue
            total += 1
            delays.append(delay)
            if delay > SCHEDULE_GRACE_MIN:
                late += 1
            else:
                on_time += 1

        supabase.table("trip_performance").upsert(
            {
                "trip_id": trip_id,
                "org_id": org_id,
                "driver_id": trip.get("driver_id"),
                "route_id": route_id,
                "trip_date": trip_date,
                "speeding_count": speeding,
                "off_route_count": off_route,
                "stops_total": total,
                "stops_on_time": on_time,
                "stops_late": late,
                "avg_delay_min": round(sum(delays) / len(delays), 1) if delays else None,
                "max_delay_min": max(delays) if delays else None,
                "computed_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="trip_id",
        ).execute()
    except Exception:
        pass  # metrics are best-effort; never fail trip-end


def _load_own_active_trip(trip_id: str, current_user: dict) -> dict:
    """Load a trip and assert: exists, in caller's org, ACTIVE, owned by caller.

    Shared by the ping path. Raises 404 (not found / cross-org — same message so
    ids can't be probed) or 403 (someone else's trip / not active).
    """
    org_id = current_user["org_id"]
    driver_id = current_user["id"]

    result = (
        supabase.table("trips")
        .select("id, org_id, driver_id, route_id, vehicle_id, status")
        .eq("id", trip_id)
        .eq("org_id", org_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No trip with id '{trip_id}' exists in your organization.",
        )
    trip = result.data[0]

    if trip["driver_id"] != driver_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This trip belongs to another driver. You can only ping your own.",
        )
    if trip["status"] != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This trip is '{trip['status']}', not active. Only active trips accept pings.",
        )
    return trip


@router.post("/{trip_id}/pings", status_code=status.HTTP_201_CREATED)
def post_pings(
    trip_id: str,
    body: Union[PingIn, List[PingIn]],
    current_user: dict = Depends(require_role("driver")),
):
    # Ownership + active-state check (one indexed lookup). org_id/driver_id are
    # taken from the trip/token below — never from the request body.
    trip = _load_own_active_trip(trip_id, current_user)
    org_id = trip["org_id"]
    driver_id = trip["driver_id"]

    # Normalize single-or-batch into a list. The app may buffer offline samples
    # and flush several at once.
    pings = body if isinstance(body, list) else [body]
    if not pings:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No pings provided.",
        )

    # Normalize device timestamps to tz-aware UTC datetimes once, so the same
    # values feed both the insert and the chronological geofence pass.
    server_now = datetime.now(timezone.utc)
    norm = []
    for p in pings:
        dt = p.recorded_at or server_now
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        norm.append(
            {"lat": p.lat, "lng": p.lng, "speed": p.speed,
             "heading": p.heading, "recorded_dt": dt}
        )

    rows = [
        {
            "trip_id": trip_id,
            "org_id": org_id,  # from the trip, not the body
            "driver_id": driver_id,  # from the token, not the body
            "lat": n["lat"],
            "lng": n["lng"],
            "speed": n["speed"],
            "heading": n["heading"],
            "recorded_at": n["recorded_dt"].isoformat(),
        }
        for n in norm
    ]

    # Single batch insert — keeps this high-frequency path to one DB round-trip.
    try:
        result = supabase.table("location_pings").insert(rows).execute()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not record pings: {exc}",
        )

    # --- Detection on the just-inserted pings: geofence stop events + rule-based
    # speeding/off_route. Runs AFTER the insert so ingestion stays lean, and is
    # best-effort (never fails capture). Speeding/off_route rebuild a bounded
    # window of STORED pings around this batch (true-predecessor seed + replay
    # dedupe), so buffered/out-of-order flushes are handled correctly. ---
    detection = _process_pings(trip, norm)

    return {"recorded": len(result.data), "trip_id": trip_id, "detection": detection}


@router.get("/{trip_id}/route-stops")
def trip_route_stops(
    trip_id: str,
    current_user: dict = Depends(require_role("driver")),
):
    """The ORDERED stops of the active trip's route, for the app's arrival timer.
    Driver-facing (own active trip only)."""
    trip = _load_own_active_trip(trip_id, current_user)
    stops = (
        supabase.table("route_stops")
        .select("id, name, lat, lng, stop_order, dwell_minutes")
        .eq("route_id", trip["route_id"])
        .order("stop_order", desc=False)
        .execute()
        .data
    )
    return {"trip_id": trip_id, "route_id": trip["route_id"], "stops": stops}


@router.get("/route-map/{route_id}")
def route_map(
    route_id: str,
    current_user: dict = Depends(require_role("driver")),
):
    """Route polyline geometry + ordered stops for the driver's in-app map.

    Org-scoped (the route must belong to the driver's organization) and does NOT
    require an active trip, so the driver can preview the assigned route before
    starting. Purely read-only; independent of tracking.
    """
    org_id = current_user["org_id"]
    route = (
        supabase.table("routes")
        .select("id, name, color, geometry, updated_at")
        .eq("id", route_id)
        .eq("org_id", org_id)  # the route must belong to the driver's org
        .limit(1)
        .execute()
        .data
    )
    if not route:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route not found.")
    r = route[0]
    stops = (
        supabase.table("route_stops")
        .select("id, name, lat, lng, stop_order")
        .eq("route_id", route_id)
        .order("stop_order", desc=False)
        .execute()
        .data
    )
    return {
        "route_id": r["id"],
        "name": r.get("name"),
        "color": r.get("color"),
        "geometry": r.get("geometry"),  # GeoJSON LineString, or null for older routes
        "updated_at": r.get("updated_at"),  # bumps on any route/stop edit
        "stops": stops,
    }


@router.get("/route-version/{route_id}")
def route_version(route_id: str, current_user: dict = Depends(require_role("driver"))):
    """Just the route's updated_at — a cheap poll the app uses to detect edits and
    re-pull the full route only when it actually changed."""
    org_id = current_user["org_id"]
    r = supabase.table("routes").select("updated_at").eq("id", route_id).eq("org_id", org_id).limit(1).execute().data
    if not r:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route not found.")
    return {"route_id": route_id, "updated_at": r[0].get("updated_at")}


class AttendanceIn(BaseModel):
    student_id: str = Field(..., min_length=1)
    boarded: bool
    drop_off_stop: Optional[str] = None  # afternoon: the stop the student got off at


def _trip_session(trip: dict) -> str:
    """The trip's SESSION for attendance: 'morning' (pickup) if it starts before
    noon LOCAL time, else 'afternoon' (drop-off). Derived from the trip — no manual
    step and no schema flag."""
    started = trip.get("started_at")
    try:
        if started:
            dt = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            hour = dt.astimezone(LOCAL_TZ).hour
        else:
            hour = datetime.now(LOCAL_TZ).hour
    except Exception:
        hour = datetime.now(LOCAL_TZ).hour
    return "morning" if hour < 12 else "afternoon"


def _resolve_trip_stop(route_id: str, value: Optional[str]) -> Optional[str]:
    """Validate a drop-off stop NAME against the trip's route, returning its
    canonical (DB) name. Empty/None → None; a name not on the route → 400."""
    if not value or not value.strip():
        return None
    match = (
        supabase.table("route_stops").select("name").eq("route_id", route_id).ilike("name", value.strip()).limit(1).execute().data
    )
    if not match:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"'{value.strip()}' is not a stop on this route.")
    return match[0]["name"]


@router.get("/{trip_id}/students")
def trip_students(trip_id: str, current_user: dict = Depends(require_role("driver"))):
    """Students on the supervisor's active-trip route, with each student's boarded
    status FOR THIS TRIP. SCHOOL orgs only — University drivers get a 403 and see
    no student list. (A student = a passenger on the trip's route.)"""
    trip = _load_own_active_trip(trip_id, current_user)  # own + active
    org_id = trip["org_id"]
    _require_school_org(org_id)

    students = (
        supabase.table("passengers")
        .select("id, name, student_phone, parent_phone, grade, class_name, drop_off_stop")
        .eq("org_id", org_id)
        .eq("route_id", trip["route_id"])
        .execute()
        .data
    )
    ids = [s["id"] for s in students]
    att_by_student = {}
    if ids:
        att = (
            supabase.table("attendance")
            .select("student_id, boarded, drop_off_stop")
            .eq("trip_id", trip_id)
            .in_("student_id", ids)
            .execute()
            .data
        )
        att_by_student = {a["student_id"]: a for a in att}

    # The route's stops — the afternoon drop-off-stop picker chooses from these.
    stops = (
        supabase.table("route_stops").select("name, stop_order").eq("route_id", trip["route_id"]).order("stop_order", desc=False).execute().data
    )
    session = _trip_session(trip)

    out = []
    for s in students:
        a = att_by_student.get(s["id"]) or {}
        out.append(
            {
                "student_id": s["id"],
                "name": s.get("name"),
                "class_name": s.get("class_name"),
                "grade": s.get("grade"),
                "student_phone": s.get("student_phone"),
                "parent_phone": s.get("parent_phone"),
                "boarded": a.get("boarded", False),
                # Afternoon: the recorded drop-off, else the student's usual stop to prefill.
                "drop_off_stop": a.get("drop_off_stop") or s.get("drop_off_stop"),
            }
        )
    out.sort(key=lambda x: (x["name"] or "").lower())
    return {
        "trip_id": trip_id,
        "count": len(out),
        "session": session,
        "route_stops": [st.get("name") for st in stops if st.get("name")],
        "students": out,
    }


@router.post("/{trip_id}/attendance", status_code=status.HTTP_200_OK)
def record_attendance(
    trip_id: str,
    body: AttendanceIn,
    current_user: dict = Depends(require_role("driver")),
):
    """Record (upsert) one student's boarded status for this trip. Idempotent per
    (trip, student). SCHOOL orgs only."""
    trip = _load_own_active_trip(trip_id, current_user)
    org_id = trip["org_id"]
    _require_school_org(org_id)

    st = (
        supabase.table("passengers").select("id").eq("id", body.student_id).eq("org_id", org_id).limit(1).execute()
    )
    if not st.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That student is not in your organization.")

    trip_date = (trip.get("started_at") or "")[:10] or datetime.now(LOCAL_TZ).date().isoformat()
    # Only the afternoon (drop-off) session records WHERE the student got off.
    session = _trip_session(trip)
    drop_off_stop = _resolve_trip_stop(trip["route_id"], body.drop_off_stop) if session == "afternoon" else None
    payload = {
        "org_id": org_id,  # from the trip, never the body
        "trip_id": trip_id,
        "student_id": body.student_id,
        "trip_date": trip_date,
        "boarded": body.boarded,
        "session": session,
        "drop_off_stop": drop_off_stop,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        row = supabase.table("attendance").upsert(payload, on_conflict="trip_id,student_id").execute().data[0]
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not record attendance: {exc}")
    return {"student_id": body.student_id, "boarded": row["boarded"], "session": session, "drop_off_stop": row.get("drop_off_stop"), "trip_id": trip_id}


@router.post("/{trip_id}/stop-visits", status_code=status.HTTP_200_OK)
def record_stop_visit(
    trip_id: str,
    body: StopVisitIn,
    current_user: dict = Depends(require_role("driver")),
):
    """Record (upsert) the driver's visit to a stop: arrival, then departure.

    Idempotent per (trip_id, stop_id): the app calls it on arrival (departure
    omitted) and again on departure (with departure_time), always sending the
    same arrival_time. We compute actual_dwell_seconds server-side and store the
    planned dwell (route_stops.dwell_minutes * 60) for planned-vs-actual reports.
    """
    trip = _load_own_active_trip(trip_id, current_user)  # own + active
    org_id = trip["org_id"]

    st = (
        supabase.table("route_stops")
        .select("id, stop_order, dwell_minutes")
        .eq("id", body.stop_id)
        .eq("route_id", trip["route_id"])  # the stop must belong to THIS route
        .limit(1)
        .execute()
        .data
    )
    if not st:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That stop is not part of this trip's route.",
        )
    stop = st[0]

    arrival = body.arrival_time
    departure = body.departure_time
    if arrival.tzinfo is None:
        arrival = arrival.replace(tzinfo=timezone.utc)
    if departure is not None and departure.tzinfo is None:
        departure = departure.replace(tzinfo=timezone.utc)
    actual = int((departure - arrival).total_seconds()) if departure else None
    if actual is not None and actual < 0:
        actual = 0  # clock skew guard

    payload = {
        "trip_id": trip_id,
        "org_id": org_id,  # from the trip, never the body
        "stop_id": body.stop_id,
        "stop_order": stop["stop_order"],
        "arrival_time": arrival.isoformat(),
        "departure_time": departure.isoformat() if departure else None,
        "planned_dwell_seconds": (stop["dwell_minutes"] or 0) * 60,
        "actual_dwell_seconds": actual,
    }
    try:
        row = (
            supabase.table("stop_visits")
            .upsert(payload, on_conflict="trip_id,stop_id")
            .execute()
            .data[0]
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not record stop visit: {exc}",
        )
    # School only, best-effort: notify parents whose child's drop-off stop is THIS
    # stop that "<child>'s bus has arrived" (deduped per trip+student, so the
    # arrival + departure calls for the same stop don't double-notify).
    notify.child_arrived(trip, body.stop_id)
    return row


@router.get("/{trip_id}/stop-visits")
def list_stop_visits(
    trip_id: str,
    current_user: dict = Depends(require_permission("manage_trips")),
):
    """Manager read of a trip's stop visits (for reports: planned vs actual dwell)."""
    org_id = current_user["org_id"]
    _load_org_trip(trip_id, org_id)  # org-scope guard

    visits = (
        supabase.table("stop_visits")
        .select("id, stop_id, stop_order, arrival_time, departure_time, planned_dwell_seconds, actual_dwell_seconds")
        .eq("trip_id", trip_id)
        .eq("org_id", org_id)
        .order("stop_order", desc=False)
        .execute()
        .data
    )
    stop_ids = {v["stop_id"] for v in visits if v.get("stop_id")}
    names = {}
    if stop_ids:
        names = {
            r["id"]: r["name"]
            for r in supabase.table("route_stops").select("id, name").in_("id", list(stop_ids)).execute().data
        }
    for v in visits:
        v["stop_name"] = names.get(v["stop_id"])
    return {"count": len(visits), "trip_id": trip_id, "stop_visits": visits}


def _load_active_rules(org_id: str) -> list:
    """All active alert_rules for an org (loaded ONCE per batch, not per ping).

    Tolerant of the table not existing yet (pre-migration): returns [] so legacy
    geofence/short_stop detection keeps working until 004 is applied.
    """
    try:
        return (
            supabase.table("alert_rules")
            .select("id, name, type, threshold, target_kind, target_ids")
            .eq("org_id", org_id)
            .eq("is_active", True)
            .execute()
        ).data
    except Exception:
        return []


def _rule_applies(rule: dict, trip: dict) -> bool:
    """Does a rule target this trip's vehicle/driver? null/empty ids => all."""
    kind = rule.get("target_kind")
    ids = rule.get("target_ids") or []
    if kind == "all" or not ids:
        return True
    if kind == "vehicles":
        return trip.get("vehicle_id") in ids
    if kind == "drivers":
        return trip.get("driver_id") in ids
    return False


def _nearest_stop_m(lat: float, lng: float, stops: list) -> float:
    """Distance (m) to the closest route stop — our off_route approximation."""
    return min(_haversine_m(lat, lng, s["lat"], s["lng"]) for s in stops)


def _process_pings(trip: dict, norm_pings: list) -> dict:
    """Detect stop arrivals/departures (geofence) and rule-based speeding /
    off_route incidents for a batch of pings. Best-effort: any error is swallowed
    (pings are already saved).

    Geofence de-duplication: a stop is "currently inside / open" iff a stop_events
    row exists for (trip_id, stop_id) with arrived_at set and departed_at NULL. We
    load those once, keep them in `open_by_stop`, and only ARRIVE when a stop is
    NOT open — so continuous in-radius pings never spawn duplicate events.

    Incident detection (speeding/off_route) is handled by _detect_incidents, which
    rebuilds a bounded window of STORED pings around this batch so it stays correct
    under out-of-order / buffered delivery (see that function).
    """
    summary = {
        "arrivals": 0, "departures": 0, "short_stop_alerts": 0,
        "speeding_alerts": 0, "off_route_alerts": 0,
    }
    try:
        trip_id = trip["id"]
        org_id = trip["org_id"]
        driver_id = trip["driver_id"]
        route_id = trip["route_id"]

        # Load the route's stops and the org's active rules ONCE for the batch.
        stops = (
            supabase.table("route_stops")
            .select("id, name, lat, lng, dwell_minutes")
            .eq("route_id", route_id)
            .execute()
        ).data
        rules = _load_active_rules(org_id)
        ordered = sorted(norm_pings, key=lambda x: x["recorded_dt"])

        # short_stop reconciliation: legacy always-on UNLESS a short_stop rule is
        # defined, in which case its targeting governs whether alerts fire.
        shortstop_rules = [r for r in rules if r["type"] == "short_stop"]
        shortstop_applies = (not shortstop_rules) or any(
            _rule_applies(r, trip) for r in shortstop_rules
        )

        if not stops:
            # No stops -> no geofence/short_stop/off_route, but speeding can still
            # fire (it needs no route geometry).
            _detect_incidents(trip, ordered, rules, stops, summary)
            return summary

        # Currently-open events for this trip (arrived, not yet departed).
        open_rows = (
            supabase.table("stop_events")
            .select("id, stop_id, arrived_at")
            .eq("trip_id", trip_id)
            .is_("departed_at", "null")
            .execute()
        ).data
        open_by_stop = {
            r["stop_id"]: {"id": r["id"], "arrived_at": _parse_dt(r["arrived_at"])}
            for r in open_rows
        }
        # Most recent ping time known to be inside each open stop. Seed from
        # arrival so a cross-batch departure never predates the arrival.
        last_inside = {sid: ev["arrived_at"] for sid, ev in open_by_stop.items()}

        # CHRONOLOGICAL order is required, or arrival/departure pairing breaks.
        for n in sorted(norm_pings, key=lambda x: x["recorded_dt"]):
            t = n["recorded_dt"]
            for s in stops:
                sid = s["id"]
                inside = (
                    _haversine_m(n["lat"], n["lng"], s["lat"], s["lng"])
                    <= GEOFENCE_RADIUS_M
                )
                if inside:
                    if sid not in open_by_stop:
                        # ARRIVAL: first ping inside a stop with no open event.
                        ev = (
                            supabase.table("stop_events")
                            .insert(
                                {
                                    "trip_id": trip_id,
                                    "org_id": org_id,
                                    "stop_id": sid,
                                    "arrived_at": t.isoformat(),
                                    "departed_at": None,
                                    "confirmed": True,  # geofence-confirmed
                                    "was_short": False,
                                }
                            )
                            .execute()
                        ).data[0]
                        open_by_stop[sid] = {"id": ev["id"], "arrived_at": t}
                        summary["arrivals"] += 1
                    # Arrived now or still sitting inside: advance last-inside.
                    last_inside[sid] = t
                elif sid in open_by_stop:
                    # DEPARTURE: previously inside, this ping is now outside.
                    ev = open_by_stop[sid]
                    departed = last_inside.get(sid, ev["arrived_at"])
                    dwell_sec = (departed - ev["arrived_at"]).total_seconds()
                    required_sec = (s["dwell_minutes"] or 0) * 60
                    was_short = dwell_sec < required_sec  # any shortfall, no grace

                    supabase.table("stop_events").update(
                        {"departed_at": departed.isoformat(), "was_short": was_short}
                    ).eq("id", ev["id"]).execute()
                    summary["departures"] += 1

                    # was_short stays factual on the event; the ALERT is gated by
                    # short_stop rule targeting (legacy always-on if no rule).
                    if was_short and shortstop_applies:
                        detail = (
                            f"Stopped {dwell_sec / 60.0:.1f} min at {s['name']}, "
                            f"required {s['dwell_minutes']} min"
                        )
                        supabase.table("alerts").insert(
                            {
                                "org_id": org_id,
                                "trip_id": trip_id,
                                "driver_id": driver_id,
                                "type": "short_stop",
                                "lat": s["lat"],
                                "lng": s["lng"],
                                "detail": detail,
                                "occurred_at": departed.isoformat(),
                            }
                        ).execute()
                        summary["short_stop_alerts"] += 1

                    del open_by_stop[sid]
                    last_inside.pop(sid, None)

        # Rule-based incident detection (bounded-window, rising-edge, dedup'd).
        _detect_incidents(trip, ordered, rules, stops, summary)
    except Exception as exc:
        # Never let detection trouble fail ping capture.
        summary["error"] = f"detection skipped: {exc}"
    return summary


def _parse_ping_row(r: dict) -> dict:
    """Stored ping row -> internal form with a tz-aware recorded_dt."""
    return {
        "lat": r["lat"], "lng": r["lng"], "speed": r.get("speed"),
        "recorded_dt": _parse_dt(r["recorded_at"]),
    }


def _detect_incidents(trip, ordered_batch, rules, stops, summary) -> None:
    """Bounded-window speeding/off_route detection, correct under out-of-order /
    buffered delivery.

    Rather than seeding from the global-latest ping, we rebuild a small window of
    the trip's STORED pings around this batch's time span:
      * true predecessor — the one ping immediately BEFORE the span (seeds prev),
      * the span itself — batch pings plus any pre-existing pings in that range,
      * one follower immediately AFTER the span — so a ping inserted in the middle
        re-evaluates the ping that now follows it.
    An edge is emitted only if no identical alert already exists in that range
    (replay-dedupe), so re-flushing the same buffered span adds nothing.

    Exact for our adjacent-pair edges (a ping's edge depends only on its immediate
    timestamp predecessor). It is insert-only: it never RETRACTS an earlier alert
    that a late-arriving ping demotes from being an edge (see notes to the user).
    """
    trip_id = trip["id"]
    speeding_rules = [
        r for r in rules
        if r["type"] == "speeding" and r.get("threshold") is not None
        and _rule_applies(r, trip)
    ]
    offroute_rules = [
        r for r in rules
        if r["type"] == "off_route" and r.get("threshold") is not None
        and _rule_applies(r, trip)
    ] if stops else []
    if (not speeding_rules and not offroute_rules) or not ordered_batch:
        return

    min_ts = ordered_batch[0]["recorded_dt"]
    max_ts = ordered_batch[-1]["recorded_dt"]

    def _pings(*filters):
        q = (
            supabase.table("location_pings")
            .select("lat, lng, speed, recorded_at")
            .eq("trip_id", trip_id)
        )
        for col, op, val in filters:
            q = getattr(q, op)(col, val)
        return q

    # True predecessor: latest stored ping strictly before the span.
    pred_rows = _pings(("recorded_at", "lt", min_ts.isoformat())).order(
        "recorded_at", desc=True
    ).limit(1).execute().data
    predecessor = _parse_ping_row(pred_rows[0]) if pred_rows else None

    # Span (batch + any pre-existing in range) and one follower after it.
    span_rows = _pings(
        ("recorded_at", "gte", min_ts.isoformat()),
        ("recorded_at", "lte", max_ts.isoformat()),
    ).order("recorded_at", desc=False).execute().data
    fol_rows = _pings(("recorded_at", "gt", max_ts.isoformat())).order(
        "recorded_at", desc=False
    ).limit(1).execute().data

    window = [_parse_ping_row(r) for r in span_rows] + [
        _parse_ping_row(r) for r in fol_rows
    ]
    window.sort(key=lambda p: p["recorded_dt"])
    if not window:
        return
    window_max = window[-1]["recorded_dt"]

    # Existing alerts in the window range -> replay-dedupe set.
    existing = (
        supabase.table("alerts")
        .select("type, detail, occurred_at")
        .eq("trip_id", trip_id)
        .gte("occurred_at", min_ts.isoformat())
        .lte("occurred_at", window_max.isoformat())
        .execute()
    ).data
    seen = {(a["type"], _parse_dt(a["occurred_at"]), a["detail"]) for a in existing}

    _detect_speeding(trip, window, predecessor, speeding_rules, seen, summary)
    _detect_off_route(trip, window, predecessor, offroute_rules, stops, seen, summary)


def _emit_alert(trip, type_, lat, lng, detail, occurred_dt, seen, summary, counter):
    """Insert an alert unless an identical one already exists (replay-dedupe)."""
    key = (type_, occurred_dt, detail)
    if key in seen:
        return
    supabase.table("alerts").insert(
        {
            "org_id": trip["org_id"],
            "trip_id": trip["id"],
            "driver_id": trip["driver_id"],
            "type": type_,
            "lat": lat,
            "lng": lng,
            "detail": detail,
            "occurred_at": occurred_dt.isoformat(),
        }
    ).execute()
    seen.add(key)
    summary[counter] += 1


def _detect_speeding(trip, window, predecessor, srules, seen, summary) -> None:
    """Rising-edge speeding over the window, one alert per incident per rule.
    `srules` is already filtered to applicable speeding rules with a threshold."""
    for r in srules:
        t = float(r["threshold"])
        prev_over = bool(
            predecessor and predecessor["speed"] is not None
            and predecessor["speed"] > t
        )
        for p in window:
            spd = p["speed"]
            cur_over = spd is not None and spd > t
            if cur_over and not prev_over:
                detail = (
                    f"Speed {spd:.0f} km/h exceeded limit {t:.0f} km/h "
                    f"(rule '{r['name']}')"
                )
                _emit_alert(trip, "speeding", p["lat"], p["lng"], detail,
                            p["recorded_dt"], seen, summary, "speeding_alerts")
            prev_over = cur_over


def _detect_off_route(trip, window, predecessor, orules, stops, seen, summary) -> None:
    """Rising-edge off_route over the window, nearest-stop distance metric.
    `orules` is already filtered to applicable off_route rules with a threshold."""
    if not orules or not stops:
        return
    dists = [(p, _nearest_stop_m(p["lat"], p["lng"], stops)) for p in window]
    prev_dist = (
        _nearest_stop_m(predecessor["lat"], predecessor["lng"], stops)
        if predecessor else None
    )
    for r in orules:
        d_limit = float(r["threshold"])
        prev_over = prev_dist is not None and prev_dist > d_limit
        for p, d in dists:
            cur_over = d > d_limit
            if cur_over and not prev_over:
                detail = (
                    f"Off route by {d:.0f} m (limit {d_limit:.0f} m, "
                    f"rule '{r['name']}')"
                )
                _emit_alert(trip, "off_route", p["lat"], p["lng"], detail,
                            p["recorded_dt"], seen, summary, "off_route_alerts")
            prev_over = cur_over


def _load_org_trip(trip_id: str, org_id: str) -> dict:
    """Manager read guard: trip must exist in the caller's org (else 404)."""
    res = (
        supabase.table("trips")
        .select("id, org_id, route_id")
        .eq("id", trip_id)
        .eq("org_id", org_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No trip with id '{trip_id}' exists in your organization.",
        )
    return res.data[0]


@router.get("/{trip_id}/pings")
def list_trip_pings(
    trip_id: str,
    current_user: dict = Depends(require_permission("manage_trips")),
    limit: int = Query(100, ge=1, le=1000, description="Max pings, newest first."),
):
    org_id = current_user["org_id"]
    _load_org_trip(trip_id, org_id)  # org-scope guard

    result = (
        supabase.table("location_pings")
        .select("id, lat, lng, speed, heading, recorded_at, created_at")
        .eq("trip_id", trip_id)
        .eq("org_id", org_id)
        .order("recorded_at", desc=True)  # newest first, for the live path
        .limit(limit)
        .execute()
    )
    return {"count": len(result.data), "trip_id": trip_id, "pings": result.data}


@router.get("/{trip_id}/stop-events")
def list_trip_stop_events(
    trip_id: str,
    current_user: dict = Depends(require_permission("manage_trips")),
):
    org_id = current_user["org_id"]
    _load_org_trip(trip_id, org_id)  # org-scope guard

    events = (
        supabase.table("stop_events")
        .select("id, stop_id, arrived_at, departed_at, confirmed, was_short")
        .eq("trip_id", trip_id)
        .eq("org_id", org_id)
        .order("arrived_at", desc=False)
        .execute()
    ).data

    # Attach readable stop names.
    stop_ids = {e["stop_id"] for e in events if e.get("stop_id")}
    names = {}
    if stop_ids:
        rows = (
            supabase.table("route_stops")
            .select("id, name")
            .in_("id", list(stop_ids))
            .execute()
        ).data
        names = {r["id"]: r["name"] for r in rows}

    enriched = [
        {
            "id": e["id"],
            "stop_id": e["stop_id"],
            "stop_name": names.get(e["stop_id"]),
            "arrived_at": e["arrived_at"],
            "departed_at": e["departed_at"],
            "confirmed": e["confirmed"],
            "was_short": e["was_short"],
        }
        for e in events
    ]
    return {"count": len(enriched), "trip_id": trip_id, "stop_events": enriched}


@router.get("")
def list_trips(
    current_user: dict = Depends(require_permission("manage_trips")),
    status_filter: Optional[str] = Query(
        None, alias="status", description="Filter by status: active|completed|scheduled|cancelled"
    ),
    trip_date: Optional[date] = Query(
        None, alias="date", description="Filter by the date of started_at (YYYY-MM-DD)."
    ),
):
    org_id = current_user["org_id"]

    query = supabase.table("trips").select("*").eq("org_id", org_id)

    if status_filter:
        query = query.eq("status", status_filter)

    if trip_date is not None:
        # started_at is a timestamp; match the whole calendar day [day, day+1).
        day_start = datetime.combine(trip_date, time.min, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)
        query = query.gte("started_at", day_start.isoformat()).lt(
            "started_at", day_end.isoformat()
        )

    result = query.execute()
    trips = result.data

    # Active first, then newest by started_at. Python's sort is stable, so we
    # sort by the secondary key (started_at desc) first, then the primary
    # (active before everything else).
    trips.sort(key=lambda t: t.get("started_at") or "", reverse=True)
    trips.sort(key=lambda t: 0 if t["status"] == "active" else 1)

    enriched = _enrich_many(trips)
    return {"count": len(enriched), "trips": enriched}


# --- enrichment helpers (batched name lookups) ---------------------------------

def _enrich_one(trip: dict) -> dict:
    """Enrich a single trip with driver/route/vehicle readable fields."""
    return _enrich_many([trip])[0]


def _enrich_many(trips: list) -> list:
    """Batch-enrich trips: one lookup per related table, joined in memory."""
    if not trips:
        return []

    driver_ids = {t["driver_id"] for t in trips if t.get("driver_id")}
    route_ids = {t["route_id"] for t in trips if t.get("route_id")}
    vehicle_ids = {t["vehicle_id"] for t in trips if t.get("vehicle_id")}

    drivers, routes, vehicles = {}, {}, {}
    if driver_ids:
        rows = supabase.table("profiles").select("id, name").in_("id", list(driver_ids)).execute()
        drivers = {r["id"]: r["name"] for r in rows.data}
    if route_ids:
        rows = supabase.table("routes").select("id, name").in_("id", list(route_ids)).execute()
        routes = {r["id"]: r["name"] for r in rows.data}
    if vehicle_ids:
        rows = (
            supabase.table("vehicles")
            .select("id, bus_number, share_token")
            .in_("id", list(vehicle_ids))
            .execute()
        )
        vehicles = {r["id"]: r for r in rows.data}

    out = []
    for t in trips:
        v = vehicles.get(t.get("vehicle_id"), {})
        out.append(
            _enrich(
                t,
                driver_name=drivers.get(t.get("driver_id")),
                route_name=routes.get(t.get("route_id")),
                vehicle_bus_number=v.get("bus_number"),
                share_token=v.get("share_token"),
            )
        )
    return out
