"""Logs / events feed (School module).

A human-readable view of the driver events the detection engine ALREADY records
into the `alerts` table (speeding, off_route, short_stop, offline) — produced by
routers/trips.py `_process_pings`/`_detect_incidents`. This router is READ-ONLY and
reuses routers/alerts._enrich_alerts (driver name + route + bus). No new detection.

  * GET /logs/today            — today's events across all drivers (live feed)
  * GET /logs/all              — all events (full logs), newest first
  * GET /logs/driver/{id}      — one driver's events (for the driver detail)

Manager-guarded (view_tracking) + school-only + org-scoped.
"""

from datetime import datetime, time, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query

from alert_text import alert_message_ar
from auth import require_permission
from capacity_logic import LOCAL_TZ, require_school_org
from database import supabase

from .alerts import _enrich_alerts  # reuse existing enrichment (driver/route/bus)

router = APIRouter(prefix="/logs", tags=["logs"])

# Human labels for the detection engine's alert types.
EVENT_LABELS = {
    "speeding": "Exceeded speed limit",
    "off_route": "Took an off-route path",
    "short_stop": "Stop shorter than required",
    "long_stop": "Stopped too long away from a stop",
    "offline": "Went offline",
    # trip-lifecycle log points (trip_lifecycle.py)
    "trip_started": "Trip started",
    "trip_ended": "Trip ended",
    "connection_lost": "Connection lost",
    "connection_restored": "Connection restored",
}
EVENT_LABELS_AR = {
    "speeding": "تجاوز السرعة المحددة",
    "off_route": "خرج عن المسار",
    "short_stop": "توقف أقصر من المطلوب",
    "long_stop": "توقف طويل بعيدًا عن المحطة",
    "offline": "انقطع الإرسال",
    "trip_started": "بدأت الرحلة",
    "trip_ended": "انتهت الرحلة",
    "connection_lost": "انقطع الاتصال",
    "connection_restored": "عاد الاتصال",
}


def _labelled(events: list) -> list:
    for e in events:
        # Pre-migration-039 long stops are stored as short_stop with a "Long stop:"
        # detail prefix (see trips._insert_long_stop_alert) — label them right.
        if e.get("type") == "short_stop" and str(e.get("detail") or "").startswith("Long stop:"):
            e["type"] = "long_stop"
        t = e.get("type")
        e["label"] = EVENT_LABELS.get(t, (t or "event").replace("_", " ").title())
        e["label_ar"] = EVENT_LABELS_AR.get(t, e["label"])
        # Lifecycle events carry ready-made bilingual messages in alerts.meta.
        meta = e.get("meta") if isinstance(e.get("meta"), dict) else {}
        e["detail_ar"] = meta.get("message_ar") or alert_message_ar(t, e.get("detail")) or e.get("detail")
        e["end_reason"] = meta.get("reason") if t == "trip_ended" else None
    return events


def _fetch(org_id: str, *, driver_id: Optional[str] = None, day=None, limit: int = 200) -> list:
    q = supabase.table("alerts").select("*").eq("org_id", org_id)
    if driver_id:
        q = q.eq("driver_id", driver_id)
    if day is not None:
        start = datetime.combine(day, time.min, tzinfo=LOCAL_TZ)
        end = start + timedelta(days=1)
        q = q.gte("occurred_at", start.isoformat()).lt("occurred_at", end.isoformat())
    rows = q.order("occurred_at", desc=True).limit(limit).execute().data  # newest first
    return _labelled(_enrich_alerts(rows))


@router.get("/today")
def logs_today(current_user: dict = Depends(require_permission("view_tracking"))):
    """Today's events (local day) across all drivers, newest first — the live feed."""
    org_id = current_user["org_id"]
    require_school_org(org_id)
    events = _fetch(org_id, day=datetime.now(LOCAL_TZ).date(), limit=300)
    return {"count": len(events), "events": events}


@router.get("/all")
def logs_all(
    current_user: dict = Depends(require_permission("view_tracking")),
    limit: int = Query(500, ge=1, le=1000),
):
    """All events (full logs), newest first, with detail + timestamps."""
    org_id = current_user["org_id"]
    require_school_org(org_id)
    events = _fetch(org_id, limit=limit)
    return {"count": len(events), "events": events}


@router.get("/driver/{driver_id}")
def logs_driver(
    driver_id: str,
    current_user: dict = Depends(require_permission("view_tracking")),
    limit: int = Query(100, ge=1, le=500),
):
    """One driver's/supervisor's notable events (for the driver detail)."""
    org_id = current_user["org_id"]
    require_school_org(org_id)
    events = _fetch(org_id, driver_id=driver_id, limit=limit)
    return {"count": len(events), "events": events, "driver_id": driver_id}
