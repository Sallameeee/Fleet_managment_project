"""Manager/admin school-module extras (backend only).

  * GET /school/buses-live  — every bus's live position (reuses the Full View feed)
  * GET /school/directory   — supervisors + bus drivers with phones, route, bus

School orgs only; University callers get a 403.
"""

from datetime import date, datetime, time, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from auth import require_permission
from capacity_logic import require_school_org
from database import supabase
from features import require_feature
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


@router.get("/buses-live", dependencies=[Depends(require_feature("buses_map"))])
def buses_live(current_user: dict = Depends(require_permission("view_tracking"))):
    """Every bus's current live location in the org — for a map of all buses.
    Reuses the SAME computation as the manager Full View (live_logic), no
    duplicate tracking path."""
    org_id = current_user["org_id"]
    require_school_org(org_id)
    out = driver_live_positions(org_id)
    return {"count": len(out), "buses": out}


@router.get("/directory", dependencies=[Depends(require_feature("directory"))])
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


@router.get("/performance", dependencies=[Depends(require_feature("performance"))])
def performance(
    current_user: dict = Depends(require_permission("view_tracking")),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
):
    """Per-trip + per-supervisor performance over a date range (from the persisted
    trip_performance rows): speeding, off-route, and schedule adherence. School
    only. Rows populate as trips are ended."""
    org_id = current_user["org_id"]
    require_school_org(org_id)

    today = datetime.now(LOCAL_TZ).date()
    d_from = date_from or (today - timedelta(days=14))
    d_to = date_to or today
    rows = (
        supabase.table("trip_performance")
        .select("*")
        .eq("org_id", org_id)
        .gte("trip_date", d_from.isoformat())
        .lte("trip_date", d_to.isoformat())
        .order("trip_date", desc=True)
        .execute()
        .data
    )
    driver_ids = list({r["driver_id"] for r in rows if r.get("driver_id")})
    route_ids = list({r["route_id"] for r in rows if r.get("route_id")})
    names, rnames = {}, {}
    if driver_ids:
        names = {p["id"]: p["name"] for p in supabase.table("profiles").select("id, name").in_("id", driver_ids).execute().data}
    if route_ids:
        rnames = {r["id"]: r["name"] for r in supabase.table("routes").select("id, name").in_("id", route_ids).execute().data}

    trips = []
    agg: dict = {}
    for r in rows:
        did = r.get("driver_id")
        trips.append(
            {
                "trip_id": r["trip_id"],
                "trip_date": r.get("trip_date"),
                "driver_id": did,
                "driver_name": names.get(did),
                "route_name": rnames.get(r.get("route_id")),
                "speeding_count": r.get("speeding_count") or 0,
                "off_route_count": r.get("off_route_count") or 0,
                "stops_total": r.get("stops_total") or 0,
                "stops_on_time": r.get("stops_on_time") or 0,
                "stops_late": r.get("stops_late") or 0,
                "avg_delay_min": r.get("avg_delay_min"),
                "max_delay_min": r.get("max_delay_min"),
            }
        )
        a = agg.setdefault(did, {"driver_id": did, "name": names.get(did), "trips": 0, "speeding": 0, "off_route": 0, "stops_total": 0, "stops_on_time": 0, "stops_late": 0})
        a["trips"] += 1
        a["speeding"] += r.get("speeding_count") or 0
        a["off_route"] += r.get("off_route_count") or 0
        a["stops_total"] += r.get("stops_total") or 0
        a["stops_on_time"] += r.get("stops_on_time") or 0
        a["stops_late"] += r.get("stops_late") or 0

    supervisors = []
    for a in agg.values():
        tot = a["stops_total"]
        a["on_time_pct"] = round(100 * a["stops_on_time"] / tot) if tot else None
        supervisors.append(a)
    supervisors.sort(key=lambda x: (x["name"] or "").lower())

    return {"from": d_from.isoformat(), "to": d_to.isoformat(), "trips": trips, "supervisors": supervisors}
