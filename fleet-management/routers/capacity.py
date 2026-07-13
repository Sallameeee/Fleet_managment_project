"""Bus/route capacity (FULL / seats free) — School module, manager-facing.

Feeds the (later) dashboard view and the change-request approval logic. School
orgs only; University orgs get a 403 and never see this.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from auth import require_permission
from capacity_logic import base_assigned_map, require_school_org, route_vehicle_map, seats
from database import supabase

router = APIRouter(prefix="/capacity", tags=["capacity"])


@router.get("/routes")
def route_capacity(current_user: dict = Depends(require_permission("manage_passengers"))):
    """Per-route capacity for every route in the org: the operating bus, its
    capacity, students assigned, seats free, and a FULL flag."""
    org_id = current_user["org_id"]
    require_school_org(org_id)

    routes = supabase.table("routes").select("id, name").eq("org_id", org_id).execute().data
    rv = route_vehicle_map(org_id)
    assigned = base_assigned_map(org_id)

    out = []
    for r in routes:
        rid = r["id"]
        v = rv.get(rid, {})
        out.append(
            {
                "route_id": rid,
                "route_name": r.get("name"),
                "vehicle_id": v.get("vehicle_id"),
                "vehicle_bus_number": v.get("bus_number"),
                **seats(v.get("capacity"), assigned.get(rid, 0)),
            }
        )
    out.sort(key=lambda x: (x["route_name"] or "").lower())
    return {"count": len(out), "routes": out}


@router.get("/routes/{route_id}")
def one_route_capacity(route_id: str, current_user: dict = Depends(require_permission("manage_passengers"))):
    """Capacity for a single route (same shape as one row of /capacity/routes)."""
    org_id = current_user["org_id"]
    require_school_org(org_id)

    r = supabase.table("routes").select("id, name").eq("id", route_id).eq("org_id", org_id).limit(1).execute().data
    if not r:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such route in your organization.")
    v = route_vehicle_map(org_id).get(route_id, {})
    assigned = base_assigned_map(org_id).get(route_id, 0)
    return {
        "route_id": route_id,
        "route_name": r[0].get("name"),
        "vehicle_id": v.get("vehicle_id"),
        "vehicle_bus_number": v.get("bus_number"),
        **seats(v.get("capacity"), assigned),
    }
