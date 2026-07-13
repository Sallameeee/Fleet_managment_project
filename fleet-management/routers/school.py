"""Manager/admin school-module extras (backend only).

  * GET /school/buses-live  — every bus's live position (reuses the Full View feed)
  * GET /school/directory   — supervisors + bus drivers with phones, route, bus

School orgs only; University callers get a 403.
"""

from datetime import datetime, time

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from auth import require_permission
from capacity_logic import require_school_org
from database import supabase
from live_logic import LOCAL_TZ, driver_live_positions, pick_current_assignment

router = APIRouter(prefix="/school", tags=["school"])

DEFAULT_CUTOFF = "20:00:00"


def _parse_cutoff(value: str) -> str:
    """Validate an 'HH:MM' or 'HH:MM:SS' 24-hour time; return normalized HH:MM:SS.
    Raises 400 on anything malformed."""
    raw = (value or "").strip()
    parts = raw.split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        s = int(parts[2]) if len(parts) > 2 else 0
        return time(h, m, s).strftime("%H:%M:%S")  # time() range-checks h/m/s
    except (ValueError, IndexError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid time. Use HH:MM (24-hour).")


class CutoffUpdate(BaseModel):
    change_cutoff_time: str = Field(..., min_length=1)


@router.get("/settings")
def get_school_settings(current_user: dict = Depends(require_permission("manage_settings"))):
    """The org's school settings (currently the change-request cutoff time)."""
    org_id = current_user["org_id"]
    require_school_org(org_id)
    org = supabase.table("organizations").select("change_cutoff_time").eq("id", org_id).limit(1).execute().data
    cutoff = (org[0].get("change_cutoff_time") if org else None) or DEFAULT_CUTOFF
    return {"change_cutoff_time": cutoff}


@router.put("/settings")
def update_school_settings(
    body: CutoffUpdate,
    current_user: dict = Depends(require_permission("manage_settings")),
):
    """Update the change-request cutoff time. Read live by the change-request
    creation logic, so editing this immediately changes when parents are cut off."""
    org_id = current_user["org_id"]
    require_school_org(org_id)
    value = _parse_cutoff(body.change_cutoff_time)
    supabase.table("organizations").update({"change_cutoff_time": value}).eq("id", org_id).execute()
    return {"change_cutoff_time": value}


@router.get("/buses-live")
def buses_live(current_user: dict = Depends(require_permission("view_tracking"))):
    """Every bus's current live location in the org — for a map of all buses.
    Reuses the SAME computation as the manager Full View (live_logic), no
    duplicate tracking path."""
    org_id = current_user["org_id"]
    require_school_org(org_id)
    out = driver_live_positions(org_id)
    return {"count": len(out), "buses": out}


@router.get("/directory")
def directory(current_user: dict = Depends(require_permission("manage_drivers"))):
    """All supervisors (the app users) and bus drivers (data-only) with phone
    numbers, plus the route and bus each is on TODAY (from today's assignments)."""
    org_id = current_user["org_id"]
    require_school_org(org_id)

    today = datetime.now(LOCAL_TZ).date().isoformat()
    now_t = datetime.now(LOCAL_TZ).time()

    assigns = (
        supabase.table("assignments")
        .select("driver_id, vehicle_id, bus_driver_id, route_id, start_time, end_time")
        .eq("org_id", org_id)
        .eq("trip_date", today)
        .execute()
        .data
    )
    by_driver: dict = {}
    by_bus_driver: dict = {}
    for a in assigns:
        if a.get("driver_id"):
            by_driver.setdefault(a["driver_id"], []).append(a)
        if a.get("bus_driver_id"):
            by_bus_driver.setdefault(a["bus_driver_id"], []).append(a)

    route_ids = list({a["route_id"] for a in assigns if a.get("route_id")})
    vehicle_ids = list({a["vehicle_id"] for a in assigns if a.get("vehicle_id")})
    routes = {}
    if route_ids:
        routes = {r["id"]: r["name"] for r in supabase.table("routes").select("id, name").in_("id", route_ids).execute().data}
    buses = {}
    if vehicle_ids:
        buses = {v["id"]: v["bus_number"] for v in supabase.table("vehicles").select("id, bus_number").in_("id", vehicle_ids).execute().data}

    def route_bus(assign_list):
        a = pick_current_assignment(assign_list, now_t) if assign_list else None
        if not a:
            return None, None
        return routes.get(a.get("route_id")), buses.get(a.get("vehicle_id"))

    # Supervisors = drivers (app users) in the org.
    sups = supabase.table("profiles").select("id, name, phone").eq("org_id", org_id).eq("role", "driver").execute().data
    supervisors = []
    for s in sups:
        rn, bn = route_bus(by_driver.get(s["id"], []))
        supervisors.append({"driver_id": s["id"], "name": s.get("name"), "phone": s.get("phone"), "route_name": rn, "vehicle_bus_number": bn})
    supervisors.sort(key=lambda x: (x["name"] or "").lower())

    # Bus drivers = data-only entities.
    bds = supabase.table("bus_drivers").select("id, name, phone").eq("org_id", org_id).execute().data
    bus_drivers = []
    for b in bds:
        rn, bn = route_bus(by_bus_driver.get(b["id"], []))
        bus_drivers.append({"id": b["id"], "name": b.get("name"), "phone": b.get("phone"), "route_name": rn, "vehicle_bus_number": bn})
    bus_drivers.sort(key=lambda x: (x["name"] or "").lower())

    return {"supervisors": supervisors, "bus_drivers": bus_drivers}
