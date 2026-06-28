"""Manager dashboard summary (org-scoped, any authenticated org user).

Aggregates the landing-page widgets in one call: driver counts, the top driver
this month by actual km, and a live alerts feed.
"""

from collections import defaultdict
from datetime import datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from auth import get_current_user
from database import supabase
from routers.reports import LOCAL_TZ  # Egypt UTC+2, for month boundaries
from routers.trips import _haversine_m  # reuse the geofence haversine

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

ONLINE_WINDOW = timedelta(minutes=2)


@router.get("/summary")
def dashboard_summary(
    current_user: dict = Depends(get_current_user),
    alerts_limit: int = Query(8, ge=1, le=50),
):
    org_id = current_user["org_id"]

    # --- Drivers + counts ----------------------------------------------------
    drivers = (
        supabase.table("profiles")
        .select("id, name")
        .eq("org_id", org_id)
        .eq("role", "driver")
        .execute()
    ).data
    total = len(drivers)
    driver_name = {d["id"]: d["name"] for d in drivers}

    # Active trips right now -> "working_now" drivers.
    active = (
        supabase.table("trips")
        .select("id, driver_id")
        .eq("org_id", org_id)
        .eq("status", "active")
        .execute()
    ).data
    working_ids = {t["driver_id"] for t in active}
    active_trip_ids = [t["id"] for t in active]

    # ONLINE = a ping in the last 2 min. Efficiency: restricted to the (few)
    # active trips' ids + the time window — never scans the whole pings table.
    online_ids: set = set()
    if active_trip_ids:
        cutoff = (datetime.now(timezone.utc) - ONLINE_WINDOW).isoformat()
        recent = (
            supabase.table("location_pings")
            .select("driver_id")
            .in_("trip_id", active_trip_ids)
            .gte("recorded_at", cutoff)
            .execute()
        ).data
        online_ids = {p["driver_id"] for p in recent}
    online = len(online_ids)

    driver_counts = {
        "total": total,
        "online": online,
        "offline": max(total - online, 0),
        "working_now": len(working_ids),
    }

    # --- Top driver this month (actual km) -----------------------------------
    today = datetime.now(LOCAL_TZ).date()
    month_start = today.replace(day=1)
    start_utc = datetime.combine(month_start, time.min, tzinfo=LOCAL_TZ).astimezone(timezone.utc)
    end_utc = datetime.combine(today + timedelta(days=1), time.min, tzinfo=LOCAL_TZ).astimezone(timezone.utc)

    mtrips = (
        supabase.table("trips")
        .select("id, driver_id")
        .eq("org_id", org_id)
        .gte("started_at", start_utc.isoformat())
        .lt("started_at", end_utc.isoformat())
        .execute()
    ).data
    trip_driver = {t["id"]: t["driver_id"] for t in mtrips}
    trips_by_driver: dict = defaultdict(int)
    for t in mtrips:
        trips_by_driver[t["driver_id"]] += 1

    km_by_driver: dict = defaultdict(float)
    trip_ids = list(trip_driver.keys())
    if trip_ids:
        # Bounded to this month's trips (filtered by trip_id), single pass.
        pings = (
            supabase.table("location_pings")
            .select("trip_id, lat, lng, recorded_at")
            .in_("trip_id", trip_ids)
            .order("trip_id", desc=False)
            .order("recorded_at", desc=False)
            .execute()
        ).data
        prev_tid = None
        plat = plng = None
        for p in pings:
            tid = p["trip_id"]
            if tid != prev_tid:
                prev_tid, plat, plng = tid, None, None
            if plat is not None:
                km_by_driver[trip_driver[tid]] += _haversine_m(plat, plng, p["lat"], p["lng"])
            plat, plng = p["lat"], p["lng"]

    top_driver = None
    if km_by_driver:
        did, meters = max(km_by_driver.items(), key=lambda kv: kv[1])
        top_driver = {
            "driver_id": did,
            "name": driver_name.get(did),
            "actual_km": round(meters / 1000.0, 2),
            "trips": trips_by_driver.get(did, 0),
            "score": None,  # scoring is future work
        }

    # --- Live alerts feed ----------------------------------------------------
    alert_rows = (
        supabase.table("alerts")
        .select("id, type, detail, occurred_at, is_read, driver_id")
        .eq("org_id", org_id)
        .order("occurred_at", desc=True)
        .limit(alerts_limit)
        .execute()
    ).data
    alerts = [
        {
            "id": a["id"],
            "type": a["type"],
            "detail": a.get("detail"),
            "occurred_at": a.get("occurred_at"),
            "is_read": a.get("is_read"),
            "driver_name": driver_name.get(a.get("driver_id")),
        }
        for a in alert_rows
    ]

    return {"drivers": driver_counts, "top_driver": top_driver, "alerts": alerts}
