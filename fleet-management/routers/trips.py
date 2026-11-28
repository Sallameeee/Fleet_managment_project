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

import features as feature_flags
import gps_filter
import live_status
import progress_runtime
import trip_events
import notifications_logic as notify
import trip_lifecycle as lifecycle
from alert_text import alert_meta
from log_settings_logic import effective_log_settings
from auth import require_permission, require_role
from capacity_logic import effective_roster, org_module
from database import is_transient, retry_transient, supabase

router = APIRouter(prefix="/trips", tags=["trips"])

SCHEDULE_GRACE_MIN = 5  # arrival within 5 min of the scheduled time counts as on-time

# A trip can be started at most this many minutes BEFORE its scheduled start.
# Being late is always fine (no upper bound). No scheduled time -> no gate.
START_EARLY_WINDOW_MIN = 15

# Geofence radius (meters) for auto arrival/departure detection. Named so it's
# a one-line tune. A ping within this distance of a stop counts as "at" it.
GEOFENCE_RADIUS_M = 75

# Long-stop detection (SHARED, school + university): a bus that stays within
# LONG_STOP_JITTER_M of one point (and reports ~0 speed) for longer than the
# org's threshold, while NOT within GEOFENCE_RADIUS_M of any scheduled route
# stop, raises one `long_stop` alert. The threshold lives in
# organizations.long_stop_minutes (migration 040); this default applies when the
# column is missing or NULL. 0 disables detection for the org.
LONG_STOP_DEFAULT_MIN = 5
LONG_STOP_JITTER_M = 30      # GPS wobble tolerated while "standing still"
LONG_STOP_MOVING_MPS = 1.5   # a fix faster than this (~5 km/h) ends the stand-still

# "Today" for a driver's assignments is LOCAL (Africa/Cairo, DST-aware) — see the
# single shared definition in capacity_logic.
from capacity_logic import LOCAL_TZ


def _parse_hhmm(value) -> Optional[time]:
    """Parse a DB time value ('HH:MM' / 'HH:MM:SS') into a time, or None."""
    if value is None or value == "":
        return None
    if isinstance(value, time):
        return value
    s = str(value)
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    return None


def _start_gate(scheduled_start, trip_date: Optional[str]) -> dict:
    """Whether a trip may be started now, given its LOCAL scheduled start time.

    A trip unlocks 15 minutes before the scheduled start and stays unlocked
    afterwards (late is fine). With no scheduled time there is no gate.

    Returns a small dict the caller (endpoint or payload) can use directly:
      * scheduled_start_time  -> 'HH:MM' or None
      * earliest_start_time   -> 'HH:MM' or None  (scheduled - 15 min)
      * can_start_now         -> bool
    All times are Africa/Cairo (LOCAL_TZ) — never a fixed offset.
    """
    t = _parse_hhmm(scheduled_start)
    if t is None or not trip_date:
        return {"scheduled_start_time": None, "earliest_start_time": None, "can_start_now": True}
    try:
        day = date.fromisoformat(str(trip_date)[:10])
    except ValueError:
        return {"scheduled_start_time": None, "earliest_start_time": None, "can_start_now": True}
    scheduled_local = datetime.combine(day, t, tzinfo=LOCAL_TZ)
    earliest_local = scheduled_local - timedelta(minutes=START_EARLY_WINDOW_MIN)
    now_local = datetime.now(LOCAL_TZ)
    return {
        "scheduled_start_time": scheduled_local.strftime("%H:%M"),
        "earliest_start_time": earliest_local.strftime("%H:%M"),
        "can_start_now": now_local >= earliest_local,
    }


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
    # Range-checked: a fix outside the globe (lat 95, lng 200) is not a
    # position and would draw garbage on every map — 422 instead of storing it.
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    speed: Optional[float] = None
    heading: Optional[float] = None
    # Device timestamp. Defaults to server now() at insert time if missing.
    recorded_at: Optional[datetime] = None
    # Phone state at the moment of the fix (optional, migration 046): battery %
    # and the app's own view of its network (online | weak | offline). Feeds the
    # trip-lifecycle end-reason inference (trip_lifecycle.py).
    battery: Optional[int] = Field(None, ge=0, le=100)
    net_state: Optional[str] = Field(None, max_length=16)
    # Horizontal accuracy (m) of the fix, when the phone reports it (048). Feeds
    # the GPS filter's accuracy allowance and the low_accuracy tag.
    accuracy: Optional[float] = Field(None, ge=0, le=1_000_000)


class HeartbeatIn(BaseModel):
    """One "app alive" beacon — SEPARATE from GPS so the server can tell "GPS
    weak but app+net alive" from "app/net gone". Batched like pings."""
    sent_at: datetime
    battery: Optional[int] = Field(None, ge=0, le=100)
    net_state: Optional[str] = Field(None, max_length=16)


class AppStateIn(BaseModel):
    """Best-effort Flutter lifecycle beacon: detached (closed / swiped away),
    paused (backgrounded), resumed."""
    state: str = Field(..., pattern="^(detached|paused|resumed)$")
    at: Optional[datetime] = None
    battery: Optional[int] = Field(None, ge=0, le=100)
    net_state: Optional[str] = Field(None, max_length=16)


# A device clock that runs AHEAD of real time produces fixes "from the future".
# Anything more than this ahead of the server clock is unambiguously wrong: it
# is stored raw but tagged is_outlier (reject_reason 'future_timestamp') so no
# map/report/detector ever consumes it (a single such fix otherwise raises a
# bogus "offline for N million minutes" alert and stretches History replays).
FUTURE_SKEW_TOLERANCE = timedelta(minutes=10)
# After a driver ends a trip, the phone may still hold buffered fixes recorded
# BEFORE the end (a flush that lost the race with /end, or an offline tail).
# Those are genuine data from the trip, so a completed trip keeps accepting
# fixes recorded up to its ended_at (+ tolerance). Anything later is refused.
COMPLETED_TRIP_GRACE = timedelta(seconds=90)


class StopVisitIn(BaseModel):
    """The app reports reaching a stop (arrival) and, on departure, how long it
    actually stayed. Called once on arrival (departure omitted) and again on
    departure (with departure_time). Idempotent per (trip, stop).

    `skipped=true` records that the bus PASSED this stop without stopping (the
    driver reached a later stop) — SHARED behaviour for both modules. arrival_time
    then carries the moment it was passed; no departure/dwell, and no arrival
    notification is sent."""
    stop_id: str = Field(..., min_length=1)
    arrival_time: datetime
    departure_time: Optional[datetime] = None
    skipped: bool = False


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
    # The start-time gate + "directions to start" are SCHOOL features. University
    # keeps its exact previous behaviour (start any time, no directions button):
    # the extra fields are simply omitted for non-school orgs.
    is_school = _org_module(org_id) == "school"

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
            x["id"]: x
            for x in _route_rows(route_ids)
        }
    if vehicle_ids:
        vehicles = {
            x["id"]: x
            for x in supabase.table("vehicles").select("id, bus_number").in_("id", vehicle_ids).execute().data
        }

    stop_counts: dict = {}
    if route_ids:
        try:
            for st in supabase.table("route_stops").select("route_id").in_("route_id", route_ids).execute().data:
                stop_counts[st["route_id"]] = stop_counts.get(st["route_id"], 0) + 1
        except Exception:
            stop_counts = {}

    # First stop per route (for the "Directions to start point" button) — school only.
    first_stops: dict = {}
    if is_school and route_ids:
        for st in (
            supabase.table("route_stops")
            .select("route_id, name, lat, lng, stop_order")
            .in_("route_id", route_ids)
            .order("stop_order", desc=False)
            .execute()
            .data
        ):
            first_stops.setdefault(st["route_id"], st)  # ordered asc -> first wins

    assignments = []
    for r in rows:
        route = routes.get(r.get("route_id")) or {}
        item = {
            "assignment_id": r["id"],
            "route_id": r.get("route_id"),
            "route_name": route.get("name"),
            "vehicle_id": r.get("vehicle_id"),
            "vehicle_bus_number": (vehicles.get(r.get("vehicle_id")) or {}).get("bus_number"),
            "trip_date": r.get("trip_date"),
            "shift_label": r.get("shift_label"),
            "start_time": r.get("start_time"),
            "end_time": r.get("end_time"),
            # Display extras for the app's assignment card (read-only).
            "stop_count": stop_counts.get(r.get("route_id"), 0),
            "route_total_km": route.get("total_km"),
            "route_est_minutes": route.get("est_minutes"),
        }
        if is_school:
            # Scheduled start = the ASSIGNMENT's start_time (per-day), falling back
            # to the ROUTE's default start_time. The 15-min gate is computed from
            # it (Africa/Cairo). University orgs never get these fields, so the app
            # leaves the button ungated exactly as before.
            gate = _start_gate(r.get("start_time") or route.get("start_time"), r.get("trip_date"))
            fs = first_stops.get(r.get("route_id"))
            item.update(
                {
                    "scheduled_start_time": gate["scheduled_start_time"],
                    "earliest_start_time": gate["earliest_start_time"],
                    "can_start_now": gate["can_start_now"],
                    "first_stop": (
                        {"name": fs.get("name"), "lat": fs.get("lat"), "lng": fs.get("lng")}
                        if fs else None
                    ),
                }
            )
        assignments.append(item)

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
    return {
        "date": today,
        "module": _org_module(org_id),
        "enabled_features": sorted(feature_flags.org_enabled_features(org_id)),
        "assignments": assignments,
        "active_trip": active_trip,
    }


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

    # --- 2b. Time gate (SCHOOL only): no starting more than 15 min before the
    # scheduled start. Scheduled start = the assignment's start_time, falling back
    # to the route's default. Late is always allowed; no scheduled time = no gate.
    # University keeps its exact prior behaviour (start any time). Enforced here so
    # the server and the app can never disagree. ---
    if _org_module(org_id) == "school":
        scheduled = assignment.get("start_time")
        if not scheduled and assignment.get("route_id"):
            _r = (
                supabase.table("routes")
                .select("start_time")
                .eq("id", assignment["route_id"])
                .eq("org_id", org_id)
                .limit(1)
                .execute()
                .data
            )
            scheduled = _r[0].get("start_time") if _r else None
        gate = _start_gate(scheduled, assignment.get("trip_date"))
        if not gate["can_start_now"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This trip starts at {gate['scheduled_start_time']} — you can start "
                    f"it from {gate['earliest_start_time']}."
                ),
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
        msg = str(exc)
        # Two starts racing (double tap, two phones, a retried request) both
        # pass the check above; the partial unique indexes from migration 044
        # (one ACTIVE trip per driver / per assignment) reject the loser here.
        # Answer exactly like the guard would have: 409 + the trip that won.
        if "uq_trips_one_active" in msg or "duplicate key" in msg:
            won = (
                supabase.table("trips")
                .select("*")
                .eq("org_id", org_id)
                .eq("status", "active")
                .or_(f"driver_id.eq.{driver_id},assignment_id.eq.{body.assignment_id}")
                .limit(1)
                .execute()
            ).data
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "You already have an active trip. End it before starting another.",
                    "active_trip": _enrich_one(won[0]) if won else None,
                },
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not start trip: {exc}",
        )

    trip = result.data[0]
    # School only, best-effort: make sure the org has default speeding/off-route
    # rules so the Logs feed collects events (detection is a no-op without a rule).
    # Idempotent; never overrides a manager's own/edited rules; University untouched.
    from routers.alert_rules import ensure_default_alert_rules

    ensure_default_alert_rules(org_id)
    # School only, best-effort: tell each parent whose child is on this bus today
    # that "<child>'s bus has started" (deduped per trip+parent).
    notify.trip_started(trip)
    # Trip-lifecycle log point (shared): TRIP STARTED — marker patched in by the first accepted fix.
    lifecycle.on_trip_started(trip)
    return _enrich(
        trip,
        driver_name=current_user.get("name"),
        route_name=route_name,
        vehicle_bus_number=vehicle["bus_number"],
        share_token=vehicle["share_token"],
    )


class EndTripIn(BaseModel):
    # When the driver pressed End Trip (phone clock). The app sends it always; it
    # matters when the End was pressed OFFLINE: the app keeps it queued, uploads
    # the buffered fixes once the connection returns, then delivers this End with
    # the original time. Optional — older builds send {} and the server's clock
    # is used, as before.
    ended_at: Optional[datetime] = None


def _driver_end_time(trip: dict, body: Optional[EndTripIn], now: datetime) -> datetime:
    """The trip's end: the phone's End-pressed time clamped to [started_at, now]
    (a wrong phone clock can neither end a trip before it began nor in the
    future), else now."""
    if not body or body.ended_at is None:
        return now
    dt = body.ended_at if body.ended_at.tzinfo else body.ended_at.replace(tzinfo=timezone.utc)
    started = gps_filter.parse_dt(trip.get("started_at")) or now
    return max(started, min(dt, now))


@router.post("/{trip_id}/end")
def end_trip(
    trip_id: str,
    body: Optional[EndTripIn] = None,
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

    now = datetime.now(timezone.utc)
    end_dt = _driver_end_time(trip, body, now)

    # Idempotent-ish: if it's already finished, just report that (no error).
    if trip["status"] == "completed":
        # EXCEPT an End pressed OFFLINE that arrives after the sweeper auto-closed
        # the trip for silence with a GUESSED reason: the driver's own End (with
        # the time it was pressed) replaces the guess — a normal end. A trip that
        # already ended normally is never rewritten (repeat deliveries are no-ops).
        if body is not None and body.ended_at is not None:
            revised = lifecycle.revise_to_driver_end(trip, at=end_dt, reported_at=now)
            if revised:
                progress_runtime.finalize(revised, end_dt)  # stops never reached -> skipped (shared)
                _compute_trip_performance(revised)  # school only; the backlog may add stop events
                enriched = _enrich_one(revised)
                enriched["message"] = "Recorded your End Trip with the time you pressed it."
                return enriched
        enriched = _enrich_one(trip)
        enriched["message"] = "This trip was already completed."
        return enriched
    if trip["status"] == "cancelled":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This trip was cancelled and cannot be ended.",
        )

    # Complete it — at the moment the driver pressed End Trip (see EndTripIn).
    try:
        upd = (
            supabase.table("trips")
            .update({"status": "completed", "ended_at": end_dt.isoformat()})
            .eq("id", trip_id)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not end trip: {exc}",
        )

    # Shared stop progression: close the stop the bus is at; every stop never
    # reached is recorded as skipped with its closest approach (decision log).
    progress_runtime.finalize(upd.data[0], end_dt)
    # School only, best-effort: compute + persist this trip's performance metrics.
    _compute_trip_performance(upd.data[0])
    # Trip-lifecycle log point (shared): TRIP ENDED — normally, by the driver.
    lifecycle.on_trip_ended(upd.data[0], "normal", at=end_dt, reported_at=now)
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


def cancel_active_trips(org_id: str, *, assignment_id: Optional[str] = None,
                        trip_id: Optional[str] = None, driver_id: Optional[str] = None) -> list:
    """Mark every ACTIVE trip matching the filter as `cancelled` (org-scoped).

    SHARED (school + university): this is THE cancel mechanism. It is called
    when a manager deletes an assignment that is currently being driven, and by
    POST /trips/{id}/cancel. Once a trip is `cancelled`:
      * the driver app's pings are rejected (403 by _load_own_active_trip),
      * GET /trips/{id}/status reports `cancelled` so the app resets itself,
      * /my-assignments no longer returns it as `active_trip`.
    Returns the updated trip rows (possibly empty). Never raises — a failure
    here must not block the caller's own operation (e.g. the assignment delete).
    """
    try:
        q = (
            supabase.table("trips")
            .update({"status": "cancelled", "ended_at": _now_iso()})
            .eq("org_id", org_id)
            .eq("status", "active")
        )
        if assignment_id is not None:
            q = q.eq("assignment_id", assignment_id)
        if trip_id is not None:
            q = q.eq("id", trip_id)
        if driver_id is not None:
            # A driver being DEACTIVATED: their running trip can never be ended
            # from the app again (every call is 403), so close it out here.
            q = q.eq("driver_id", driver_id)
        if assignment_id is None and trip_id is None and driver_id is None:
            return []  # never cancel an org's every trip by accident
        rows = q.execute().data or []
        reason = "driver_deactivated" if driver_id is not None else "cancelled"
        for row in rows:  # log point: TRIP ENDED (cancelled / driver deactivated) — never blocks the caller
            lifecycle.on_trip_ended(row, reason, at=gps_filter.parse_dt(row.get("ended_at")))
        return rows
    except Exception:
        return []


@router.post("/{trip_id}/cancel")
def cancel_trip(
    trip_id: str,
    current_user: dict = Depends(require_permission("manage_trips")),
):
    """Manager cancels a running trip (org-scoped). Idempotent: an already
    completed/cancelled trip is returned unchanged with a message."""
    org_id = current_user["org_id"]
    rows = (
        supabase.table("trips").select("*").eq("id", trip_id).eq("org_id", org_id).limit(1).execute().data
    )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No trip with id '{trip_id}' exists in your organization.",
        )
    trip = rows[0]
    if trip["status"] != "active":
        enriched = _enrich_one(trip)
        enriched["message"] = f"This trip is already {trip['status']}."
        return enriched
    rows = cancel_active_trips(org_id, trip_id=trip_id)
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not cancel trip.",
        )
    return _enrich_one(rows[0])


@router.get("/{trip_id}/status")
def trip_status(trip_id: str, current_user: dict = Depends(require_role("driver"))):
    """Lightweight liveness check for the driver app's running trip. Unlike the
    ping path this answers 200 for ANY status so the app can tell `cancelled`
    (manager cancelled it -> reset the UI) from `completed`. 404 if the trip
    does not exist in the caller's org (or is another driver's — same message
    so ids can't be probed)."""
    org_id = current_user["org_id"]
    rows = (
        supabase.table("trips")
        .select("id, driver_id, status, ended_at")
        .eq("id", trip_id)
        .eq("org_id", org_id)
        .limit(1)
        .execute()
        .data
    )
    if not rows or rows[0]["driver_id"] != current_user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No trip with id '{trip_id}' exists in your organization.",
        )
    row = rows[0]
    # A trip the sweeper auto-closed for SILENCE (app_closed / device_power /
    # uncertain) was never really over — the phone was offline, and its next
    # newer fix reopens it (see post_pings). Report it as still ACTIVE to the
    # driver app: otherwise the app's 15 s status watcher can see 'completed' a
    # moment before its offline backlog reaches the server, reset itself and
    # DROP the buffered fixes — a whole offline stretch of the trip lost.
    if row["status"] == "completed":
        try:
            sig = lifecycle.load_signals(trip_id)
            if sig.get("end_reason") in lifecycle.INFERRED_REASONS:
                return {"id": row["id"], "status": "active", "ended_at": None,
                        "auto_closed_at": row.get("ended_at")}
        except Exception:
            pass
    return {"id": row["id"], "status": row["status"], "ended_at": row.get("ended_at")}


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


def _load_own_trip_for_pings(trip_id: str, current_user: dict) -> dict:
    """Ping-path variant of _load_own_active_trip: ACTIVE trips as before, and a
    COMPLETED trip is returned too (with ended_at) so post_pings can accept the
    backlog recorded before it ended. Cancelled / other statuses stay 403."""
    org_id = current_user["org_id"]
    result = (
        supabase.table("trips")
        .select("id, org_id, driver_id, route_id, vehicle_id, status, started_at, ended_at")
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
    if trip["driver_id"] != current_user["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This trip belongs to another driver. You can only ping your own.",
        )
    if trip["status"] not in ("active", "completed"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This trip is '{trip['status']}', not active. Only active trips accept pings.",
        )
    return trip


def _store_pings(to_store: list, row_fn) -> int:
    """Insert fixes so that a row already present for (trip_id, recorded_at) is
    SKIPPED, never the whole batch. Two devices (or a retried flush) posting
    overlapping-but-different batches used to collide on migration 042's unique
    index and the loser's ENTIRE batch was dropped while the app got a 201 and
    deleted it from its buffer — silent loss. Returns the number of rows stored.

    1. upsert ... ON CONFLICT DO NOTHING (needs 042's unique index) — one
       round-trip, returns exactly the rows that were new.
    2. 042 missing -> plain insert (the pre-check above already skipped
       stored timestamps); a race that still collides falls to
    3. row-by-row insert, skipping only the duplicate rows.
    Each step retries once WITHOUT the outlier-tag columns when 041 is missing.
    """
    tag = True

    def _rows():
        return [row_fn(n, tag) for n in to_store]

    def _missing_tag_cols(msg: str) -> bool:
        return "is_outlier" in msg or "reject_reason" in msg or "implied_kmh" in msg

    def _is_dup(msg: str) -> bool:
        return "duplicate key" in msg or "uq_location_pings" in msg

    # 1. upsert, ignoring rows whose (trip_id, recorded_at) already exists.
    # Idempotent, so a transient connection drop is simply retried; if it keeps
    # failing the caller gets 503 (the app keeps its buffer and retries later).
    for _ in range(2):
        try:
            res = retry_transient(lambda: (
                supabase.table("location_pings")
                .upsert(_rows(), on_conflict="trip_id,recorded_at", ignore_duplicates=True)
                .execute()
            ))
            return len(res.data)
        except Exception as exc:
            msg = str(exc)
            if tag and _missing_tag_cols(msg):
                tag = False
                continue
            if "unique or exclusion constraint" in msg or "42P10" in msg:
                break  # migration 042 not applied: no conflict target -> plain insert
            if is_transient(exc):
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                    detail=f"Could not record pings right now (temporary): {msg}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not record pings: {msg}")

    # 2. plain insert (pre-042 databases)
    for _ in range(2):
        try:
            return len(supabase.table("location_pings").insert(_rows()).execute().data)
        except Exception as exc:
            msg = str(exc)
            if tag and _missing_tag_cols(msg):
                tag = False
                continue
            if _is_dup(msg):
                break  # collided with a concurrent flush: keep the non-duplicates
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not record pings: {msg}")

    # 3. row by row, skipping only the rows that already exist
    stored = 0
    for n in to_store:
        try:
            stored += len(supabase.table("location_pings").insert(row_fn(n, tag)).execute().data)
        except Exception as exc:
            if not _is_dup(str(exc)):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not record pings: {exc}")
    return stored


@router.post("/{trip_id}/pings", status_code=status.HTTP_201_CREATED)
def post_pings(
    trip_id: str,
    body: Union[PingIn, List[PingIn]],
    current_user: dict = Depends(require_role("driver")),
):
    """Ingest one fix or an offline backlog batch (original device timestamps
    are stored as-is). The shared GPS plausibility rule (gps_filter.py) runs
    here ONCE for every consumer: exact duplicates are not stored, physically
    impossible fixes are stored RAW but tagged is_outlier (migration 041; before
    it they are stored untagged and every reader re-filters), and detection
    only ever sees clean fixes."""
    # Ownership + state check (one indexed lookup). org_id/driver_id are taken
    # from the trip/token below — never from the request body.
    trip = _load_own_trip_for_pings(trip_id, current_user)
    org_id = trip["org_id"]
    driver_id = trip["driver_id"]

    pings = body if isinstance(body, list) else [body]
    if not pings:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No pings provided.")

    # Normalize device timestamps to tz-aware UTC datetimes once, so the same
    # values feed both the insert and the chronological detection pass.
    server_now = datetime.now(timezone.utc)
    norm = []
    naive_local = 0
    missing_ts = 0
    for p in pings:
        dt = p.recorded_at
        if dt is None:
            dt = server_now
            missing_ts += 1
        if dt.tzinfo is None:
            dt, was_local = _resolve_naive(dt, server_now)
            naive_local += was_local
        norm.append({"lat": p.lat, "lng": p.lng, "speed": p.speed, "heading": p.heading,
                     "battery": p.battery, "net_state": p.net_state, "accuracy": p.accuracy,
                     "recorded_dt": dt, "recorded_at": dt.isoformat()})
    norm.sort(key=lambda n: n["recorded_dt"])
    min_ts, max_ts = norm[0]["recorded_dt"], norm[-1]["recorded_dt"]
    # Phone state travels with the newest fix of the batch.
    phone_battery = next((n["battery"] for n in reversed(norm) if n.get("battery") is not None), None)
    phone_net = next((n["net_state"] for n in reversed(norm) if n.get("net_state")), None)

    # A COMPLETED trip only takes the backlog that was recorded while it ran
    # (see COMPLETED_TRIP_GRACE). Later fixes mean the phone is still tracking
    # a trip that ended elsewhere — 403, so the app resets itself as before.
    # EXCEPTION (trip lifecycle): a trip the SWEEPER auto-closed for silence
    # (end_reason app_closed / device_power / uncertain) whose phone comes back
    # with NEWER fixes was never over — it was a network outage. Reopen it and
    # keep tracking; the connection_restored log point explains the gap.
    reopened = False
    if trip["status"] == "completed":
        ended = gps_filter.parse_dt(trip.get("ended_at")) if trip.get("ended_at") else server_now
        sig = lifecycle.load_signals(trip_id)
        inferred = sig.get("end_reason") in lifecycle.INFERRED_REASONS
        if max_ts > ended + COMPLETED_TRIP_GRACE:
            if inferred:
                try:
                    back = (
                        supabase.table("trips").update({"status": "active", "ended_at": None})
                        .eq("id", trip_id).eq("status", "completed").execute()
                    ).data
                except Exception:
                    back = []  # e.g. 044: the driver already runs another trip
                if back:
                    reopened = True
                    trip["status"] = "active"
                    lifecycle.update_trip(trip_id, {"end_reason": None, "conn_lost_at": ended.isoformat(),
                                                    "end_detail": {**(sig.get("end_detail") or {}), "reopened_at": server_now.isoformat(),
                                                                   "revised_from": sig.get("end_reason")}})
                    ev = lifecycle._find_event(trip_id, "trip_ended")
                    if ev:  # that end never happened
                        try:
                            supabase.table("alerts").delete().eq("id", ev["id"]).execute()
                        except Exception:
                            pass
            if not reopened:
                trip_events.emit(trip, "pings_refused", f"{len(norm)} fix(es) refused: trip is completed and they were recorded after it ended",
                                 {"received": len(norm), "ended_at": ended.isoformat(), "newest_fix": max_ts.isoformat(),
                                  "grace_s": COMPLETED_TRIP_GRACE.total_seconds(), "http_status": 403})
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="This trip is 'completed', not active. Only active trips accept pings.",
                )
        elif inferred:
            # Late backlog inside the grace window: the silence was the network.
            lifecycle.on_signal_after_close(trip, kind="backlog", at=server_now)

    # 0. Clock sanity: fixes from the future are tagged, never trusted.
    future_cut = server_now + FUTURE_SKEW_TOLERANCE
    future = [n for n in norm if n["recorded_dt"] > future_cut]
    for n in future:
        n["reject_reason"] = "future_timestamp"
        n["implied_kmh"] = None
        n["why"] = {"ahead_s": round((n["recorded_dt"] - server_now).total_seconds(), 1),
                    "server_now": server_now.isoformat(), "tolerance_s": FUTURE_SKEW_TOLERANCE.total_seconds()}
    norm = [n for n in norm if n["recorded_dt"] <= future_cut]
    if norm:
        min_ts, max_ts = norm[0]["recorded_dt"], norm[-1]["recorded_dt"]

    # 1. Fixes already stored for these timestamps (a re-sent buffer row, or two
    #    flushes racing) are not new data — skip them. Migration 042's unique
    #    index makes this airtight; this check covers the pre-migration case.
    existing_ts: set = set()
    if norm:
        try:
            have = (
                supabase.table("location_pings").select("recorded_at").eq("trip_id", trip_id)
                .gte("recorded_at", min_ts.isoformat()).lte("recorded_at", max_ts.isoformat())
                .limit(5000).execute().data
            )
            existing_ts = {gps_filter.parse_dt(r["recorded_at"]) for r in have}
        except Exception:
            pass
    fresh = [n for n in norm if n["recorded_dt"] not in existing_ts]
    skipped_stored = len(norm) - len(fresh)

    # 2. Judge the batch against the last ACCEPTED stored fix (real history),
    #    so an offline backlog is filtered exactly like live fixes.
    anchor = _last_accepted_before(trip_id, min_ts) if norm else None
    accepted, rejected = gps_filter.filter_fixes(fresh, anchor=anchor, trip_started_at=trip.get("started_at")) if fresh else ([], [])
    dup_in_batch = [r for r in rejected if r["reject_reason"] == "duplicate"]
    outliers = [r for r in rejected if r["reject_reason"] != "duplicate"] + future

    def _row(n: dict, tag: bool) -> dict:
        row = {
            "trip_id": trip_id, "org_id": org_id, "driver_id": driver_id,
            "lat": n["lat"], "lng": n["lng"], "speed": n["speed"], "heading": n["heading"],
            "recorded_at": n["recorded_dt"].isoformat(),
        }
        if _ping_state_cols():
            row["battery"] = n.get("battery")
            row["net_state"] = n.get("net_state")
        if accuracy_col:
            row["accuracy"] = n.get("accuracy")
        if tag:
            row["is_outlier"] = bool(n.get("reject_reason"))
            row["reject_reason"] = n.get("reject_reason")
            row["implied_kmh"] = n.get("implied_kmh")
        return row

    accuracy_col = lifecycle.has_col("location_pings", "accuracy")
    to_store = accepted + outliers  # raw outliers kept (tagged); duplicates are not data
    stored = 0
    if to_store:
        stored = _store_pings(to_store, _row)
    if accepted:
        _bump_last_fix(trip_id, max(a["recorded_dt"] for a in accepted))

    # --- Trip decision log: one summary row per request + one row per rejected
    # fix (capped) with the values that triggered the decision. ---
    _log_ping_batch(trip, norm_count=len(pings), accepted=accepted, outliers=outliers,
                    dup_in_batch=dup_in_batch, skipped_stored=skipped_stored, stored=stored,
                    anchor=anchor, server_now=server_now, naive_local=naive_local, missing_ts=missing_ts,
                    phone_battery=phone_battery, phone_net=phone_net, reopened=reopened)

    # --- Detection on the CLEAN, new fixes only: geofence stop events, offline
    # gaps, speeding, off_route, long_stop. Best-effort (never fails capture). ---
    detection = _process_pings(trip, accepted, anchor=anchor) if accepted else {"skipped": "no new accepted fixes"}
    # Shared stop progression (next stop / arrived / passed / skipped).
    progress = progress_runtime.apply_fixes(trip, accepted) if accepted else None

    # Trip lifecycle (shared): close an open connection-loss episode (CONNECTION
    # RESTORED), give TRIP STARTED its marker on the first fix, record the
    # phone's state (last signal / battery / net) for the end-reason inference.
    restored = lifecycle.on_pings_arrived(
        trip, accepted, now=server_now, battery=phone_battery, net_state=phone_net,
        first_fix=(anchor is None and trip["status"] == "active"),
    )
    if restored or reopened:
        detection = {**(detection if isinstance(detection, dict) else {}),
                     "connection_restored": bool(restored), "trip_reopened": reopened}

    return {
        "recorded": stored,
        "accepted": len(accepted),
        "rejected": [{"recorded_at": r["recorded_at"], "reason": r["reject_reason"], "implied_kmh": r.get("implied_kmh")} for r in outliers],
        "duplicates_skipped": skipped_stored + len(dup_in_batch),
        "trip_id": trip_id,
        "detection": detection,
        # Shared stop progression after this batch (None when unchanged/unavailable).
        "progress": progress,
    }


def _resolve_naive(dt: datetime, server_now: datetime) -> tuple:
    """See gps_filter.resolve_naive_timestamp (shared, unit-tested)."""
    return gps_filter.resolve_naive_timestamp(dt, server_now, LOCAL_TZ, FUTURE_SKEW_TOLERANCE)


def _bump_last_fix(trip_id: str, newest: datetime) -> None:
    """trips.last_fix_at = max(current, newest accepted fix) — live-state input."""
    if not lifecycle.has_col("trips", "last_fix_at"):
        return
    iso = newest.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")  # no '+' in the filter
    try:
        (supabase.table("trips").update({"last_fix_at": iso}).eq("id", trip_id)
         .or_(f"last_fix_at.is.null,last_fix_at.lt.{iso}").execute())
    except Exception:
        pass


MAX_REJECT_EVENTS_PER_BATCH = 50


def _log_ping_batch(trip: dict, *, norm_count: int, accepted: list, outliers: list, dup_in_batch: list,
                    skipped_stored: int, stored: int, anchor, server_now: datetime, naive_local: int,
                    missing_ts: int, phone_battery, phone_net, reopened: bool) -> None:
    from collections import Counter
    reasons = Counter(o["reject_reason"] for o in outliers)
    stale = sum(1 for a in accepted if a.get("stale_repeat"))
    span = [a["recorded_dt"] for a in accepted] + [o["recorded_dt"] for o in outliers]
    lag = [(server_now - a["recorded_dt"]).total_seconds() for a in accepted]
    payload = {
        "received": norm_count, "accepted": len(accepted), "stored": stored,
        "rejected": dict(reasons), "duplicates_in_batch": len(dup_in_batch), "already_stored": skipped_stored,
        "stale_repeats": stale, "reanchored": sum(1 for a in accepted if a.get("reanchored")),
        "first_fix_at": min(span).isoformat() if span else None, "last_fix_at": max(span).isoformat() if span else None,
        "max_lag_s": round(max(lag), 1) if lag else None, "server_now": server_now.isoformat(),
        "anchor_at": str(anchor.get("recorded_at")) if anchor else None,
        "naive_local_timestamps_fixed": naive_local, "missing_timestamps": missing_ts,
        "battery": phone_battery, "net_state": phone_net, "trip_reopened": reopened,
        "filter": {"ceiling_kmh": gps_filter.SPIKE_CEILING_KMH, "default_accuracy_m": gps_filter.DEFAULT_ACCURACY_M,
                   "low_accuracy_m": gps_filter.LOW_ACCURACY_M},
    }
    if accepted and anchor is None:
        payload["first_fix_of_trip"] = {"lat": accepted[0]["lat"], "lng": accepted[0]["lng"],
                                        "recorded_at": accepted[0]["recorded_dt"].isoformat()}
    parts = [f"{norm_count} received", f"{len(accepted)} accepted"]
    if reasons:
        parts.append("rejected " + ", ".join(f"{k}×{v}" for k, v in reasons.items()))
    if dup_in_batch or skipped_stored:
        parts.append(f"{len(dup_in_batch) + skipped_stored} duplicate(s) skipped")
    if stale:
        parts.append(f"{stale} cached repeat(s)")
    if naive_local:
        parts.append(f"{naive_local} local-time stamp(s) corrected")
    rows = [trip_events.make(trip, "ping_batch", "pings: " + ", ".join(parts), payload, occurred_at=server_now)]
    for o in outliers[:MAX_REJECT_EVENTS_PER_BATCH]:
        why = o.get("why") or {}
        detail = ", ".join(f"{k}={v}" for k, v in why.items() if k in ("implied_kmh", "accuracy_m", "ahead_s", "age_before_start_s", "dist_m", "elapsed_s"))
        rows.append(trip_events.make(
            trip, "ping_rejected", f"fix rejected: {o['reject_reason']}" + (f" ({detail})" if detail else ""),
            {"reason": o["reject_reason"], "lat": o["lat"], "lng": o["lng"], "recorded_at": o["recorded_dt"].isoformat(),
             "implied_kmh": o.get("implied_kmh"), "accuracy": o.get("accuracy"), "speed": o.get("speed"), **why},
            occurred_at=o["recorded_dt"]))
    if len(outliers) > MAX_REJECT_EVENTS_PER_BATCH:
        rows.append(trip_events.make(trip, "ping_rejected_more", f"{len(outliers) - MAX_REJECT_EVENTS_PER_BATCH} more rejected fixes in this batch (see location_pings)",
                                     {"count": len(outliers) - MAX_REJECT_EVENTS_PER_BATCH}, occurred_at=server_now))
    for d in dup_in_batch[:10]:
        rows.append(trip_events.make(trip, "ping_duplicate", "fix dropped: exact duplicate (same time and place)",
                                     {"lat": d["lat"], "lng": d["lng"], "recorded_at": d["recorded_dt"].isoformat(), **(d.get("why") or {})},
                                     occurred_at=d["recorded_dt"]))
    trip_events.write(rows)


def _ping_state_cols() -> bool:
    """location_pings.battery / net_state exist (migration 046)?"""
    return lifecycle.has_col("location_pings", "battery")


@router.post("/{trip_id}/heartbeat", status_code=status.HTTP_200_OK)
def post_heartbeat(
    trip_id: str,
    body: Union[HeartbeatIn, List[HeartbeatIn]],
    current_user: dict = Depends(require_role("driver")),
):
    """Tiny periodic "app alive" beacon from the tracking service (batched when
    offline). Stored in trip_heartbeats (046) and folded into trips.last_* so
    the sweeper can tell "GPS weak but app+net alive" from "gone". Answers 200
    with the trip status for ANY non-cancelled trip so a phone that outlived an
    auto-close learns it and resets (and the end reason is revised to network)."""
    trip = _load_own_trip_for_pings(trip_id, current_user)
    beats = body if isinstance(body, list) else [body]
    now = datetime.now(timezone.utc)
    norm = []
    for b in beats:
        dt = b.sent_at if b.sent_at.tzinfo else b.sent_at.replace(tzinfo=timezone.utc)
        if dt > now + FUTURE_SKEW_TOLERANCE:
            dt = now
        norm.append({"sent_at": dt, "battery": b.battery, "net_state": b.net_state})
    norm.sort(key=lambda n: n["sent_at"])
    stored = 0
    prev_sig = lifecycle.load_signals(trip_id) if trip["status"] == "active" else {}
    if trip["status"] == "active":
        stored = lifecycle.store_heartbeats(trip, norm)
        last = norm[-1] if norm else {}
        lifecycle.record_signal(trip_id, at=now, battery=last.get("battery"), net_state=last.get("net_state"), heartbeat=True)
        _log_heartbeats(trip, norm, prev_sig, now, stored)
    else:
        lifecycle.on_signal_after_close(trip, kind="heartbeat", at=now)
        trip_events.emit(trip, "heartbeat_after_end", f"heartbeat received for a {trip['status']} trip — the app is told to reset",
                         {"beats": len(norm), "status": trip["status"]}, occurred_at=now)
    return {"trip_id": trip_id, "status": trip["status"], "stored": stored}


def _log_heartbeats(trip: dict, beats: list, prev_sig: dict, now: datetime, stored: int) -> None:
    """Heartbeats are ~1/min: log the FIRST one, buffered batches (the app was
    offline), and changes of network state / low battery — not every beat."""
    if not beats:
        return
    last = beats[-1]
    first_ever = not prev_sig.get("last_heartbeat_at")
    net_changed = last.get("net_state") and last.get("net_state") != prev_sig.get("last_net_state")
    low = last.get("battery") is not None and last["battery"] <= lifecycle.LOW_BATTERY_PCT and \
        (prev_sig.get("last_battery") is None or prev_sig["last_battery"] > lifecycle.LOW_BATTERY_PCT)
    if not (first_ever or len(beats) > 1 or net_changed or low):
        return
    why = []
    if first_ever:
        why.append("first heartbeat of the trip")
    if len(beats) > 1:
        why.append(f"{len(beats)} buffered beats delivered together (app was offline)")
    if net_changed:
        why.append(f"network {prev_sig.get('last_net_state') or '?'} → {last.get('net_state')}")
    if low:
        why.append(f"battery low ({last['battery']} %)")
    trip_events.emit(trip, "heartbeat", "app alive: " + "; ".join(why),
                     {"beats": len(beats), "stored": stored, "first_sent_at": beats[0]["sent_at"].isoformat(),
                      "last_sent_at": last["sent_at"].isoformat(), "battery": last.get("battery"),
                      "net_state": last.get("net_state"), "lag_s": round((now - last["sent_at"]).total_seconds(), 1)},
                     occurred_at=now)


@router.post("/{trip_id}/app-state", status_code=status.HTTP_200_OK)
def post_app_state(
    trip_id: str,
    body: AppStateIn,
    current_user: dict = Depends(require_role("driver")),
):
    """Best-effort Flutter lifecycle beacon (detached = closed/swiped away,
    paused = backgrounded, resumed). `detached` with no signal after it is the
    "Driver closed the app" evidence for the end-reason inference."""
    trip = _load_own_trip_for_pings(trip_id, current_user)
    now = datetime.now(timezone.utc)
    if trip["status"] == "active":
        lifecycle.record_signal(trip_id, at=now, battery=body.battery, net_state=body.net_state, app_state=body.state)
    else:
        lifecycle.on_signal_after_close(trip, kind="app_state", at=now)
    meaning = {"detached": "app closed / swiped away", "paused": "app in background (or screen locked)",
               "resumed": "app back in foreground"}[body.state]
    at = body.at if body.at is None or body.at.tzinfo else body.at.replace(tzinfo=timezone.utc)
    trip_events.emit(trip, "app_state", f"app state: {body.state} — {meaning}",
                     {"state": body.state, "reported_at": at.isoformat() if at else None, "battery": body.battery,
                      "net_state": body.net_state, "trip_status": trip["status"]}, occurred_at=at or now)
    return {"trip_id": trip_id, "status": trip["status"], "state": body.state}


# ── App-side decision log ────────────────────────────────────────────────────
# The driver app batches its own decisions (fix sampled / dropped by the
# SpikeGate or duplicate gate / queued / sent / server response / permission and
# background state / app version) in its offline buffer and posts them with the
# ping + heartbeat flush. Size-capped; idempotent per client event id.
APP_EVENT_MAX_BATCH = 200
APP_EVENT_TYPE_PATTERN = r"^[a-z][a-z0-9_.]{0,47}$"
APP_EVENT_ACCEPT_AFTER_END = timedelta(hours=24)


class AppEventIn(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)           # client-generated, unique per event
    type: str = Field(..., pattern=APP_EVENT_TYPE_PATTERN)       # e.g. gps.sampled, gate.spike_drop, queue.sent
    at: datetime
    message: Optional[str] = Field(None, max_length=500)
    data: Optional[dict] = None                                  # bounded server-side (trip_events.PAYLOAD_MAX_BYTES)


class AppEventBatchIn(BaseModel):
    app_version: Optional[str] = Field(None, max_length=32)
    build: Optional[str] = Field(None, max_length=32)
    platform: Optional[str] = Field(None, max_length=32)        # android 14 / ios 17 ...
    events: List[AppEventIn] = Field(..., max_length=APP_EVENT_MAX_BATCH)


@router.post("/{trip_id}/events", status_code=status.HTTP_200_OK)
def post_app_events(
    trip_id: str,
    body: AppEventBatchIn,
    current_user: dict = Depends(require_role("driver")),
):
    """Store a batch of the driver app's own trip decisions in the trip decision
    log (source='app'). Own trip only; active, or completed within 24 h (the
    offline buffer drains after the trip ends). Re-sent events are ignored."""
    trip = _load_own_trip_for_pings(trip_id, current_user)
    now = datetime.now(timezone.utc)
    if trip["status"] == "completed" and trip.get("ended_at"):
        if now - gps_filter.parse_dt(trip["ended_at"]) > APP_EVENT_ACCEPT_AFTER_END:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This trip ended too long ago to accept app events.")
    build = {"app_version": body.app_version, "build": body.build, "platform": body.platform}
    rows = []
    for e in body.events:
        at = e.at if e.at.tzinfo else e.at.replace(tzinfo=timezone.utc)
        if at > now + FUTURE_SKEW_TOLERANCE:
            at = now
        rows.append(trip_events.make(trip, e.type, e.message or e.type, {**(e.data or {}), "_app": build},
                                     source="app", occurred_at=at, client_event_id=e.id))
    written = trip_events.write(rows)
    return {"trip_id": trip_id, "received": len(rows), "stored": written, "status": trip["status"],
            "log_available": trip_events.available()}


@router.get("/{trip_id}/progress")
def trip_progress(trip_id: str, current_user: dict = Depends(require_role("driver"))):
    """The driver's own trip: next stop, ETA, progress and the live state — the
    SAME shared progression the server uses for stop visits (stop_progress.py)."""
    trip = _load_own_trip_for_pings(trip_id, current_user)
    full = supabase.table("trips").select("*").eq("id", trip_id).limit(1).execute().data
    t = full[0] if full else trip
    return {"trip_id": trip_id, "status": t["status"], "progress": progress_runtime.view(t),
            "live": live_status.for_trip(t)}


def _last_accepted_before(trip_id: str, ts: datetime) -> Optional[dict]:
    """Newest ACCEPTED stored fix strictly before `ts` (the anchor for judging a
    batch, and the reference for offline-gap detection). None at trip start."""
    try:
        rows = gps_filter.select_pings_tolerant(
            lambda cols: supabase.table("location_pings").select(cols).eq("trip_id", trip_id)
            .lt("recorded_at", ts.isoformat()).order("recorded_at", desc=True).limit(30)
        )
    except Exception:
        return None
    clean = gps_filter.clean_pings(rows)
    return clean[-1] if clean else None


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


def _route_rows(route_ids: list) -> list:
    """routes rows for the driver payloads; the length/duration columns are optional."""
    try:
        return supabase.table("routes").select("id, name, start_time, total_km, est_minutes").in_("id", route_ids).execute().data
    except Exception:
        return supabase.table("routes").select("id, name, start_time").in_("id", route_ids).execute().data


_TRIP_KM_CACHE: dict = {}


def _trip_distance_km(trip_id: str) -> Optional[float]:
    """Driven distance of a COMPLETED trip from its accepted fixes (cached)."""
    if trip_id in _TRIP_KM_CACHE:
        return _TRIP_KM_CACHE[trip_id]
    try:
        rows = (
            supabase.table("location_pings").select("lat, lng, is_outlier")
            .eq("trip_id", trip_id).order("recorded_at", desc=False).limit(6000).execute().data
        )
    except Exception:
        return None
    pts = [(r["lat"], r["lng"]) for r in rows
           if r.get("lat") is not None and r.get("lng") is not None and not r.get("is_outlier")]
    total = 0.0
    for (a, b), (c, d) in zip(pts, pts[1:]):
        seg = _haversine_m(a, b, c, d)
        if seg < 2000:  # a longer hop is a signal gap, not driving
            total += seg
    km = round(total / 1000, 1) if pts else None
    _TRIP_KM_CACHE[trip_id] = km
    return km


@router.get("/my-schedule")
def my_schedule(
    start: Optional[date] = Query(None, description="First day (YYYY-MM-DD, Cairo). Default: today."),
    days: int = Query(7, ge=1, le=31),
    current_user: dict = Depends(require_role("driver")),
):
    """The signed-in DRIVER's (or school supervisor's) own schedule for a range of
    days: each assignment with its status (scheduled / active / completed /
    cancelled / missed), the recent completed trips (with driven distance) and
    this month's totals. Read-only; always scoped to the caller."""
    org_id = current_user["org_id"]
    driver_id = current_user["id"]
    today = datetime.now(LOCAL_TZ).date()
    first = start or today
    last = first + timedelta(days=days - 1)

    rows = (
        supabase.table("assignments")
        .select("id, route_id, vehicle_id, trip_date, shift_label, start_time, end_time")
        .eq("org_id", org_id).eq("driver_id", driver_id)
        .gte("trip_date", first.isoformat()).lte("trip_date", last.isoformat())
        .order("trip_date", desc=False).order("start_time", desc=False)
        .execute().data
    )
    month_start = today.replace(day=1)
    since = datetime.combine(min(first, month_start), time.min, tzinfo=LOCAL_TZ) - timedelta(days=31)
    trip_rows = (
        supabase.table("trips")
        .select("id, assignment_id, route_id, vehicle_id, status, started_at, ended_at, end_reason")
        .eq("org_id", org_id).eq("driver_id", driver_id)
        .gte("started_at", since.isoformat())
        .order("started_at", desc=True).limit(400)
        .execute().data
    )
    route_ids = list({r["route_id"] for r in rows + trip_rows if r.get("route_id")})
    vehicle_ids = list({r["vehicle_id"] for r in rows + trip_rows if r.get("vehicle_id")})
    routes = {x["id"]: x for x in _route_rows(route_ids)} if route_ids else {}
    vehicles = (
        {x["id"]: x for x in supabase.table("vehicles").select("id, bus_number").in_("id", vehicle_ids).execute().data}
        if vehicle_ids else {}
    )
    by_asg: dict = {}
    for t in trip_rows:
        if t.get("assignment_id"):
            by_asg.setdefault(t["assignment_id"], []).append(t)
    rank = {"active": 3, "completed": 2, "cancelled": 1}

    def status_of(a: dict):
        ts = sorted(by_asg.get(a["id"], []), key=lambda t: rank.get(t.get("status"), 0), reverse=True)
        if ts:
            return ts[0].get("status") or "scheduled", ts[0]
        d = date.fromisoformat(str(a["trip_date"])[:10])
        return ("missed" if d < today else "scheduled"), None

    out_days = []
    for i in range(days):
        d = first + timedelta(days=i)
        items = []
        for a in rows:
            if str(a.get("trip_date"))[:10] != d.isoformat():
                continue
            st, trip = status_of(a)
            route = routes.get(a.get("route_id")) or {}
            items.append({
                "assignment_id": a["id"],
                "route_id": a.get("route_id"),
                "route_name": route.get("name"),
                "vehicle_bus_number": (vehicles.get(a.get("vehicle_id")) or {}).get("bus_number"),
                "shift_label": a.get("shift_label"),
                "start_time": a.get("start_time") or route.get("start_time"),
                "end_time": a.get("end_time"),
                "status": st,
                "trip_id": trip.get("id") if trip else None,
            })
        out_days.append({"date": d.isoformat(), "assignments": items})

    def mins(t: dict) -> Optional[int]:
        a, b = gps_filter.parse_dt(t.get("started_at")), gps_filter.parse_dt(t.get("ended_at"))
        return int((b - a).total_seconds() // 60) if a and b and b > a else None

    completed = [t for t in trip_rows if t.get("status") == "completed"]
    recent = []
    for t in completed[:5]:
        route = routes.get(t.get("route_id")) or {}
        recent.append({
            "trip_id": t["id"],
            "route_name": route.get("name"),
            "vehicle_bus_number": (vehicles.get(t.get("vehicle_id")) or {}).get("bus_number"),
            "started_at": t.get("started_at"),
            "ended_at": t.get("ended_at"),
            "end_reason": t.get("end_reason"),
            "duration_min": mins(t),
            "distance_km": _trip_distance_km(t["id"]),
        })

    def local_day(t: dict) -> Optional[date]:
        dt = gps_filter.parse_dt(t.get("started_at"))
        return dt.astimezone(LOCAL_TZ).date() if dt else None

    month_trips = [t for t in completed if (local_day(t) or date.min) >= month_start]
    month = None
    if month_trips:
        on_time = None
        try:
            perf = (
                supabase.table("trip_performance").select("stops_total, stops_on_time")
                .in_("trip_id", [t["id"] for t in month_trips]).execute().data
            )
            tot = sum(int(p.get("stops_total") or 0) for p in perf)
            ok = sum(int(p.get("stops_on_time") or 0) for p in perf)
            on_time = round(100 * ok / tot) if tot else None
        except Exception:
            on_time = None
        month = {
            "trips": len(month_trips),
            "hours": round(sum(mins(t) or 0 for t in month_trips) / 60, 1),
            "on_time_pct": on_time,
        }
    return {"today": today.isoformat(), "module": _org_module(org_id), "days": out_days,
            "recent_trips": recent, "month": month}


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
        .select("id, name, color, geometry, updated_at, total_km, est_minutes")
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
        "total_km": r.get("total_km"),
        "est_minutes": r.get("est_minutes"),
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
    # Undo/correct a mistake: delete this student's record for the trip so they go
    # back to "not marked yet". (boarded is ignored when clear=true.)
    clear: bool = False


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


@router.get("/{trip_id}/students", dependencies=[Depends(feature_flags.require_feature("attendance"))])
def trip_students(trip_id: str, current_user: dict = Depends(require_role("driver"))):
    """Students on the supervisor's active-trip route, with each student's boarded
    status FOR THIS TRIP. SCHOOL orgs only — University drivers get a 403 and see
    no student list. (A student = a passenger on the trip's route.)"""
    trip = _load_own_active_trip(trip_id, current_user)  # own + active
    org_id = trip["org_id"]
    _require_school_org(org_id)

    # TODAY'S EFFECTIVE roster — approved one-day change requests move children
    # between buses, so the supervisor sees exactly who is on THIS bus today, and
    # separately who was moved off it. Same rule the parent/student map applies.
    trip_day = (trip.get("started_at") or "")[:10] or datetime.now(LOCAL_TZ).date().isoformat()
    students, moved_out = effective_roster(org_id, trip["route_id"], trip_day)
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

    # Route-stop order, so the roster is sorted the way the bus reaches the stops.
    stop_order = {(st.get("name") or "").strip().lower(): st.get("stop_order") for st in stops}
    _LAST = 10**9  # students with no/unknown stop sort to the end

    out = []
    for s in students:
        a = att_by_student.get(s["id"])  # None → this student has NOT been marked yet
        # Tri-state so the app can move MARKED students to a "Checked" section and
        # tell "not marked yet" apart from "marked absent":
        #   present  = a row exists and boarded=true
        #   absent   = a row exists and boarded=false (explicitly not on the bus)
        #   None     = no row yet (still to mark)
        attendance_status = None
        if a is not None:
            attendance_status = "present" if a.get("boarded") else "absent"
        stop_name = (a or {}).get("drop_off_stop") or s.get("effective_stop") or s.get("drop_off_stop")
        out.append(
            {
                "student_id": s["id"],
                "name": s.get("name"),
                "class_name": s.get("class_name"),
                "grade": s.get("grade"),
                "student_phone": s.get("student_phone"),
                "parent_phone": s.get("parent_phone"),
                "boarded": bool((a or {}).get("boarded", False)),
                "attendance_status": attendance_status,
                # Afternoon: the recorded drop-off, else today's EFFECTIVE stop
                # (a moved-in child uses the stop the change request asked for).
                "drop_off_stop": stop_name,
                # True when an approved change put this child on your bus today.
                "moved_in": bool(s.get("moved_in")),
            }
        )
    # Sort by the route order of each student's stop, then by name.
    out.sort(key=lambda x: (stop_order.get((x["drop_off_stop"] or "").strip().lower(), _LAST) or _LAST, (x["name"] or "").lower()))
    return {
        "trip_id": trip_id,
        "count": len(out),
        "session": session,
        "route_stops": [st.get("name") for st in stops if st.get("name")],
        "students": out,
        # Children normally on this route who ride ANOTHER bus today. Not part of
        # attendance — shown so the supervisor can flag one who boards anyway.
        "moved_out": [
            {"student_id": m["id"], "name": m.get("name"), "class_name": m.get("class_name"), "grade": m.get("grade")}
            for m in sorted(moved_out, key=lambda m: (m.get("name") or "").lower())
        ],
    }


@router.post("/{trip_id}/attendance", status_code=status.HTTP_200_OK, dependencies=[Depends(feature_flags.require_feature("attendance"))])
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

    # Undo / correct a mistake: remove the record so the student is unmarked again.
    if body.clear:
        try:
            supabase.table("attendance").delete().eq("trip_id", trip_id).eq("student_id", body.student_id).execute()
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not clear attendance: {exc}")
        return {"student_id": body.student_id, "attendance_status": None, "trip_id": trip_id}

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
    return {
        "student_id": body.student_id,
        "boarded": row["boarded"],
        "attendance_status": "present" if row["boarded"] else "absent",
        "session": session,
        "drop_off_stop": row.get("drop_off_stop"),
        "trip_id": trip_id,
    }


class BoardingFlagIn(BaseModel):
    student_id: str = Field(..., min_length=1)
    note: Optional[str] = Field(None, max_length=500)


@router.post("/{trip_id}/boarding-flag", status_code=status.HTTP_200_OK, dependencies=[Depends(feature_flags.require_feature("attendance"))])
def flag_boarding(
    trip_id: str,
    body: BoardingFlagIn,
    current_user: dict = Depends(require_role("driver")),
):
    """The supervisor reports a child who boarded THIS bus even though an approved
    one-day change moved them to another bus today. Raises a MANAGER notification
    (school only). Does NOT record attendance — the child isn't on this roster;
    it's a discrepancy report for the office."""
    trip = _load_own_active_trip(trip_id, current_user)
    org_id = trip["org_id"]
    _require_school_org(org_id)

    trip_day = (trip.get("started_at") or "")[:10] or datetime.now(LOCAL_TZ).date().isoformat()
    # The child must actually have an approved change OFF this route today —
    # otherwise there is nothing to flag (they'd simply be on the roster).
    cr = (
        supabase.table("change_requests")
        .select("id, requested_route_id")
        .eq("org_id", org_id)
        .eq("student_id", body.student_id)
        .eq("request_date", trip_day)
        .eq("status", "approved")
        .eq("current_route_id", trip["route_id"])
        .limit(1)
        .execute()
        .data
    )
    if not cr:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That student has no approved bus change off this route today.",
        )

    _s = supabase.table("passengers").select("name").eq("id", body.student_id).eq("org_id", org_id).limit(1).execute().data
    if not _s:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That student is not in your organization.")
    _r = supabase.table("routes").select("name").eq("id", trip["route_id"]).limit(1).execute().data

    notify.boarding_flag(
        org_id,
        cr[0]["id"],
        _s[0].get("name"),
        current_user.get("name"),
        _r[0].get("name") if _r else None,
        (body.note or "").strip() or None,
    )
    return {"flagged": True, "student_id": body.student_id, "change_request_id": cr[0]["id"]}


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
    departure = None if body.skipped else body.departure_time
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
        "status": "skipped" if body.skipped else "visited",
    }
    try:
        row = (
            supabase.table("stop_visits")
            .upsert(payload, on_conflict="trip_id,stop_id")
            .execute()
            .data[0]
        )
    except Exception as exc:
        # Resilient to deploy order: if migration 036 (stop_visits.status) hasn't
        # run yet, retry WITHOUT the new column so normal visit recording keeps
        # working. Skipped stops just stay unmarked until the migration is applied.
        if "status" in str(exc):
            try:
                row = (
                    supabase.table("stop_visits")
                    .upsert({k: v for k, v in payload.items() if k != "status"}, on_conflict="trip_id,stop_id")
                    .execute()
                    .data[0]
                )
            except Exception as exc2:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not record stop visit: {exc2}")
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not record stop visit: {exc}")
    # A SKIPPED stop was never reached — do NOT fire the arrival notification.
    # (child_arrived is already school-gated; this also keeps a school bus from
    # telling parents "arrived" at a stop it drove past. University never hits it.)
    if not body.skipped:
        # School only, best-effort: notify parents whose child's drop-off stop is
        # THIS stop that "<child>'s bus has arrived" (deduped per trip+student).
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

    base_cols = "id, stop_id, stop_order, arrival_time, departure_time, planned_dwell_seconds, actual_dwell_seconds"
    try:
        visits = (
            supabase.table("stop_visits").select(base_cols + ", status")
            .eq("trip_id", trip_id).eq("org_id", org_id).order("stop_order", desc=False).execute().data
        )
    except Exception:
        # migration 036 (status column) not applied yet — read the legacy shape.
        visits = (
            supabase.table("stop_visits").select(base_cols)
            .eq("trip_id", trip_id).eq("org_id", org_id).order("stop_order", desc=False).execute().data
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


def _org_wide_rules(rules: list, settings: dict, type_: str) -> list:
    """Effective rules of one type. Targeted alert_rules (specific vehicles /
    drivers) always apply. The ORG-WIDE limit comes from Edit Logs once a
    manager has saved it (explicit); otherwise a legacy org-wide alert_rules
    row ('all') if one exists; otherwise the catalog default (if enabled)."""
    s = settings.get(type_) or {}
    def _targeted(r):
        return r.get("target_kind") in ("vehicles", "drivers") and bool(r.get("target_ids"))
    targeted = [r for r in rules if r["type"] == type_ and _targeted(r)]
    legacy_all = [r for r in rules if r["type"] == type_ and not _targeted(r)]
    synthesized = [{"id": f"log-settings:{type_}", "name": "Edit Logs", "type": type_,
                    "threshold": s.get("threshold"), "target_kind": "all", "target_ids": None}]
    if s.get("explicit"):
        org_wide = synthesized if s.get("enabled") else []
    elif legacy_all:
        org_wide = legacy_all
    else:
        org_wide = synthesized if s.get("enabled", True) and s.get("threshold") is not None else []
    return org_wide + targeted


def _alert_row(trip: dict, type_: str, lat, lng, detail: str, occurred_dt) -> dict:
    """One alerts row. The Arabic message rides in `meta` (046) — same bilingual
    mechanism as the trip-lifecycle log points; skipped before that migration."""
    row = {"org_id": trip["org_id"], "trip_id": trip["id"], "driver_id": trip["driver_id"],
           "type": type_, "lat": lat, "lng": lng, "detail": detail, "occurred_at": occurred_dt.isoformat()}
    meta = alert_meta(type_, detail)
    if meta and lifecycle.has_col("alerts", "meta"):
        row["meta"] = meta
    return row


def _insert_alert(trip: dict, type_: str, lat, lng, detail: str, occurred_dt) -> None:
    supabase.table("alerts").insert(_alert_row(trip, type_, lat, lng, detail, occurred_dt)).execute()


def _existing_alert_times(trip_id: str, type_: str, since, until, detail_prefix: str = "") -> set:
    """occurred_at of alerts of `type_` in a range (replay-dedupe). Filters by
    detail prefix in Python rather than `.eq("type", ...)` so a not-yet-migrated
    enum value never errors."""
    rows = (
        supabase.table("alerts").select("type, detail, occurred_at").eq("trip_id", trip_id)
        .gte("occurred_at", since.isoformat()).lte("occurred_at", until.isoformat()).execute().data
    )
    return {
        _parse_dt(a["occurred_at"]) for a in rows
        if a.get("type") == type_ or (detail_prefix and str(a.get("detail") or "").startswith(detail_prefix))
    }


def _process_pings(trip: dict, norm_pings: list, anchor: Optional[dict] = None) -> dict:
    """Detect stop arrivals/departures (geofence) and the logged incidents —
    offline gaps, speeding, off_route, long_stop — for a batch of CLEAN pings
    (gps_filter already dropped duplicates and impossible fixes). Best-effort:
    any error is swallowed (pings are already saved).

    Every incident type reads the org's Edit Logs settings (enabled + threshold)
    via log_settings_logic.effective_log_settings — shared by both modules.

    Geofence de-duplication: a stop is "currently inside / open" iff a stop_events
    row exists for (trip_id, stop_id) with arrived_at set and departed_at NULL.
    """
    summary = {
        "arrivals": 0, "departures": 0, "short_stop_alerts": 0, "speeding_alerts": 0,
        "off_route_alerts": 0, "long_stop_alerts": 0, "offline_alerts": 0,
    }
    try:
        trip_id = trip["id"]
        org_id = trip["org_id"]
        driver_id = trip["driver_id"]
        route_id = trip["route_id"]

        settings = effective_log_settings(org_id)
        stops = []
        geometry = None
        if route_id:
            stops = (
                supabase.table("route_stops").select("id, name, lat, lng, dwell_minutes")
                .eq("route_id", route_id).execute()
            ).data
            try:
                rr = supabase.table("routes").select("geometry").eq("id", route_id).limit(1).execute().data
                geometry = gps_filter.route_line_coords(rr[0].get("geometry")) if rr else None
            except Exception:
                geometry = None
        rules = _load_active_rules(org_id)
        ordered = sorted(norm_pings, key=lambda x: x["recorded_dt"])

        _detect_offline_gap(trip, anchor, ordered, settings, summary)

        # short_stop: Edit Logs on/off, then legacy rule targeting (always-on when
        # no short_stop rule exists).
        shortstop_rules = [r for r in rules if r["type"] == "short_stop"]
        shortstop_applies = bool((settings.get("short_stop") or {}).get("enabled", True)) and (
            (not shortstop_rules) or any(_rule_applies(r, trip) for r in shortstop_rules)
        )

        if not stops:
            # No stops -> no geofence/short_stop. Speeding, off-route (against the
            # route line if there is one), long stops and offline gaps still run.
            _detect_incidents(trip, ordered, rules, settings, summary)
            _detect_off_route(trip, ordered, rules, settings, stops, geometry, summary)
            _detect_long_stop(trip, ordered, stops, settings, summary)
            return summary

        open_rows = (
            supabase.table("stop_events").select("id, stop_id, arrived_at")
            .eq("trip_id", trip_id).is_("departed_at", "null").execute()
        ).data
        open_by_stop = {r["stop_id"]: {"id": r["id"], "arrived_at": _parse_dt(r["arrived_at"])} for r in open_rows}
        last_inside = {sid: ev["arrived_at"] for sid, ev in open_by_stop.items()}
        # `last_inside` lives only for this request. The live app posts ONE fix
        # per request, so without carrying state across requests every departure
        # read departed == arrived_at -> "Stopped 0.0 min" (a false short_stop on
        # every stop). The anchor (newest stored clean fix before this batch)
        # carries it: while a stop's event is open, every stored fix since the
        # arrival was inside, so an anchor inside the geofence is the latest
        # "inside" moment.
        if anchor and anchor.get("recorded_dt") is not None:
            stops_by_id = {s["id"]: s for s in stops}
            for sid, ev in open_by_stop.items():
                s = stops_by_id.get(sid)
                if (s and anchor["recorded_dt"] >= ev["arrived_at"]
                        and _haversine_m(anchor["lat"], anchor["lng"], s["lat"], s["lng"]) <= GEOFENCE_RADIUS_M):
                    last_inside[sid] = anchor["recorded_dt"]

        for n in ordered:
            t = n["recorded_dt"]
            for s in stops:
                sid = s["id"]
                inside = _haversine_m(n["lat"], n["lng"], s["lat"], s["lng"]) <= GEOFENCE_RADIUS_M
                if inside:
                    if sid not in open_by_stop:
                        ev = (
                            supabase.table("stop_events").insert(
                                {"trip_id": trip_id, "org_id": org_id, "stop_id": sid,
                                 "arrived_at": t.isoformat(), "departed_at": None,
                                 "confirmed": True, "was_short": False}
                            ).execute()
                        ).data[0]
                        open_by_stop[sid] = {"id": ev["id"], "arrived_at": t}
                        summary["arrivals"] += 1
                    last_inside[sid] = t
                elif sid in open_by_stop:
                    ev = open_by_stop[sid]
                    departed = last_inside.get(sid, ev["arrived_at"])
                    dwell_sec = (departed - ev["arrived_at"]).total_seconds()
                    required_sec = (s["dwell_minutes"] or 0) * 60
                    was_short = dwell_sec < required_sec
                    supabase.table("stop_events").update(
                        {"departed_at": departed.isoformat(), "was_short": was_short}
                    ).eq("id", ev["id"]).execute()
                    summary["departures"] += 1
                    if was_short and shortstop_applies:
                        detail = f"Stopped {dwell_sec / 60.0:.1f} min at {s['name']}, required {s['dwell_minutes']} min"
                        _insert_alert(trip, "short_stop", s["lat"], s["lng"], detail, departed)
                        summary["short_stop_alerts"] += 1
                    del open_by_stop[sid]
                    last_inside.pop(sid, None)

        _detect_incidents(trip, ordered, rules, settings, summary)
        _detect_off_route(trip, ordered, rules, settings, stops, geometry, summary)
        _detect_long_stop(trip, ordered, stops, settings, summary)
    except Exception as exc:
        summary["error"] = f"detection skipped: {exc}"
    return summary


def _detect_offline_gap(trip: dict, anchor: Optional[dict], ordered: list, settings: dict, summary: dict) -> None:
    """'offline' = no GPS data RECORDED for longer than the threshold during an
    active trip (a buffered-then-flushed stretch has continuous timestamps and is
    NOT offline — the bus was tracked, just uploaded late). Checked between the
    last stored accepted fix and this batch, and inside the batch. One alert per
    gap, at the moment data stopped."""
    s = settings.get("offline") or {}
    if not s.get("enabled", True) or not ordered:
        return
    thr = timedelta(minutes=float(s.get("threshold") or 5))
    seq = ([anchor] if anchor else []) + ordered
    if len(seq) < 2:
        return
    since = seq[0]["recorded_dt"]
    flagged = _existing_alert_times(trip["id"], "offline", since, seq[-1]["recorded_dt"])
    for a, b in zip(seq, seq[1:]):
        gap = b["recorded_dt"] - a["recorded_dt"]
        if gap >= thr and a["recorded_dt"] not in flagged:
            detail = f"No GPS data for {gap.total_seconds() / 60:.0f} min (limit {thr.total_seconds() / 60:.0f} min)"
            _insert_alert(trip, "offline", a["lat"], a["lng"], detail, a["recorded_dt"])
            flagged.add(a["recorded_dt"])
            summary["offline_alerts"] = summary.get("offline_alerts", 0) + 1


def _long_stop_threshold_min(org_id: str, settings: Optional[dict] = None) -> int:
    """Org's long-stop threshold in minutes: Edit Logs (explicit) -> the
    organizations.long_stop_minutes column (040) -> LONG_STOP_DEFAULT_MIN.
    0 = off. Never raises."""
    s = (settings or {}).get("long_stop") or {}
    if s.get("explicit"):
        return int(s.get("threshold") or 0) if s.get("enabled", True) else 0
    if not s.get("enabled", True):
        return 0
    try:
        rows = supabase.table("organizations").select("long_stop_minutes").eq("id", org_id).limit(1).execute().data
        v = rows[0].get("long_stop_minutes") if rows else None
        return LONG_STOP_DEFAULT_MIN if v is None else max(0, int(v))
    except Exception:
        return int(s.get("threshold") or LONG_STOP_DEFAULT_MIN)


def _detect_long_stop(trip: dict, ordered_batch: list, stops: list, settings: dict, summary: dict) -> None:
    """Flag a bus standing still for longer than the org threshold while NOT at
    a scheduled stop. SHARED for both modules; best-effort. Writes to the same
    `alerts` table the Logs page / Alerts page / manager bell read.

    "Standing still" (documented proxy — GPS has no odometer): an ANCHOR fix
    starts a stand-still; every later fix within LONG_STOP_JITTER_M of the
    anchor AND not moving (speed missing or < LONG_STOP_MOVING_MPS) extends it;
    anything else starts a new anchor. Once (fix time - anchor time) >=
    threshold and the anchor is farther than GEOFENCE_RADIUS_M from every route
    stop (or the route has no stops), ONE alert is written with occurred_at =
    the anchor time. The window is rebuilt from STORED clean pings, so buffered
    / out-of-order delivery works; the anchor time is stable while the bus keeps
    standing, so later batches de-duplicate against the existing alert.
    LIMITATION: a traffic jam / red light longer than the threshold, or a stop on
    ANOTHER route, is still flagged — the manager sees the location.
    """
    if not ordered_batch:
        return
    threshold_min = _long_stop_threshold_min(trip["org_id"], settings)
    if threshold_min <= 0:
        return
    threshold = timedelta(minutes=threshold_min)
    trip_id = trip["id"]
    min_ts = ordered_batch[0]["recorded_dt"]
    max_ts = ordered_batch[-1]["recorded_dt"]
    lookback = max(threshold * 2, timedelta(minutes=15))
    rows = gps_filter.select_pings_tolerant(
        lambda cols: supabase.table("location_pings").select(cols).eq("trip_id", trip_id)
        .gte("recorded_at", (min_ts - lookback).isoformat()).lte("recorded_at", max_ts.isoformat())
        .order("recorded_at", desc=False).limit(5000)
    )
    window = gps_filter.clean_pings([r for r in rows if r.get("lat") is not None and r.get("lng") is not None])
    if len(window) < 2:
        return
    flagged = _existing_alert_times(trip_id, "long_stop", min_ts - lookback, max_ts, detail_prefix="Long stop:")
    # A stand-still longer than the lookback: later windows start MID-stand-still
    # (new anchor time), so also de-duplicate by PLACE against the trip's recent
    # long-stop alerts — the bus has not moved, it is the same event.
    recent_places = [
        (a["lat"], a["lng"]) for a in supabase.table("alerts").select("type, detail, lat, lng, occurred_at")
        .eq("trip_id", trip_id).gte("occurred_at", (min_ts - timedelta(hours=3)).isoformat()).execute().data
        if a.get("type") == "long_stop" or str(a.get("detail") or "").startswith("Long stop:")
    ]

    def _already_logged_here(p):
        return any(_haversine_m(p["lat"], p["lng"], la, ln) <= LONG_STOP_JITTER_M for la, ln in recent_places if la is not None)

    anchor = window[0]
    for p in window[1:]:
        moving = p.get("speed") is not None and float(p["speed"]) >= LONG_STOP_MOVING_MPS
        drifted = _haversine_m(anchor["lat"], anchor["lng"], p["lat"], p["lng"]) > LONG_STOP_JITTER_M
        if moving or drifted:
            anchor = p
            continue
        standing = p["recorded_dt"] - anchor["recorded_dt"]
        if standing < threshold or anchor["recorded_dt"] in flagged or _already_logged_here(anchor):
            continue
        at_stop = bool(stops) and _nearest_stop_m(anchor["lat"], anchor["lng"], stops) <= GEOFENCE_RADIUS_M
        if at_stop:
            flagged.add(anchor["recorded_dt"])
            continue
        near_m = _nearest_stop_m(anchor["lat"], anchor["lng"], stops) if stops else None
        where = f"{near_m:.0f} m from the nearest stop" if near_m is not None else "on a route with no stops"
        detail = f"Long stop: stationary for {standing.total_seconds() / 60:.0f} min ({where}; limit {threshold_min} min)"
        _insert_long_stop_alert(trip, anchor, detail)
        flagged.add(anchor["recorded_dt"])
        summary["long_stop_alerts"] = summary.get("long_stop_alerts", 0) + 1


def _insert_long_stop_alert(trip: dict, anchor: dict, detail: str) -> None:
    """Insert the long_stop alert. Resilient pre-migration 039: if the alerts
    `type` enum does not have 'long_stop' yet, store it as 'short_stop' with the
    'Long stop:' detail prefix (logs.py labels it correctly from the prefix)."""
    try:
        _insert_alert(trip, "long_stop", anchor["lat"], anchor["lng"], detail, anchor["recorded_dt"])
    except Exception as exc:
        if "enum" not in str(exc).lower():
            raise
        _insert_alert(trip, "short_stop", anchor["lat"], anchor["lng"], detail, anchor["recorded_dt"])


def _parse_ping_row(r: dict) -> dict:
    """Stored ping row -> internal form with a tz-aware recorded_dt."""
    return {
        "lat": r["lat"], "lng": r["lng"], "speed": r.get("speed"),
        "recorded_dt": _parse_dt(r["recorded_at"]),
    }


def _detect_incidents(trip, ordered_batch, rules, settings, summary) -> None:
    """Bounded-window SPEEDING detection, correct under out-of-order / buffered
    delivery: rebuild a small window of the trip's STORED clean pings around this
    batch's span (true predecessor + span + one follower), emit rising edges
    only, and de-duplicate against alerts already in that range (replay-safe).
    The org-wide limit comes from Edit Logs (see _org_wide_rules)."""
    trip_id = trip["id"]
    speeding_rules = [
        r for r in _org_wide_rules(rules, settings, "speeding")
        if r.get("threshold") is not None and _rule_applies(r, trip)
    ]
    if not speeding_rules or not ordered_batch:
        return

    min_ts = ordered_batch[0]["recorded_dt"]
    max_ts = ordered_batch[-1]["recorded_dt"]

    def _pings(*filters):
        def build(cols):
            q = supabase.table("location_pings").select(cols).eq("trip_id", trip_id)
            for col, op, val in filters:
                q = getattr(q, op)(col, val)
            return q
        return build

    pred_rows = gps_filter.select_pings_tolerant(
        lambda cols: _pings(("recorded_at", "lt", min_ts.isoformat()))(cols).order("recorded_at", desc=True).limit(30)
    )
    pred_clean = gps_filter.clean_pings(pred_rows)
    predecessor = _parse_ping_row(pred_clean[-1]) if pred_clean else None

    span_rows = gps_filter.select_pings_tolerant(
        lambda cols: _pings(("recorded_at", "gte", min_ts.isoformat()), ("recorded_at", "lte", max_ts.isoformat()))(cols)
        .order("recorded_at", desc=False)
    )
    fol_rows = gps_filter.select_pings_tolerant(
        lambda cols: _pings(("recorded_at", "gt", max_ts.isoformat()))(cols).order("recorded_at", desc=False).limit(1)
    )
    window = [_parse_ping_row(r) for r in gps_filter.clean_pings(span_rows + fol_rows)]
    if not window:
        return
    window_max = window[-1]["recorded_dt"]

    existing = (
        supabase.table("alerts").select("type, detail, occurred_at").eq("trip_id", trip_id)
        .gte("occurred_at", min_ts.isoformat()).lte("occurred_at", window_max.isoformat()).execute()
    ).data
    seen = {(a["type"], _parse_dt(a["occurred_at"]), a["detail"]) for a in existing}

    _detect_speeding(trip, window, predecessor, speeding_rules, seen, summary)


def _emit_alert(trip, type_, lat, lng, detail, occurred_dt, seen, summary, counter):
    """Insert an alert unless an identical one already exists (replay-dedupe)."""
    key = (type_, occurred_dt, detail)
    if key in seen:
        return
    supabase.table("alerts").insert(_alert_row(trip, type_, lat, lng, detail, occurred_dt)).execute()
    seen.add(key)
    summary[counter] += 1


def _detect_speeding(trip, window, predecessor, srules, seen, summary) -> None:
    """Rising-edge speeding over the window, one alert per incident per rule.
    `srules` is already filtered to applicable speeding rules with a threshold."""
    # GPS speed is stored in METERS/SECOND (Geolocator), but the rule threshold is
    # in KM/H — convert before comparing, or real driving (≤24 m/s ≈ 86 km/h) would
    # never exceed an 80 "km/h" limit and speeding would never fire.
    MPS_TO_KMH = 3.6

    def _kmh(mps):
        return None if mps is None else mps * MPS_TO_KMH

    for r in srules:
        t = float(r["threshold"])
        pred_kmh = _kmh(predecessor["speed"]) if predecessor else None
        prev_over = bool(pred_kmh is not None and pred_kmh > t)
        for p in window:
            spd_kmh = _kmh(p["speed"])
            cur_over = spd_kmh is not None and spd_kmh > t
            if cur_over and not prev_over:
                detail = (
                    f"Speed {spd_kmh:.0f} km/h exceeded limit {t:.0f} km/h "
                    f"(rule '{r['name']}')"
                )
                _emit_alert(trip, "speeding", p["lat"], p["lng"], detail,
                            p["recorded_dt"], seen, summary, "speeding_alerts")
            prev_over = cur_over


def _detect_off_route(trip, ordered_batch, rules, settings, stops, geometry, summary) -> None:
    """Off-route = farther than the limit from the PLANNED ROUTE LINE
    (routes.geometry, the road-snapped path the manager drew) for longer than the
    configured duration. Falls back to nearest-stop distance only when the route
    has no line (legacy routes) — stops can be kilometres apart, so that proxy is
    far noisier. One alert per off-route episode, at the moment it started
    (occurred_at = first fix beyond the limit), de-duplicated across batches by
    that start time. Runs on STORED clean pings over a lookback window so an
    episode spanning several batches is judged once, with its true start."""
    orules = [
        r for r in _org_wide_rules(rules, settings, "off_route")
        if r.get("threshold") is not None and _rule_applies(r, trip)
    ]
    if not orules or not ordered_batch or (not geometry and not stops):
        return
    trip_id = trip["id"]
    duration_s = int((settings.get("off_route") or {}).get("duration_s") or 0)
    min_ts = ordered_batch[0]["recorded_dt"]
    max_ts = ordered_batch[-1]["recorded_dt"]
    lookback = max(timedelta(seconds=duration_s * 2), timedelta(minutes=5))
    rows = gps_filter.select_pings_tolerant(
        lambda cols: supabase.table("location_pings").select(cols).eq("trip_id", trip_id)
        .gte("recorded_at", (min_ts - lookback).isoformat()).lte("recorded_at", max_ts.isoformat())
        .order("recorded_at", desc=False).limit(5000)
    )
    window = gps_filter.clean_pings(rows)
    if not window:
        return

    def _dist(p):
        d = gps_filter.point_to_polyline_m(p["lat"], p["lng"], geometry) if geometry else None
        return d if d is not None else _nearest_stop_m(p["lat"], p["lng"], stops)

    # An episode longer than the lookback would otherwise look like it STARTED at
    # the window's first fix and be logged again later with a new start time.
    # Extend the window back (up to 60 min) until its first fix is on-route for
    # the tightest limit, so the true start is always inside the window.
    tightest = min(float(r["threshold"]) for r in orules)
    back = min_ts - lookback
    extra = 0
    while window and _dist(window[0]) > tightest and extra < 6:
        extra += 1
        older_from = back - timedelta(minutes=10)
        older = gps_filter.select_pings_tolerant(
            lambda cols: supabase.table("location_pings").select(cols).eq("trip_id", trip_id)
            .gte("recorded_at", older_from.isoformat()).lt("recorded_at", back.isoformat())
            .order("recorded_at", desc=False).limit(2000)
        )
        back = older_from
        if not older:
            break
        window = gps_filter.clean_pings(older + window)

    dists = [(p, _dist(p)) for p in window]
    flagged = _existing_alert_times(trip_id, "off_route", back, max_ts)
    basis = "route line" if geometry else "nearest stop"
    for r in orules:
        limit = float(r["threshold"])
        run_start = None
        run_d0 = 0.0
        for p, d in dists:
            if d > limit:
                if run_start is None:
                    run_start, run_d0 = p, d
                held = (p["recorded_dt"] - run_start["recorded_dt"]).total_seconds()
                if held >= duration_s and run_start["recorded_dt"] not in flagged:
                    detail = (
                        f"Off route by {run_d0:.0f} m from the {basis} for {max(held, 0):.0f} s "
                        f"(limit {limit:.0f} m / {duration_s} s, rule '{r['name']}')"
                    )
                    _insert_alert(trip, "off_route", run_start["lat"], run_start["lng"], detail, run_start["recorded_dt"])
                    flagged.add(run_start["recorded_dt"])
                    summary["off_route_alerts"] += 1
            else:
                run_start = None


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

    rows = gps_filter.select_pings_tolerant(
        lambda cols: supabase.table("location_pings").select(cols)
        .eq("trip_id", trip_id).eq("org_id", org_id)
        .order("recorded_at", desc=True)  # newest first, for the live path
        .limit(limit)
    )
    # Shared plausibility rule at the read boundary: duplicates and impossible
    # (tagged or not) fixes never reach a map.
    clean = gps_filter.clean_pings(rows)
    pings = [{k: p.get(k) for k in ("id", "lat", "lng", "speed", "heading", "recorded_at", "created_at")} for p in reversed(clean)]
    return {"count": len(pings), "trip_id": trip_id, "pings": pings}


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
