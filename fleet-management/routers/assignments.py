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
    driver_id: str = Field(..., min_length=1)
    route_id: str = Field(..., min_length=1)
    vehicle_id: str = Field(..., min_length=1)
    trip_date: date
    shift_label: Optional[str] = None
    start_time: Optional[time] = None


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

    # Pydantic gives us date/time objects; serialize to ISO strings for JSON.
    payload = {
        "org_id": org_id,  # caller's org, NOT from the body
        "driver_id": body.driver_id,
        "route_id": body.route_id,
        "vehicle_id": body.vehicle_id,
        "trip_date": body.trip_date.isoformat(),
        "shift_label": body.shift_label,
        "start_time": body.start_time.isoformat() if body.start_time else None,
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
        "driver_id": a["driver_id"],
        "driver_name": driver["name"],
        "route_id": a["route_id"],
        "route_name": route["name"],
        "vehicle_id": a["vehicle_id"],
        "vehicle_bus_number": vehicle["bus_number"],
        "created_at": a["created_at"],
    }


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
        "id, driver_id, route_id, vehicle_id, trip_date, shift_label, start_time, created_at"
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

    drivers = {}
    routes = {}
    vehicles = {}
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
            "driver_id": a["driver_id"],
            "driver_name": drivers.get(a["driver_id"]),
            "route_id": a["route_id"],
            "route_name": routes.get(a["route_id"]),
            "vehicle_id": a["vehicle_id"],
            "vehicle_bus_number": vehicles.get(a["vehicle_id"]),
            "created_at": a["created_at"],
        }
        for a in assignments
    ]

    return {"count": len(enriched), "assignments": enriched}
