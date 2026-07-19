"""Profile-edit requests (School module).

A parent never edits their own personal info directly — they submit a request the
MANAGER approves. On approve the parent's info is applied; on reject nothing
changes. Mirrors change_requests. School-only throughout.

  * GET  /profile-requests/me         (parent)  — current info + any pending request
  * POST /profile-requests            (parent)  — submit an edit request
  * GET  /profile-requests            (manager) — list (current vs proposed)
  * POST /profile-requests/{id}/decision (manager) — approve / reject
"""

import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

import notifications_logic as notify
from auth import get_current_user, require_permission, require_role
from capacity_logic import require_school_org
from database import supabase

router = APIRouter(prefix="/profile-requests", tags=["profile-requests"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ProfileRequestIn(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None


def _clean(v: Optional[str]) -> Optional[str]:
    v = (v or "").strip()
    return v or None


@router.get("/me")
def my_profile(current_user: dict = Depends(require_role("passenger"))):
    """The parent's current personal info + their pending request (if any), so the
    app can prefill the edit form and disable submit while one is pending."""
    org_id = current_user["org_id"]
    require_school_org(org_id)
    p = supabase.table("profiles").select("name, phone, email").eq("id", current_user["id"]).limit(1).execute().data
    cur = p[0] if p else {}
    pend = (
        supabase.table("profile_change_requests")
        .select("id, proposed_name, proposed_phone, proposed_email, status, created_at")
        .eq("parent_id", current_user["id"])
        .eq("status", "pending")
        .limit(1)
        .execute()
        .data
    )
    return {
        "name": cur.get("name"),
        "phone": cur.get("phone"),
        "email": cur.get("email"),
        "pending": pend[0] if pend else None,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_profile_request(body: ProfileRequestIn, current_user: dict = Depends(require_role("passenger"))):
    """A parent requests changes to their name / phone / email. Only ONE pending
    request per parent at a time."""
    org_id = current_user["org_id"]
    parent_id = current_user["id"]
    require_school_org(org_id)

    name, phone, email = _clean(body.name), _clean(body.phone), _clean(body.email)
    if not any([name, phone, email]):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nothing to change.")
    if email and not _EMAIL_RE.match(email):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Please enter a valid email address.")

    # Only propose fields that actually differ from the current values.
    cur = supabase.table("profiles").select("name, phone, email").eq("id", parent_id).limit(1).execute().data
    c = cur[0] if cur else {}
    proposed_name = name if name and name != c.get("name") else None
    proposed_phone = phone if phone is not None and phone != (c.get("phone") or "") else None
    proposed_email = email if email and email.lower() != (c.get("email") or "").lower() else None
    if not any([proposed_name, proposed_phone, proposed_email]):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="These values match your current info.")

    payload = {
        "org_id": org_id,
        "parent_id": parent_id,
        "proposed_name": proposed_name,
        "proposed_phone": proposed_phone,
        "proposed_email": proposed_email,
        "status": "pending",
    }
    try:
        row = supabase.table("profile_change_requests").insert(payload).execute().data[0]
    except Exception as exc:
        # The partial unique index (one pending per parent) trips here.
        if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You already have a pending request. Please wait for it to be reviewed.")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not submit your request: {exc}")

    notify.profile_request_created(org_id, row["id"], c.get("name") or c.get("email"))
    return {"id": row["id"], "status": "pending"}


@router.get("")
def list_profile_requests(
    current_user: dict = Depends(require_permission("manage_passengers")),
    status_filter: Optional[str] = Query(None, alias="status"),
):
    """All profile-edit requests with the parent's CURRENT values alongside the
    proposed ones, so the manager can see exactly what would change."""
    org_id = current_user["org_id"]
    require_school_org(org_id)

    q = supabase.table("profile_change_requests").select("*").eq("org_id", org_id)
    if status_filter in ("pending", "approved", "rejected"):
        q = q.eq("status", status_filter)
    reqs = q.order("created_at", desc=True).execute().data

    parent_ids = list({r["parent_id"] for r in reqs})
    parents = {}
    if parent_ids:
        parents = {p["id"]: p for p in supabase.table("profiles").select("id, name, phone, email").in_("id", parent_ids).execute().data}

    out = []
    for r in reqs:
        p = parents.get(r["parent_id"], {})
        out.append(
            {
                "id": r["id"],
                "status": r["status"],
                "parent_id": r["parent_id"],
                "current": {"name": p.get("name"), "phone": p.get("phone"), "email": p.get("email")},
                "proposed": {"name": r.get("proposed_name"), "phone": r.get("proposed_phone"), "email": r.get("proposed_email")},
                "created_at": r.get("created_at"),
                "decided_at": r.get("decided_at"),
            }
        )
    return {"count": len(out), "profile_requests": out}


class DecisionIn(BaseModel):
    action: str  # 'approve' | 'reject'


def _apply(org_id: str, parent_id: str, req: dict) -> None:
    """Apply an approved request. Email is the auth login, so it is changed in
    Supabase Auth FIRST (raises if taken → the approval is aborted), then mirrored
    to the profile (email + username) and the children's parent_email."""
    updates: dict = {}
    if req.get("proposed_name"):
        updates["name"] = req["proposed_name"]
    if req.get("proposed_phone") is not None and req.get("proposed_phone") != "":
        updates["phone"] = req["proposed_phone"]

    new_email = req.get("proposed_email")
    if new_email:
        try:
            supabase.auth.admin.update_user_by_id(parent_id, {"email": new_email, "email_confirm": True})
        except Exception as exc:
            text = str(exc).lower()
            if "already" in text or "registered" in text or "exist" in text:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"The email '{new_email}' is already in use by another account.")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not update the login email: {exc}")
        updates["email"] = new_email
        updates["username"] = new_email  # parents log in by email

    if updates:
        supabase.table("profiles").update(updates).eq("id", parent_id).eq("org_id", org_id).execute()
    if new_email:
        supabase.table("passengers").update({"parent_email": new_email}).eq("parent_id", parent_id).eq("org_id", org_id).execute()


@router.post("/{req_id}/decision")
def decide_profile_request(
    req_id: str,
    body: DecisionIn,
    current_user: dict = Depends(require_permission("manage_passengers")),
):
    org_id = current_user["org_id"]
    require_school_org(org_id)

    r = supabase.table("profile_change_requests").select("*").eq("id", req_id).eq("org_id", org_id).limit(1).execute().data
    if not r:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile request not found.")
    req = r[0]
    if req["status"] != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"This request is already {req['status']}.")

    action = (body.action or "").lower()
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="action must be 'approve' or 'reject'.")

    if action == "approve":
        _apply(org_id, req["parent_id"], req)  # raises (and aborts) on a bad email change

    now = datetime.now(timezone.utc).isoformat()
    supabase.table("profile_change_requests").update(
        {"status": "approved" if action == "approve" else "rejected", "decided_at": now, "decided_by": current_user["id"]}
    ).eq("id", req_id).eq("org_id", org_id).execute()

    notify.profile_request_decided(org_id, req["parent_id"], req_id, action == "approve")
    return {"id": req_id, "status": "approved" if action == "approve" else "rejected"}
