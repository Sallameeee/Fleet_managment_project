"""Route management routes — org-scoped, permission-gated.

A route has many ordered stops. POST creates the route and all its stops in
one request, with rollback so we never leave a half-created route. The
route_stops.geog PostGIS point is set by a DB trigger from lat/lng, so we
never send it.
"""

from datetime import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from auth import require_permission
from database import supabase

router = APIRouter(prefix="/routes", tags=["routes"])


class RouteStopCreate(BaseModel):
    name: str = Field(..., min_length=1)
    lat: float
    lng: float
    stop_order: int
    dwell_minutes: int = 0
    arrival_time: Optional[time] = None  # manager-entered arrival at this stop


class RouteCreate(BaseModel):
    name: str = Field(..., min_length=1)
    geometry: Optional[dict] = None  # GeoJSON road path, stored as-is (jsonb)
    total_km: Optional[float] = None
    est_minutes: Optional[int] = None
    start_time: Optional[time] = None  # departure time of the first stop
    color: Optional[str] = None  # hex line color, e.g. "#3AA76D"
    stops: List[RouteStopCreate] = Field(default_factory=list)


# Update reuses the same shape as create: a full replace of name/meta + stops.
class RouteUpdate(RouteCreate):
    pass


def _delete_route(route_id: str) -> None:
    """Best-effort rollback: remove a route and any of its stops."""
    try:
        supabase.table("route_stops").delete().eq("route_id", route_id).execute()
    except Exception:
        pass
    try:
        supabase.table("routes").delete().eq("id", route_id).execute()
    except Exception:
        pass


def _serialize_route(route: dict, stops: list) -> dict:
    """Shared response shape for a route + its ordered stops."""
    ordered = sorted(stops, key=lambda s: s["stop_order"])
    return {
        "id": route["id"],
        "name": route["name"],
        "geometry": route.get("geometry"),
        "total_km": route.get("total_km"),
        "est_minutes": route.get("est_minutes"),
        "start_time": route.get("start_time"),
        "color": route.get("color"),
        "is_active": route.get("is_active"),
        "created_at": route.get("created_at"),
        "stops": [
            {
                "id": s["id"],
                "name": s["name"],
                "stop_order": s["stop_order"],
                "lat": s["lat"],
                "lng": s["lng"],
                "dwell_minutes": s["dwell_minutes"],
                "arrival_time": s.get("arrival_time"),
            }
            for s in ordered
        ],
    }


def _stops_payload(route_id: str, stops: List[RouteStopCreate]) -> list:
    """Build the route_stops insert rows (geog is filled by the DB trigger)."""
    return [
        {
            "route_id": route_id,
            "name": s.name,
            "stop_order": s.stop_order,
            "lat": s.lat,
            "lng": s.lng,
            "dwell_minutes": s.dwell_minutes,
            "arrival_time": s.arrival_time.isoformat() if s.arrival_time else None,
        }
        for s in stops
    ]


def _validate_stops(stops: List[RouteStopCreate]) -> None:
    if not stops:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A route must have at least one stop.",
        )
    orders = [s.stop_order for s in stops]
    if len(orders) != len(set(orders)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Duplicate stop_order values are not allowed; each stop must have a unique order.",
        )


def _get_owned_route(route_id: str, org_id: str) -> dict:
    """Fetch a route scoped to the caller's org, or 404. Enforces tenant isolation."""
    res = (
        supabase.table("routes")
        .select("*")
        .eq("id", route_id)
        .eq("org_id", org_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route not found.")
    return res.data[0]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_route(
    body: RouteCreate,
    current_user: dict = Depends(require_permission("manage_routes")),
):
    # Tenant isolation: org is always the caller's own, from their token.
    org_id = current_user["org_id"]

    # --- Validate stops ---
    _validate_stops(body.stops)

    # --- Step a: insert the route ---
    try:
        route_response = (
            supabase.table("routes")
            .insert(
                {
                    "org_id": org_id,  # caller's org, NOT from the body
                    "name": body.name,
                    "geometry": body.geometry,
                    "total_km": body.total_km,
                    "est_minutes": body.est_minutes,
                    "start_time": body.start_time.isoformat() if body.start_time else None,
                    "color": body.color,
                    "is_active": True,
                }
            )
            .execute()
        )
        route = route_response.data[0]
        route_id = route["id"]
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not create route: {exc}",
        )

    # --- Step b: insert all stops (geog is filled by the DB trigger) ---
    try:
        stops_response = (
            supabase.table("route_stops").insert(_stops_payload(route_id, body.stops)).execute()
        )
    except Exception as exc:
        _delete_route(route_id)  # rollback the route + any inserted stops
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not create route stops (route was rolled back): {exc}",
        )

    return _serialize_route(route, stops_response.data)


@router.get("")
def list_routes(
    current_user: dict = Depends(require_permission("manage_routes")),
):
    # Tenant isolation: only the caller's own org.
    org_id = current_user["org_id"]

    routes_result = (
        supabase.table("routes")
        .select("id, name, total_km, est_minutes, start_time, color, geometry, is_active, created_at")
        .eq("org_id", org_id)
        .order("created_at", desc=True)  # newest first
        .execute()
    )
    routes = routes_result.data
    route_ids = [r["id"] for r in routes]

    # Fetch all stops for these routes in one query, then group by route.
    stops_by_route: dict = {}
    if route_ids:
        stops_result = (
            supabase.table("route_stops")
            .select("id, route_id, name, stop_order, lat, lng, dwell_minutes, arrival_time")
            .in_("route_id", route_ids)
            .order("stop_order", desc=False)
            .execute()
        )
        for s in stops_result.data:
            stops_by_route.setdefault(s["route_id"], []).append(
                {
                    "id": s["id"],
                    "name": s["name"],
                    "stop_order": s["stop_order"],
                    "lat": s["lat"],
                    "lng": s["lng"],
                    "dwell_minutes": s["dwell_minutes"],
                    "arrival_time": s.get("arrival_time"),
                }
            )

    for r in routes:
        r["stops"] = stops_by_route.get(r["id"], [])

    return {"count": len(routes), "routes": routes}


@router.patch("/{route_id}")
def update_route(
    route_id: str,
    body: RouteUpdate,
    current_user: dict = Depends(require_permission("manage_routes")),
):
    """Update a route's name/meta and REPLACE its stops, transactionally.

    Stops are fully replaced (delete-then-insert) rather than diffed: the editor
    can reorder/add/remove freely, so a replace keeps stop_order consistent. We
    back up the old stops first and restore them if the re-insert fails, so a
    route is never left stop-less.
    """
    org_id = current_user["org_id"]
    _get_owned_route(route_id, org_id)  # 404 if not ours (tenant isolation)
    _validate_stops(body.stops)

    # --- Update the route columns (org filter doubly enforces ownership) ---
    try:
        updated = (
            supabase.table("routes")
            .update(
                {
                    "name": body.name,
                    "geometry": body.geometry,
                    "total_km": body.total_km,
                    "est_minutes": body.est_minutes,
                    "start_time": body.start_time.isoformat() if body.start_time else None,
                    "color": body.color,
                }
            )
            .eq("id", route_id)
            .eq("org_id", org_id)
            .execute()
        )
        route = updated.data[0]
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not update route: {exc}",
        )

    # --- Replace stops: back up, delete, re-insert (restore on failure) ---
    backup = (
        supabase.table("route_stops")
        .select("name, stop_order, lat, lng, dwell_minutes, arrival_time")
        .eq("route_id", route_id)
        .execute()
        .data
    )
    supabase.table("route_stops").delete().eq("route_id", route_id).execute()
    try:
        stops_response = (
            supabase.table("route_stops").insert(_stops_payload(route_id, body.stops)).execute()
        )
    except Exception as exc:
        # Roll the stops back to their previous state.
        supabase.table("route_stops").delete().eq("route_id", route_id).execute()
        if backup:
            for row in backup:
                row["route_id"] = route_id
            try:
                supabase.table("route_stops").insert(backup).execute()
            except Exception:
                pass
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not update route stops (stops were restored): {exc}",
        )

    return _serialize_route(route, stops_response.data)


@router.delete("/{route_id}", status_code=status.HTTP_200_OK)
def delete_route(
    route_id: str,
    current_user: dict = Depends(require_permission("manage_routes")),
):
    """Delete a route and its stops. Blocked if any assignment still uses it.

    Historical trips keep their route_id (route_name just shows blank for them);
    we only guard against assignments, which are the forward-looking schedule and
    would break if their route vanished.
    """
    org_id = current_user["org_id"]
    _get_owned_route(route_id, org_id)  # 404 if not ours

    used_by = (
        supabase.table("assignments")
        .select("id", count="exact")
        .eq("route_id", route_id)
        .execute()
    )
    in_use = used_by.count or 0
    if in_use > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This route is used by {in_use} assignment(s). "
                "Reassign or remove those assignments before deleting the route."
            ),
        )

    supabase.table("route_stops").delete().eq("route_id", route_id).execute()
    supabase.table("routes").delete().eq("id", route_id).eq("org_id", org_id).execute()
    return {"deleted": route_id}
