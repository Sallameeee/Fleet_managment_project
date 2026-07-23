"""Shared capacity logic (School module).

How "students on a bus" is counted:
  * a STUDENT is assigned to a ROUTE      (passengers.route_id)
  * a ROUTE is operated by a BUS          (assignments.vehicle_id)
So the students on a bus = the students on that bus's route, and the bus's
capacity = that vehicle's `capacity`. Null capacity is handled gracefully
(seats_free = None, full = False).

For one-day change requests, per-date OCCUPANCY also folds in APPROVED changes:
  occupancy(route, day) = base_students(route)
                          + approved requests ONTO this route that day
                          - approved requests OFF this route that day
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status

from database import supabase

# Egypt local time. Africa/Cairo observes DST (UTC+2 winter, UTC+3 summer), so a
# FIXED +2 offset displayed summer timestamps an hour early. ZoneInfo handles the
# switch; if the IANA db is somehow unavailable we fall back to the old fixed
# offset so nothing can crash on a missing-tzdata host.
try:  # pragma: no cover - depends on host tz database
    from zoneinfo import ZoneInfo

    LOCAL_TZ = ZoneInfo("Africa/Cairo")
except Exception:  # pragma: no cover
    LOCAL_TZ = timezone(timedelta(hours=2))
DEFAULT_CUTOFF = time(20, 0)  # 8 PM, if the org has none set


def read_cutoff(org_id: str) -> time:
    """The org's change-request cutoff time (organizations.change_cutoff_time),
    default 8 PM. Shared by change-request enforcement and the parent-facing
    options so the app and the server agree on what's selectable."""
    cutoff = DEFAULT_CUTOFF
    try:
        org = supabase.table("organizations").select("change_cutoff_time").eq("id", org_id).limit(1).execute().data
        raw = (org[0].get("change_cutoff_time") if org else None) or ""
        parts = str(raw).split(":")
        if len(parts) >= 2:
            cutoff = time(int(parts[0]), int(parts[1]))
    except Exception:
        pass
    return cutoff


def earliest_request_date(org_id: str) -> date:
    """Earliest date a parent may still request, under the SAME-DAY cutoff rule:
    today is allowed only until the cutoff time; once it passes, the earliest
    selectable day is tomorrow. Every later day is always allowed."""
    cutoff = read_cutoff(org_id)
    now = datetime.now(LOCAL_TZ)
    return now.date() if now.time() < cutoff else now.date() + timedelta(days=1)


def org_module(org_id: str) -> str:
    try:
        r = supabase.table("organizations").select("module").eq("id", org_id).limit(1).execute()
        if r.data and r.data[0].get("module"):
            return r.data[0]["module"]
    except Exception:
        pass
    return "university"


def require_school_org(org_id: str) -> None:
    """Capacity + change requests are school-only. University callers get a 403."""
    if org_module(org_id) != "school":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This feature is only available for school organizations.",
        )


def require_university_org(org_id: str) -> None:
    """The mirror of require_school_org: student-self features are UNIVERSITY-only.
    School callers get a 403 so the two module views never cross over."""
    if org_module(org_id) != "university":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This feature is only available for university organizations.",
        )


def route_vehicle_map(org_id: str, on_date: Optional[date] = None) -> dict:
    """route_id -> {vehicle_id, bus_number, capacity}. The route's bus is taken
    from assignments: the assignment on `on_date` if provided, else the latest one
    for that route."""
    assignments = (
        supabase.table("assignments")
        .select("route_id, vehicle_id, trip_date")
        .eq("org_id", org_id)
        .order("trip_date", desc=True)  # latest first
        .execute()
        .data
    )
    latest: dict = {}
    on_day: dict = {}
    for a in assignments:
        rid, vid = a.get("route_id"), a.get("vehicle_id")
        if not rid or not vid:
            continue
        latest.setdefault(rid, vid)  # first seen per route = latest (desc order)
        if on_date is not None and a.get("trip_date") == on_date.isoformat():
            on_day.setdefault(rid, vid)

    route_vid = {rid: (on_day.get(rid) or latest.get(rid)) for rid in set(latest) | set(on_day)}
    vids = list(set(route_vid.values()))
    veh = {}
    if vids:
        veh = {
            v["id"]: v
            for v in supabase.table("vehicles").select("id, bus_number, capacity").in_("id", vids).execute().data
        }
    out = {}
    for rid, vid in route_vid.items():
        v = veh.get(vid, {})
        out[rid] = {"vehicle_id": vid, "bus_number": v.get("bus_number"), "capacity": v.get("capacity")}
    return out


def base_assigned_map(org_id: str) -> dict:
    """route_id -> number of students (passengers) permanently on that route."""
    rows = supabase.table("passengers").select("route_id").eq("org_id", org_id).execute().data
    counts: dict = {}
    for r in rows:
        rid = r.get("route_id")
        if rid:
            counts[rid] = counts.get(rid, 0) + 1
    return counts


def approved_change_maps(org_id: str) -> tuple:
    """(incoming, outgoing): dict[(route_id, date_iso)] -> count of APPROVED one-day
    changes onto / off that route on that day."""
    try:
        rows = (
            supabase.table("change_requests")
            .select("current_route_id, requested_route_id, request_date, status")
            .eq("org_id", org_id)
            .eq("status", "approved")
            .execute()
            .data
        )
    except Exception:
        rows = []  # table not migrated yet -> no approved changes
    incoming: dict = {}
    outgoing: dict = {}
    for r in rows:
        d = r.get("request_date")
        rin, rout = r.get("requested_route_id"), r.get("current_route_id")
        if rin:
            incoming[(rin, d)] = incoming.get((rin, d), 0) + 1
        if rout:
            outgoing[(rout, d)] = outgoing.get((rout, d), 0) + 1
    return incoming, outgoing


def effective_roster(org_id: str, route_id: str, date_iso: str) -> tuple[list, list]:
    """Who is ACTUALLY on this route on this date, per approved one-day changes.

    Returns (riding, moved_out):
      * riding    — base students on the route, MINUS those an approved change moved
                    off it, PLUS those it moved onto it. Each dict carries
                    `effective_stop` (the change's requested_stop when moved in,
                    else the student's own drop_off_stop) and `moved_in`.
      * moved_out — students normally on this route who were moved to ANOTHER bus
                    today. They are NOT on the roster, but the supervisor is shown
                    them so a child who boards anyway can be flagged.

    This is the SAME rule the parent/student track endpoint applies per-child, kept
    here so the supervisor roster and the parent map can never disagree.
    """
    base = (
        supabase.table("passengers")
        .select("id, name, student_phone, parent_phone, grade, class_name, drop_off_stop")
        .eq("org_id", org_id)
        .eq("route_id", route_id)
        .execute()
        .data
    )
    try:
        crs = (
            supabase.table("change_requests")
            .select("student_id, current_route_id, requested_route_id, requested_stop")
            .eq("org_id", org_id)
            .eq("request_date", date_iso)
            .eq("status", "approved")
            .execute()
            .data
        )
    except Exception:
        crs = []  # not migrated / unavailable -> plain route roster

    riding = {
        s["id"]: {**s, "effective_stop": s.get("drop_off_stop"), "moved_in": False}
        for s in base
    }
    moved_out: list = []
    incoming_ids = [c["student_id"] for c in crs if c.get("requested_route_id") == route_id]
    detail = {}
    if incoming_ids:
        detail = {
            s["id"]: s
            for s in supabase.table("passengers")
            .select("id, name, student_phone, parent_phone, grade, class_name, drop_off_stop")
            .in_("id", incoming_ids)
            .execute()
            .data
        }
    for c in crs:
        sid = c["student_id"]
        # Moved OFF this route today (and not straight back onto it).
        if c.get("current_route_id") == route_id and c.get("requested_route_id") != route_id:
            gone = riding.pop(sid, None)
            if gone:
                moved_out.append({**gone, "moved_to_route_id": c.get("requested_route_id")})
        # Moved ONTO this route today — the change's stop wins.
        if c.get("requested_route_id") == route_id:
            d = detail.get(sid) or {}
            riding[sid] = {
                **d,
                "id": sid,
                "effective_stop": c.get("requested_stop") or d.get("drop_off_stop"),
                "moved_in": True,
            }
    return list(riding.values()), moved_out


def route_supervisor_id(org_id: str, route_id: Optional[str], date_iso: str) -> Optional[str]:
    """The app user (supervisor) assigned to drive this route on this date, if any."""
    if not route_id:
        return None
    try:
        r = (
            supabase.table("assignments")
            .select("driver_id")
            .eq("org_id", org_id)
            .eq("route_id", route_id)
            .eq("trip_date", date_iso)
            .limit(1)
            .execute()
            .data
        )
        return r[0]["driver_id"] if r else None
    except Exception:
        return None


def occupancy(route_id: str, date_iso: str, base: dict, incoming: dict, outgoing: dict) -> int:
    """Students on a route ON A GIVEN DAY, folding in approved one-day changes."""
    return base.get(route_id, 0) + incoming.get((route_id, date_iso), 0) - outgoing.get((route_id, date_iso), 0)


def seats(capacity, assigned: int) -> dict:
    """capacity / assigned / seats_free / full — null capacity handled gracefully."""
    if capacity is None:
        return {"capacity": None, "assigned": assigned, "seats_free": None, "full": False}
    return {
        "capacity": capacity,
        "assigned": assigned,
        "seats_free": capacity - assigned,
        "full": assigned >= capacity,
    }
