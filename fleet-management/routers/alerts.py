"""Alerts panel — manager view of alerts raised by the detection engine.

Alerts are produced by the ping-processing logic in routers/trips.py
(short_stop, speeding, off_route). This router is read + mark-as-read only;
it does not create alerts.
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from auth import require_permission
from database import supabase

router = APIRouter(prefix="/alerts", tags=["alerts"])


class AlertUpdate(BaseModel):
    is_read: bool


@router.get("")
def list_alerts(
    current_user: dict = Depends(require_permission("manage_trips")),
    type_filter: Optional[str] = Query(
        None, alias="type", description="Filter by type: speeding|off_route|short_stop|offline"
    ),
    is_read: Optional[bool] = Query(None, description="Filter by read/unread."),
    alert_date: Optional[date] = Query(
        None, alias="date", description="Filter by the date of occurred_at (YYYY-MM-DD)."
    ),
):
    org_id = current_user["org_id"]

    query = supabase.table("alerts").select("*").eq("org_id", org_id)
    if type_filter:
        query = query.eq("type", type_filter)
    if is_read is not None:
        query = query.eq("is_read", is_read)
    if alert_date is not None:
        day_start = datetime.combine(alert_date, time.min, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)
        query = query.gte("occurred_at", day_start.isoformat()).lt(
            "occurred_at", day_end.isoformat()
        )

    alerts = query.order("occurred_at", desc=True).execute().data  # newest first

    enriched = _enrich_alerts(alerts)
    return {"count": len(enriched), "alerts": enriched}


@router.patch("/{alert_id}")
def update_alert(
    alert_id: str,
    body: AlertUpdate,
    current_user: dict = Depends(require_permission("manage_trips")),
):
    org_id = current_user["org_id"]
    result = (
        supabase.table("alerts")
        .update({"is_read": body.is_read})
        .eq("id", alert_id)
        .eq("org_id", org_id)  # org-scope: can't touch another org's alert
        .execute()
    )
    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No alert with id '{alert_id}' exists in your organization.",
        )
    return result.data[0]


def _enrich_alerts(alerts: list) -> list:
    """Attach driver_name plus route/vehicle context (via the alert's trip)."""
    if not alerts:
        return []

    driver_ids = {a["driver_id"] for a in alerts if a.get("driver_id")}
    trip_ids = {a["trip_id"] for a in alerts if a.get("trip_id")}

    # driver names
    drivers = {}
    if driver_ids:
        rows = supabase.table("profiles").select("id, name").in_("id", list(driver_ids)).execute().data
        drivers = {r["id"]: r["name"] for r in rows}

    # trip -> route_id / vehicle_id, then names
    trips = {}
    if trip_ids:
        rows = (
            supabase.table("trips")
            .select("id, route_id, vehicle_id")
            .in_("id", list(trip_ids))
            .execute()
        ).data
        trips = {r["id"]: r for r in rows}

    route_ids = {t["route_id"] for t in trips.values() if t.get("route_id")}
    vehicle_ids = {t["vehicle_id"] for t in trips.values() if t.get("vehicle_id")}

    routes = {}
    if route_ids:
        rows = supabase.table("routes").select("id, name").in_("id", list(route_ids)).execute().data
        routes = {r["id"]: r["name"] for r in rows}
    vehicles = {}
    if vehicle_ids:
        rows = supabase.table("vehicles").select("id, bus_number").in_("id", list(vehicle_ids)).execute().data
        vehicles = {r["id"]: r["bus_number"] for r in rows}

    out = []
    for a in alerts:
        trip = trips.get(a.get("trip_id"), {})
        out.append(
            {
                "id": a["id"],
                "type": a["type"],
                "detail": a.get("detail"),
                "lat": a.get("lat"),
                "lng": a.get("lng"),
                "occurred_at": a.get("occurred_at"),
                "is_read": a.get("is_read"),
                "driver_id": a.get("driver_id"),
                "driver_name": drivers.get(a.get("driver_id")),
                "trip_id": a.get("trip_id"),
                "route_name": routes.get(trip.get("route_id")),
                "vehicle_bus_number": vehicles.get(trip.get("vehicle_id")),
            }
        )
    return out
