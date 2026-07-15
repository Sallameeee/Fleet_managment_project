"""Change requests (School module) — parent-requested ONE-DAY route/bus changes
for a SPECIFIC child; admin approves/rejects based on capacity.

  * POST /change-requests            (parent)  — request a change for one child
  * GET  /change-requests            (admin)   — list pending + history + capacity impact
  * POST /change-requests/{id}/decision (admin)— approve / reject (capacity-guarded)

School-only throughout (University callers get a 403).
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from auth import require_permission, require_role
from capacity_logic import (
    LOCAL_TZ,
    approved_change_maps,
    base_assigned_map,
    occupancy,
    read_cutoff,
    require_school_org,
    route_vehicle_map,
)
from database import supabase

router = APIRouter(prefix="/change-requests", tags=["change-requests"])


# ── Parent: create a request for ONE child ───────────────────────────────────
class ChangeRequestIn(BaseModel):
    student_id: str = Field(..., min_length=1)
    requested_route_id: str = Field(..., min_length=1)
    requested_stop: Optional[str] = None
    request_date: date  # the ONE day the change applies to


def _enforce_cutoff(org_id: str, request_date: date) -> None:
    """SAME-DAY cutoff: a request for day D must be submitted before the org's
    cutoff time ON day D (default 8 PM). So today is requestable until the cutoff,
    and any future day is fine; a past day (or today after cutoff) is blocked.
    Cutoff is per-org (organizations.change_cutoff_time)."""
    cutoff = read_cutoff(org_id)
    deadline = datetime.combine(request_date, cutoff, tzinfo=LOCAL_TZ)
    if datetime.now(LOCAL_TZ) >= deadline:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"The cutoff has passed. Requests for {request_date.isoformat()} must be made "
                f"before {cutoff.strftime('%H:%M')} that day. Please pick a later date."
            ),
        )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_change_request(
    body: ChangeRequestIn,
    current_user: dict = Depends(require_role("passenger")),
):
    """A parent requests a one-day route/bus change for ONE of their children.
    A parent with several kids submits a SEPARATE request per child."""
    org_id = current_user["org_id"]
    parent_id = current_user["id"]
    require_school_org(org_id)

    # The child must belong to THIS parent, in THIS org.
    st = (
        supabase.table("passengers")
        .select("id, route_id, name")
        .eq("id", body.student_id)
        .eq("org_id", org_id)
        .eq("parent_id", parent_id)
        .limit(1)
        .execute()
        .data
    )
    if not st:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That child is not linked to your account.")
    student = st[0]

    rr = supabase.table("routes").select("id").eq("id", body.requested_route_id).eq("org_id", org_id).limit(1).execute().data
    if not rr:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such route in your organization.")

    _enforce_cutoff(org_id, body.request_date)

    # One active request per child per day.
    dup = (
        supabase.table("change_requests")
        .select("id")
        .eq("student_id", body.student_id)
        .eq("request_date", body.request_date.isoformat())
        .in_("status", ["pending", "approved"])
        .limit(1)
        .execute()
        .data
    )
    if dup:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="There is already an active request for this child on that day.")

    payload = {
        "org_id": org_id,
        "student_id": body.student_id,
        "parent_id": parent_id,
        "current_route_id": student.get("route_id"),
        "requested_route_id": body.requested_route_id,
        "requested_stop": (body.requested_stop or "").strip() or None,
        "request_date": body.request_date.isoformat(),
        "status": "pending",
    }
    try:
        row = supabase.table("change_requests").insert(payload).execute().data[0]
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not create change request: {exc}")
    return {"id": row["id"], "status": "pending", "student_id": body.student_id, "request_date": payload["request_date"]}


# ── Admin: list with capacity impact ─────────────────────────────────────────
@router.get("")
def list_change_requests(
    current_user: dict = Depends(require_permission("manage_passengers")),
    status_filter: Optional[str] = Query(None, alias="status", description="pending|approved|rejected"),
):
    """All change requests (pending + history) with the capacity impact on BOTH
    buses: counts now, and after this child leaves the current bus / joins the
    requested bus (using the shared capacity logic)."""
    org_id = current_user["org_id"]
    require_school_org(org_id)

    q = supabase.table("change_requests").select("*").eq("org_id", org_id)
    if status_filter in ("pending", "approved", "rejected"):
        q = q.eq("status", status_filter)
    reqs = q.order("created_at", desc=True).execute().data

    student_ids = list({r["student_id"] for r in reqs})
    parent_ids = list({r["parent_id"] for r in reqs})
    route_ids = set()
    for r in reqs:
        for k in ("current_route_id", "requested_route_id"):
            if r.get(k):
                route_ids.add(r[k])

    students = {}
    if student_ids:
        students = {p["id"]: p for p in supabase.table("passengers").select("id, name, parent_email").in_("id", student_ids).execute().data}
    parents = {}
    if parent_ids:
        parents = {p["id"]: p for p in supabase.table("profiles").select("id, email, name").in_("id", parent_ids).execute().data}
    rnames = {}
    if route_ids:
        rnames = {x["id"]: x["name"] for x in supabase.table("routes").select("id, name").in_("id", list(route_ids)).execute().data}

    rv = route_vehicle_map(org_id)
    base = base_assigned_map(org_id)
    incoming, outgoing = approved_change_maps(org_id)

    def bus_view(route_id, date_iso, delta):
        if not route_id:
            return None
        v = rv.get(route_id, {})
        cap = v.get("capacity")
        occ_now = occupancy(route_id, date_iso, base, incoming, outgoing)
        occ_after = occ_now + delta
        return {
            "route_id": route_id,
            "route_name": rnames.get(route_id),
            "vehicle_bus_number": v.get("bus_number"),
            "capacity": cap,
            "count_now": occ_now,
            "count_after": occ_after,
            "seats_free_after": None if cap is None else cap - occ_after,
            "would_exceed": cap is not None and occ_after > cap,
        }

    out = []
    for r in reqs:
        d = r["request_date"]
        s = students.get(r["student_id"], {})
        p = parents.get(r["parent_id"], {})
        pending = r["status"] == "pending"
        # Only a PENDING approval moves the child (−1 current, +1 requested).
        # Approved/rejected already reflect their outcome, so no marginal delta.
        out.append(
            {
                "id": r["id"],
                "status": r["status"],
                "request_date": d,
                "student_id": r["student_id"],
                "student_name": s.get("name"),
                "parent_email": p.get("email") or s.get("parent_email"),
                "requested_stop": r.get("requested_stop"),
                "current_bus": bus_view(r.get("current_route_id"), d, -1 if pending else 0),
                "requested_bus": bus_view(r.get("requested_route_id"), d, 1 if pending else 0),
                "created_at": r.get("created_at"),
                "decided_at": r.get("decided_at"),
            }
        )
    return {"count": len(out), "change_requests": out}


# ── Admin: approve / reject ──────────────────────────────────────────────────
class DecisionIn(BaseModel):
    action: str  # 'approve' | 'reject'
    force: bool = False  # override the capacity block on approve


@router.post("/{req_id}/decision")
def decide_change_request(
    req_id: str,
    body: DecisionIn,
    current_user: dict = Depends(require_permission("manage_passengers")),
):
    """Approve or reject a pending request. APPROVE applies the one-day change
    (status -> approved, so the capacity counts reflect it). Approval is
    capacity-guarded: it BLOCKS if the requested bus would exceed capacity, unless
    `force=true` (soft block — the admin can still decide with the numbers in hand)."""
    org_id = current_user["org_id"]
    require_school_org(org_id)

    r = supabase.table("change_requests").select("*").eq("id", req_id).eq("org_id", org_id).limit(1).execute().data
    if not r:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Change request not found.")
    req = r[0]
    if req["status"] != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"This request is already {req['status']}.")

    action = (body.action or "").lower()
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="action must be 'approve' or 'reject'.")

    now = datetime.now(timezone.utc).isoformat()
    if action == "reject":
        supabase.table("change_requests").update(
            {"status": "rejected", "decided_at": now, "decided_by": current_user["id"]}
        ).eq("id", req_id).eq("org_id", org_id).execute()
        return {"id": req_id, "status": "rejected"}

    # APPROVE — capacity guard on the requested bus.
    rv = route_vehicle_map(org_id)
    base = base_assigned_map(org_id)
    incoming, outgoing = approved_change_maps(org_id)
    rr = req["requested_route_id"]
    cap = rv.get(rr, {}).get("capacity")
    count_after = occupancy(rr, req["request_date"], base, incoming, outgoing) + 1
    if cap is not None and count_after > cap and not body.force:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "capacity_exceeded",
                "message": f"The requested bus would exceed capacity ({count_after}/{cap}). Re-send with force=true to approve anyway.",
                "capacity": cap,
                "count_after": count_after,
            },
        )

    supabase.table("change_requests").update(
        {"status": "approved", "decided_at": now, "decided_by": current_user["id"]}
    ).eq("id", req_id).eq("org_id", org_id).execute()
    return {"id": req_id, "status": "approved", "requested_count_after": count_after, "capacity": cap}
