"""Notification generation (School module).

One low-level `create_notification` (school-gated, dedup-aware, NEVER raises — a
notification failure must never break the action that triggered it) plus small
event helpers used by the routers.

Recipient model:
  * parent notes  → audience='parent', recipient_id = the parent's profile id.
  * manager notes → audience='manager', recipient_id = None (org-wide).

Trip events (started / arrived) use the EFFECTIVE set of students on the bus for
today — base students on the route, minus those an approved one-day change moved
OFF it, plus those it moved ONTO it — so a child riding a different bus that day
is notified on the RIGHT bus.
"""

import logging
from datetime import datetime

from capacity_logic import LOCAL_TZ, org_module
from database import supabase

log = logging.getLogger("notifications")


def _is_school(org_id: str) -> bool:
    return org_module(org_id) == "school"


def create_notification(
    org_id: str,
    audience: str,
    recipient_id: str | None,
    ntype: str,
    title: str,
    body: str | None = None,
    related_id: str | None = None,
    dedup_key: str | None = None,
    result: str | None = None,
) -> None:
    """Insert one notification. School-only; a duplicate dedup_key (unique index)
    or any other error is swallowed so the caller is never affected. `result`
    ('approved'|'rejected') lets the app color request-result notifications."""
    try:
        if not _is_school(org_id):
            return
        supabase.table("notifications").insert(
            {
                "org_id": org_id,
                "audience": audience,
                "recipient_id": recipient_id,
                "type": ntype,
                "title": title,
                "body": body,
                "related_id": str(related_id) if related_id is not None else None,
                "dedup_key": dedup_key,
                "result": result,
            }
        ).execute()
    except Exception as exc:  # duplicate dedup_key or transient error → ignore
        log.info("notification skipped/failed (%s): %s", dedup_key, exc)


# ── Change requests (bus change) ─────────────────────────────────────────────
def change_request_created(org_id: str, req_id: str, student_name: str | None) -> None:
    create_notification(
        org_id, "manager", None, "change_request_new",
        "New bus-change request",
        f"{student_name or 'A student'} — a parent requested a one-day bus change.",
        related_id=req_id, dedup_key=f"cr_new:{req_id}",
    )


def change_request_decided(
    org_id: str, parent_id: str, req_id: str, approved: bool, student_name: str | None, route_name: str | None
) -> None:
    who = student_name or "your child"
    where = f" to {route_name}" if (approved and route_name) else ""
    create_notification(
        org_id, "parent", parent_id, "change_request_result",
        "Bus change approved" if approved else "Bus change rejected",
        f"{who}'s bus change{where} was {'approved' if approved else 'rejected'}.",
        related_id=req_id, dedup_key=f"cr_res:{req_id}",
        result="approved" if approved else "rejected",
    )


# ── Profile-edit requests ────────────────────────────────────────────────────
def profile_request_created(org_id: str, req_id: str, parent_name: str | None) -> None:
    create_notification(
        org_id, "manager", None, "profile_request_new",
        "New profile-edit request",
        f"{parent_name or 'A parent'} requested a change to their personal info.",
        related_id=req_id, dedup_key=f"pr_new:{req_id}",
    )


def change_request_supervisors(
    org_id: str,
    req_id: str,
    request_date: str,
    student_name: str | None,
    current_route_id: str | None,
    requested_route_id: str | None,
    requested_stop: str | None,
) -> None:
    """On APPROVAL, tell the two supervisors whose bus roster just changed for that
    day: the LOSING one that the child is not with them, the GAINING one that the
    child joins them (with the drop-off stop). Silent when a route has no
    supervisor assigned that day, or when both routes share one supervisor for the
    losing note. Never raises — create_notification swallows failures."""
    from capacity_logic import route_supervisor_id  # local: avoid an import cycle

    who = student_name or "A student"
    losing = route_supervisor_id(org_id, current_route_id, request_date)
    gaining = route_supervisor_id(org_id, requested_route_id, request_date)

    if losing and losing != gaining:
        create_notification(
            org_id, "supervisor", losing, "change_request_roster_out",
            "Student not with you today",
            f"{who} is not on your bus on {request_date} — an approved bus change moved them to another bus.",
            related_id=req_id, dedup_key=f"cr_sup_out:{req_id}",
        )
    if gaining:
        where = f" Drop-off: {requested_stop}." if requested_stop else ""
        create_notification(
            org_id, "supervisor", gaining, "change_request_roster_in",
            "Student joins your bus today",
            f"{who} rides your bus on {request_date} (approved bus change).{where}",
            related_id=req_id, dedup_key=f"cr_sup_in:{req_id}",
        )


def boarding_flag(
    org_id: str,
    req_id: str | None,
    student_name: str | None,
    supervisor_name: str | None,
    route_name: str | None,
    note: str | None = None,
) -> None:
    """A supervisor reports that a child boarded THEIR bus even though an approved
    change moved that child elsewhere today. Raises a MANAGER notification so it
    shows in the dashboard bell and opens the related change request."""
    who = student_name or "A student"
    extra = f" Note: {note}" if note else ""
    create_notification(
        org_id, "manager", None, "boarding_flag",
        "Student boarded the wrong bus",
        f"{who} boarded {route_name or 'a bus'} despite an approved bus change"
        f"{f' (reported by {supervisor_name})' if supervisor_name else ''}.{extra}",
        related_id=req_id,
        dedup_key=f"board_flag:{req_id}" if req_id else None,
    )


def profile_request_decided(org_id: str, parent_id: str, req_id: str, approved: bool) -> None:
    create_notification(
        org_id, "parent", parent_id, "profile_request_result",
        "Profile update approved" if approved else "Profile update rejected",
        "Your personal info change was approved and applied."
        if approved
        else "Your personal info change was rejected. Nothing was changed.",
        related_id=req_id, dedup_key=f"pr_res:{req_id}",
        result="approved" if approved else "rejected",
    )


# ── Parent-reported issues ───────────────────────────────────────────────────
def parent_report_created(org_id: str, report_id: str, parent_name: str | None, subject: str) -> None:
    create_notification(
        org_id, "manager", None, "parent_report_new",
        "New parent report",
        f"{parent_name or 'A parent'} reported: {subject}",
        related_id=report_id, dedup_key=f"report_new:{report_id}",
    )


# ── Trip events (bus started / child arrived) ────────────────────────────────
def _effective_students_on_route(org_id: str, route_id: str, date_iso: str) -> list:
    """The students actually on THIS route today: base students, minus those an
    approved one-day change moved off it, plus those it moved onto it (whose
    drop-off is the requested stop). Returns dicts: id, name, parent_id, drop_off_stop."""
    base = (
        supabase.table("passengers")
        .select("id, name, parent_id, drop_off_stop")
        .eq("org_id", org_id)
        .eq("route_id", route_id)
        .execute()
        .data
    )
    students = {
        s["id"]: {"id": s["id"], "name": s.get("name"), "parent_id": s.get("parent_id"), "drop_off_stop": s.get("drop_off_stop")}
        for s in base
    }
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
        crs = []
    incoming_ids = [c["student_id"] for c in crs if c.get("requested_route_id") == route_id]
    detail = {}
    if incoming_ids:
        detail = {s["id"]: s for s in supabase.table("passengers").select("id, name, parent_id").in_("id", incoming_ids).execute().data}
    for c in crs:
        sid = c["student_id"]
        if c.get("current_route_id") == route_id and c.get("requested_route_id") != route_id:
            students.pop(sid, None)  # moved OFF this route today
        if c.get("requested_route_id") == route_id:  # moved ONTO this route today
            d = detail.get(sid, {})
            students[sid] = {"id": sid, "name": d.get("name"), "parent_id": d.get("parent_id"), "drop_off_stop": c.get("requested_stop")}
    return [s for s in students.values() if s.get("parent_id")]


def trip_started(trip: dict) -> None:
    """Notify each parent whose child is on this trip's bus today: '<names>'s bus
    has started'. One note per parent per trip (dedup started:<trip>:<parent>)."""
    org_id, trip_id, route_id = trip.get("org_id"), trip.get("id"), trip.get("route_id")
    if not (org_id and route_id) or not _is_school(org_id):
        return
    today = datetime.now(LOCAL_TZ).date().isoformat()
    by_parent: dict = {}
    for s in _effective_students_on_route(org_id, route_id, today):
        by_parent.setdefault(s["parent_id"], []).append(s.get("name") or "Your child")
    for parent_id, names in by_parent.items():
        label = names[0] if len(names) == 1 else " & ".join(names)
        create_notification(
            org_id, "parent", parent_id, "trip_started",
            "Bus started", f"{label}'s bus has started.",
            related_id=trip_id, dedup_key=f"started:{trip_id}:{parent_id}",
        )


def child_arrived(trip: dict, stop_id: str) -> None:
    """When the bus reaches a stop, notify parents whose child's (effective)
    drop-off stop is THIS stop: '<name> has arrived'. One note per child per trip
    (dedup arrived:<trip>:<student>)."""
    org_id, trip_id, route_id = trip.get("org_id"), trip.get("id"), trip.get("route_id")
    if not (org_id and route_id) or not _is_school(org_id):
        return
    srow = supabase.table("route_stops").select("name").eq("id", stop_id).limit(1).execute().data
    if not srow:
        return
    stop_name = (srow[0].get("name") or "").strip().lower()
    if not stop_name:
        return
    today = datetime.now(LOCAL_TZ).date().isoformat()
    for s in _effective_students_on_route(org_id, route_id, today):
        drop = (s.get("drop_off_stop") or "").strip().lower()
        if drop and drop == stop_name:
            create_notification(
                org_id, "parent", s["parent_id"], "child_arrived",
                "Bus arrived", f"{s.get('name') or 'Your child'}'s bus has arrived.",
                related_id=trip_id, dedup_key=f"arrived:{trip_id}:{s['id']}",
            )
