"""Live tracking feed for the manager Full View (org-scoped, view_tracking).

We track the DRIVER; the vehicle is metadata on the trip. This returns one entry
per driver currently running an active trip, with their latest position. The
position computation lives in live_logic so the school module reuses it.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from auth import require_permission
from database import supabase
from live_logic import ONLINE_WINDOW, driver_live_positions

router = APIRouter(prefix="/live", tags=["live"])


@router.get("/positions")
def driver_positions(current_user: dict = Depends(require_permission("view_tracking"))):
    """One entry per driver with a recent last-known position (active OR offline).
    Uses the shared live-position computation (live_logic.driver_live_positions)."""
    out = driver_live_positions(current_user["org_id"])
    return {"count": len(out), "drivers": out}


@router.get("/drivers")
def live_drivers(current_user: dict = Depends(require_permission("view_tracking"))):
    org_id = current_user["org_id"]

    # Only ACTIVE trips — the only place a live driver can be.
    active = (
        supabase.table("trips")
        .select("id, driver_id, vehicle_id, route_id")
        .eq("org_id", org_id)
        .eq("status", "active")
        .execute()
    ).data
    if not active:
        return {"count": 0, "drivers": []}

    driver_ids = list({t["driver_id"] for t in active})
    vehicle_ids = list({t["vehicle_id"] for t in active if t.get("vehicle_id")})
    route_ids = list({t["route_id"] for t in active if t.get("route_id")})

    names = {
        p["id"]: p["name"]
        for p in supabase.table("profiles").select("id, name").in_("id", driver_ids).execute().data
    }
    buses = {}
    if vehicle_ids:
        buses = {
            v["id"]: v["bus_number"]
            for v in supabase.table("vehicles").select("id, bus_number").in_("id", vehicle_ids).execute().data
        }
    routes = {}
    if route_ids:
        routes = {
            r["id"]: r["name"]
            for r in supabase.table("routes").select("id, name").in_("id", route_ids).execute().data
        }

    cutoff = datetime.now(timezone.utc) - ONLINE_WINDOW
    out = []
    seen: set = set()
    for t in active:
        did = t["driver_id"]
        if did in seen:
            continue
        seen.add(did)

        lp = (
            supabase.table("location_pings")
            .select("lat, lng, recorded_at")
            .eq("trip_id", t["id"])
            .order("recorded_at", desc=True)
            .limit(1)
            .execute()
        ).data

        position = None
        online = False
        if lp:
            p = lp[0]
            position = {"lat": p["lat"], "lng": p["lng"], "recorded_at": p["recorded_at"]}
            try:
                rec = datetime.fromisoformat(str(p["recorded_at"]).replace("Z", "+00:00"))
                online = rec >= cutoff
            except Exception:
                online = False

        out.append(
            {
                "driver_id": did,
                "name": names.get(did),
                "vehicle_bus_number": buses.get(t.get("vehicle_id")),
                "route_name": routes.get(t.get("route_id")),
                "position": position,
                "online": online,
            }
        )

    return {"count": len(out), "drivers": out}
