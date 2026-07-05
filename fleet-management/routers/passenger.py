"""Passenger-facing endpoints (role='passenger' only).

STRICT SCOPING (security): a passenger may ONLY see drivers currently running
their OWN route. Every query here is pinned to:
  * the passenger's org_id (from their token → profile), and
  * the passenger's route_id (from the passengers detail row).
There is no way to pass another org/route/driver id in — the passenger supplies
nothing; the server derives everything from the authenticated identity. So a
passenger can never read another route's drivers or any management data.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends

from auth import require_role
from database import supabase

router = APIRouter(prefix="/passenger", tags=["passenger"])

ONLINE_WINDOW = timedelta(minutes=2)


@router.get("/live")
def passenger_live(current_user: dict = Depends(require_role("passenger"))):
    """Live positions of the drivers currently assigned to (running an active
    trip on) THIS passenger's route — and nothing else."""
    org_id = current_user["org_id"]

    pax = (
        supabase.table("passengers").select("route_id").eq("id", current_user["id"]).limit(1).execute().data
    )
    route_id = pax[0]["route_id"] if pax else None
    if not route_id:
        return {"route_id": None, "count": 0, "drivers": []}

    # Active trips on the passenger's route ONLY (org + route pinned).
    active = (
        supabase.table("trips")
        .select("id, driver_id, vehicle_id, route_id")
        .eq("org_id", org_id)
        .eq("route_id", route_id)
        .eq("status", "active")
        .execute()
    ).data
    if not active:
        return {"route_id": route_id, "count": 0, "drivers": []}

    driver_ids = list({t["driver_id"] for t in active})
    vehicle_ids = list({t["vehicle_id"] for t in active if t.get("vehicle_id")})
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

    cutoff = datetime.now(timezone.utc) - ONLINE_WINDOW
    out = []
    seen = set()
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
                "position": position,
                "online": online,
            }
        )
    return {"route_id": route_id, "count": len(out), "drivers": out}
