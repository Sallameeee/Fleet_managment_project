"""Route management routes — org-scoped, permission-gated.

A route has many ordered stops. POST creates the route and all its stops in
one request, with rollback so we never leave a half-created route. The
route_stops.geog PostGIS point is set by a DB trigger from lat/lng, so we
never send it.
"""

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


class RouteCreate(BaseModel):
    name: str = Field(..., min_length=1)
    geometry: Optional[dict] = None  # GeoJSON road path, stored as-is (jsonb)
    total_km: Optional[float] = None
    est_minutes: Optional[int] = None
    stops: List[RouteStopCreate] = Field(default_factory=list)


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


@router.post("", status_code=status.HTTP_201_CREATED)
def create_route(
    body: RouteCreate,
    current_user: dict = Depends(require_permission("manage_routes")),
):
    # Tenant isolation: org is always the caller's own, from their token.
    org_id = current_user["org_id"]

    # --- Validate stops ---
    if not body.stops:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A route must have at least one stop.",
        )
    orders = [s.stop_order for s in body.stops]
    if len(orders) != len(set(orders)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Duplicate stop_order values are not allowed; each stop must have a unique order.",
        )

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
        stops_payload = [
            {
                "route_id": route_id,
                "name": s.name,
                "stop_order": s.stop_order,
                "lat": s.lat,
                "lng": s.lng,
                "dwell_minutes": s.dwell_minutes,
            }
            for s in body.stops
        ]
        stops_response = (
            supabase.table("route_stops").insert(stops_payload).execute()
        )
    except Exception as exc:
        _delete_route(route_id)  # rollback the route + any inserted stops
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not create route stops (route was rolled back): {exc}",
        )

    stops = sorted(stops_response.data, key=lambda s: s["stop_order"])
    return {
        "id": route["id"],
        "name": route["name"],
        "geometry": route["geometry"],
        "total_km": route["total_km"],
        "est_minutes": route["est_minutes"],
        "is_active": route["is_active"],
        "created_at": route["created_at"],
        "stops": [
            {
                "id": s["id"],
                "name": s["name"],
                "stop_order": s["stop_order"],
                "lat": s["lat"],
                "lng": s["lng"],
                "dwell_minutes": s["dwell_minutes"],
            }
            for s in stops
        ],
    }


@router.get("")
def list_routes(
    current_user: dict = Depends(require_permission("manage_routes")),
):
    # Tenant isolation: only the caller's own org.
    org_id = current_user["org_id"]

    routes_result = (
        supabase.table("routes")
        .select("id, name, total_km, est_minutes, is_active, created_at")
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
            .select("id, route_id, name, stop_order, lat, lng, dwell_minutes")
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
                }
            )

    for r in routes:
        r["stops"] = stops_by_route.get(r["id"], [])

    return {"count": len(routes), "routes": routes}
