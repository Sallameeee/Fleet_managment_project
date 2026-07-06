"""Assignment management routes — the glue linking drivers, routes, and vehicles.

An assignment puts a specific driver behind a specific vehicle on a specific
route for a given trip date (this is operational scheduling, i.e. a *trip* —
hence the `manage_trips` permission, not `manage_routes` which governs the
geographic route definitions themselves).

The security backbone here is tenant isolation: the org is always the caller's
own (from their token, never the body), AND every referenced id (driver, route,
vehicle) is verified to belong to that same org before anything is inserted.
That prevents a manager in org A from assigning org B's driver/route/vehicle.
"""

from datetime import date, time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from auth import require_permission
from database import supabase

router = APIRouter(prefix="/assignments", tags=["assignments"])


class AssignmentCreate(BaseModel):
    driver_id: str = Field(..., min_length=1)  # the app user (a "supervisor" in school orgs)
    route_id: str = Field(..., min_length=1)
    vehicle_id: str = Field(..., min_length=1)
    trip_date: date
    shift_label: Optional[str] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    # School module: link to a bus_drivers row (the physical bus driver; optional).
    # University orgs simply never send it.
    bus_driver_id: Optional[str] = None


# Update = full replace of the same fields as create.
class AssignmentUpdate(AssignmentCreate):
    pass


def _parse_hm(value: Optional[str]) -> Optional[time]:
    """Parse a stored 'HH:MM[:SS]' time string into a time, or None."""
    if not value:
        return None
    try:
        parts = value.split(":")
        return time(int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        return None


def _name_of(table: str, record_id: str, column: str) -> Optional[str]:
    """Fetch a single display field (e.g. a driver name) for a message."""
    try:
        res = supabase.table(table).select(column).eq("id", record_id).limit(1).execute()
        return res.data[0][column] if res.data else None
    except Exception:
        return None


def _assert_no_conflict(
    org_id: str,
    driver_id: str,
    vehicle_id: str,
    trip_date_iso: str,
    start: Optional[time],
    end: Optional[time],
    exclude_id: Optional[str] = None,
) -> None:
    """Reject a double-booking of the SAME driver or vehicle for an overlapping
    time window on the same date within the org — enforced server-side.

    Overlap test (standard half-open interval intersection):
        startA < endB  AND  startB < endA
    which is true iff the two [start, end) windows intersect. Same trip_date,
    same org, excluding the assignment being edited. Rows without a full window
    can't be compared, so they're skipped.
    """
    if start is None or end is None:
        return  # no window to compare — nothing to enforce
    if end <= start:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="End time must be after start time.",
        )

    rows = (
        supabase.table("assignments")
        .select("id, driver_id, vehicle_id, route_id, start_time, end_time")
        .eq("org_id", org_id)
        .eq("trip_date", trip_date_iso)
        .execute()
        .data
    )
    for r in rows:
        if exclude_id and r["id"] == exclude_id:
            continue
        rs = _parse_hm(r.get("start_time"))
        re = _parse_hm(r.get("end_time"))
        if rs is None or re is None:
            continue  # existing row has no window — can't prove an overlap
        if not (start < re and rs < end):
            continue  # windows don't intersect

        same_driver = r["driver_id"] == driver_id
        same_vehicle = r["vehicle_id"] == vehicle_id
        if not (same_driver or same_vehicle):
            continue

        win_start = rs.strftime("%H:%M")
        win_end = re.strftime("%H:%M")
        # Driver clash takes priority in the message (people-first).
        if same_driver:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "conflict",
                    "resource": "driver",
                    "name": _name_of("profiles", driver_id, "name"),
                    "route_name": _name_of("routes", r["route_id"], "name"),
                    "start": win_start,
                    "end": win_end,
                },
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "conflict",
                "resource": "vehicle",
                "name": _name_of("vehicles", vehicle_id, "bus_number"),
                "route_name": _name_of("routes", r["route_id"], "name"),
                "start": win_start,
                "end": win_end,
            },
        )


def _verify_belongs_to_org(
    table: str,
    record_id: str,
    org_id: str,
    label: str,
    *,
    extra_eq: Optional[dict] = None,
) -> dict:
    """Fetch one row by id and confirm it belongs to the caller's org.

    Returns the row (so the caller can reuse its fields, e.g. the name).
    Raises 404 if the id doesn't exist, belongs to another org, or — when
    `extra_eq` is given (e.g. role='driver') — doesn't match that filter.

    We deliberately return the SAME 404 for "not found" and "wrong org": a
    caller must not be able to probe which ids exist in other organizations.
    """
    query = supabase.table(table).select("*").eq("id", record_id).eq("org_id", org_id)
    for column, value in (extra_eq or {}).items():
        query = query.eq(column, value)

    try:
        result = query.limit(1).execute()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not validate {label}: {exc}",
        )

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {label} with id '{record_id}' exists in your organization.",
        )
    return result.data[0]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_assignment(
    body: AssignmentCreate,
    current_user: dict = Depends(require_permission("manage_trips")),
):
    # Tenant isolation: the org is ALWAYS the caller's own, from their token —
    # never anything supplied in the request body.
    org_id = current_user["org_id"]

    # --- CRITICAL: verify all three referenced ids belong to the caller's org
    # BEFORE inserting. Each helper raises a clear 404 if the id is missing or
    # owned by another org, so we can never assign across tenants. ---
    driver = _verify_belongs_to_org(
        "profiles", body.driver_id, org_id, "driver",
        extra_eq={"role": "driver"},
    )
    route = _verify_belongs_to_org("routes", body.route_id, org_id, "route")
    vehicle = _verify_belongs_to_org("vehicles", body.vehicle_id, org_id, "vehicle")
    # School module: optional bus driver (verified to be in the caller's org).
    bus_driver = (
        _verify_belongs_to_org("bus_drivers", body.bus_driver_id, org_id, "bus driver")
        if body.bus_driver_id else None
    )

    # --- CRITICAL: reject double-booking the driver or vehicle for an
    # overlapping window on this date (409 with a helpful message). ---
    _assert_no_conflict(
        org_id, body.driver_id, body.vehicle_id, body.trip_date.isoformat(),
        body.start_time, body.end_time,
    )

    # Pydantic gives us date/time objects; serialize to ISO strings for JSON.
    payload = {
        "org_id": org_id,  # caller's org, NOT from the body
        "driver_id": body.driver_id,
        "route_id": body.route_id,
        "vehicle_id": body.vehicle_id,
        "trip_date": body.trip_date.isoformat(),
        "shift_label": body.shift_label,
        "start_time": body.start_time.isoformat() if body.start_time else None,
        "end_time": body.end_time.isoformat() if body.end_time else None,
        "bus_driver_id": body.bus_driver_id,
    }

    try:
        result = supabase.table("assignments").insert(payload).execute()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not create assignment: {exc}",
        )

    a = result.data[0]
    # Return the created row, already enriched with readable names so the
    # caller doesn't need a follow-up lookup.
    return {
        "id": a["id"],
        "trip_date": a["trip_date"],
        "shift_label": a["shift_label"],
        "start_time": a["start_time"],
        "end_time": a.get("end_time"),
        "driver_id": a["driver_id"],
        "driver_name": driver["name"],
        "route_id": a["route_id"],
        "route_name": route["name"],
        "vehicle_id": a["vehicle_id"],
        "vehicle_bus_number": vehicle["bus_number"],
        "bus_driver_id": a.get("bus_driver_id"),
        "bus_driver_name": bus_driver["name"] if bus_driver else None,
        "bus_driver_phone": bus_driver.get("phone") if bus_driver else None,
        "created_at": a["created_at"],
    }


@router.patch("/{assignment_id}")
def update_assignment(
    assignment_id: str,
    body: AssignmentUpdate,
    current_user: dict = Depends(require_permission("manage_trips")),
):
    org_id = current_user["org_id"]

    # Ownership: the assignment must belong to the caller's org (404 otherwise).
    _verify_belongs_to_org("assignments", assignment_id, org_id, "assignment")

    # Re-verify the three referenced ids belong to this org.
    driver = _verify_belongs_to_org(
        "profiles", body.driver_id, org_id, "driver", extra_eq={"role": "driver"}
    )
    route = _verify_belongs_to_org("routes", body.route_id, org_id, "route")
    vehicle = _verify_belongs_to_org("vehicles", body.vehicle_id, org_id, "vehicle")
    bus_driver = (
        _verify_belongs_to_org("bus_drivers", body.bus_driver_id, org_id, "bus driver")
        if body.bus_driver_id else None
    )

    # Conflict check EXCLUDING this assignment (so keeping its own window is ok).
    _assert_no_conflict(
        org_id, body.driver_id, body.vehicle_id, body.trip_date.isoformat(),
        body.start_time, body.end_time, exclude_id=assignment_id,
    )

    payload = {
        "driver_id": body.driver_id,
        "route_id": body.route_id,
        "vehicle_id": body.vehicle_id,
        "trip_date": body.trip_date.isoformat(),
        "shift_label": body.shift_label,
        "start_time": body.start_time.isoformat() if body.start_time else None,
        "end_time": body.end_time.isoformat() if body.end_time else None,
        "bus_driver_id": body.bus_driver_id,
    }
    try:
        result = (
            supabase.table("assignments")
            .update(payload)
            .eq("id", assignment_id)
            .eq("org_id", org_id)  # doubly enforce ownership
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not update assignment: {exc}",
        )

    a = result.data[0]
    return {
        "id": a["id"],
        "trip_date": a["trip_date"],
        "shift_label": a["shift_label"],
        "start_time": a["start_time"],
        "end_time": a.get("end_time"),
        "driver_id": a["driver_id"],
        "driver_name": driver["name"],
        "route_id": a["route_id"],
        "route_name": route["name"],
        "vehicle_id": a["vehicle_id"],
        "vehicle_bus_number": vehicle["bus_number"],
        "bus_driver_id": a.get("bus_driver_id"),
        "bus_driver_name": bus_driver["name"] if bus_driver else None,
        "bus_driver_phone": bus_driver.get("phone") if bus_driver else None,
        "created_at": a["created_at"],
    }


@router.delete("/{assignment_id}", status_code=status.HTTP_200_OK)
def delete_assignment(
    assignment_id: str,
    current_user: dict = Depends(require_permission("manage_trips")),
):
    org_id = current_user["org_id"]
    _verify_belongs_to_org("assignments", assignment_id, org_id, "assignment")
    supabase.table("assignments").delete().eq("id", assignment_id).eq("org_id", org_id).execute()
    return {"deleted": assignment_id}


@router.get("")
def list_assignments(
    current_user: dict = Depends(require_permission("manage_trips")),
    trip_date: Optional[date] = Query(
        None,
        alias="date",  # exposed to callers as ?date=YYYY-MM-DD
        description="Filter to a single trip date (YYYY-MM-DD), e.g. today's schedule.",
    ),
):
    # Tenant isolation: only the caller's own org.
    org_id = current_user["org_id"]

    query = supabase.table("assignments").select(
        "id, driver_id, route_id, vehicle_id, trip_date, shift_label, start_time, end_time, "
        "bus_driver_id, created_at"
    ).eq("org_id", org_id)

    if trip_date is not None:
        # Exact-day schedule.
        query = query.eq("trip_date", trip_date.isoformat())

    # Order by date then start time so a day's schedule reads top-to-bottom.
    result = query.order("trip_date", desc=False).order("start_time", desc=False).execute()
    assignments = result.data

    # --- Enrich each assignment with readable names, not just raw ids. Batch
    # the lookups (one query per related table) and join in memory. ---
    driver_ids = {a["driver_id"] for a in assignments}
    route_ids = {a["route_id"] for a in assignments}
    vehicle_ids = {a["vehicle_id"] for a in assignments}
    bus_driver_ids = {a["bus_driver_id"] for a in assignments if a.get("bus_driver_id")}

    drivers = {}
    routes = {}
    vehicles = {}
    bus_drivers = {}  # id -> {name, phone}
    if bus_driver_ids:
        rows = (
            supabase.table("bus_drivers")
            .select("id, name, phone")
            .in_("id", list(bus_driver_ids))
            .execute()
        )
        bus_drivers = {r["id"]: r for r in rows.data}
    if driver_ids:
        rows = (
            supabase.table("profiles")
            .select("id, name")
            .in_("id", list(driver_ids))
            .execute()
        )
        drivers = {r["id"]: r["name"] for r in rows.data}
    if route_ids:
        rows = (
            supabase.table("routes")
            .select("id, name")
            .in_("id", list(route_ids))
            .execute()
        )
        routes = {r["id"]: r["name"] for r in rows.data}
    if vehicle_ids:
        rows = (
            supabase.table("vehicles")
            .select("id, bus_number")
            .in_("id", list(vehicle_ids))
            .execute()
        )
        vehicles = {r["id"]: r["bus_number"] for r in rows.data}

    enriched = [
        {
            "id": a["id"],
            "trip_date": a["trip_date"],
            "shift_label": a["shift_label"],
            "start_time": a["start_time"],
            "end_time": a.get("end_time"),
            "driver_id": a["driver_id"],
            "driver_name": drivers.get(a["driver_id"]),
            "route_id": a["route_id"],
            "route_name": routes.get(a["route_id"]),
            "vehicle_id": a["vehicle_id"],
            "vehicle_bus_number": vehicles.get(a["vehicle_id"]),
            "bus_driver_id": a.get("bus_driver_id"),
            "bus_driver_name": (bus_drivers.get(a.get("bus_driver_id")) or {}).get("name"),
            "bus_driver_phone": (bus_drivers.get(a.get("bus_driver_id")) or {}).get("phone"),
            "created_at": a["created_at"],
        }
        for a in assignments
    ]

    return {"count": len(enriched), "assignments": enriched}
