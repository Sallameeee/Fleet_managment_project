"""Parent-reported issues / complaints (School module).

  * POST /parent-reports            (parent)  — submit an issue → notifies manager
  * GET  /parent-reports/mine       (parent)  — the parent's own submissions
  * GET  /parent-reports            (manager) — list all, with parent + child names
  * POST /parent-reports/{id}/resolve (manager) — mark resolved

School-only throughout (require_school_org). University callers get a 403.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

import notifications_logic as notify
from auth import require_permission, require_role
from capacity_logic import require_school_org
from database import supabase

router = APIRouter(prefix="/parent-reports", tags=["parent-reports"])


class ReportIn(BaseModel):
    subject: str = Field(..., min_length=1, max_length=200)
    message: str = Field(..., min_length=1, max_length=4000)
    student_id: Optional[str] = None


@router.post("", status_code=status.HTTP_201_CREATED)
def create_report(body: ReportIn, current_user: dict = Depends(require_role("passenger"))):
    org_id = current_user["org_id"]
    parent_id = current_user["id"]
    require_school_org(org_id)

    student_id = (body.student_id or "").strip() or None
    if student_id:
        # If a child is named it must belong to THIS parent, in THIS org.
        st = supabase.table("passengers").select("id").eq("id", student_id).eq("org_id", org_id).eq("parent_id", parent_id).limit(1).execute().data
        if not st:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That child is not linked to your account.")

    payload = {
        "org_id": org_id,
        "parent_id": parent_id,
        "student_id": student_id,
        "subject": body.subject.strip(),
        "message": body.message.strip(),
        "status": "open",
    }
    try:
        row = supabase.table("parent_reports").insert(payload).execute().data[0]
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not submit your report: {exc}")

    # Name for the manager notification.
    p = supabase.table("profiles").select("name, email").eq("id", parent_id).limit(1).execute().data
    parent_name = (p[0].get("name") or p[0].get("email")) if p else None
    notify.parent_report_created(org_id, row["id"], parent_name, payload["subject"])
    return {"id": row["id"], "status": "open"}


@router.get("/mine")
def my_reports(current_user: dict = Depends(require_role("passenger"))):
    org_id = current_user["org_id"]
    require_school_org(org_id)
    rows = (
        supabase.table("parent_reports")
        .select("id, subject, message, status, created_at, student_id")
        .eq("org_id", org_id)
        .eq("parent_id", current_user["id"])
        .order("created_at", desc=True)
        .execute()
        .data
    )
    return {"count": len(rows), "reports": rows}


@router.get("")
def list_reports(
    current_user: dict = Depends(require_permission("manage_passengers")),
    status_filter: Optional[str] = Query(None, alias="status"),
):
    org_id = current_user["org_id"]
    require_school_org(org_id)

    q = supabase.table("parent_reports").select("*").eq("org_id", org_id)
    if status_filter in ("open", "resolved"):
        q = q.eq("status", status_filter)
    reqs = q.order("created_at", desc=True).execute().data

    parent_ids = list({r["parent_id"] for r in reqs})
    student_ids = list({r["student_id"] for r in reqs if r.get("student_id")})
    parents, students = {}, {}
    if parent_ids:
        parents = {p["id"]: p for p in supabase.table("profiles").select("id, name, email, phone").in_("id", parent_ids).execute().data}
    if student_ids:
        students = {s["id"]: s for s in supabase.table("passengers").select("id, name, route_id").in_("id", student_ids).execute().data}

    # The attached students' CURRENT route names (the route/bus the child rides).
    route_ids = list({s.get("route_id") for s in students.values() if s.get("route_id")})
    routes = {}
    if route_ids:
        routes = {x["id"]: x["name"] for x in supabase.table("routes").select("id, name").in_("id", route_ids).execute().data}

    # Fallback parent contact (phone/email) from their children's contact fields —
    # for a parent whose profile has none. Reuses the parent→students model.
    contact = {}
    if parent_ids:
        for row in supabase.table("passengers").select("parent_id, parent_phone, parent_email").in_("parent_id", parent_ids).execute().data:
            c = contact.setdefault(row["parent_id"], {"phone": None, "email": None})
            if not c["phone"] and row.get("parent_phone"):
                c["phone"] = row["parent_phone"]
            if not c["email"] and row.get("parent_email"):
                c["email"] = row["parent_email"]

    out = []
    for r in reqs:
        p = parents.get(r["parent_id"], {})
        fb = contact.get(r["parent_id"], {})
        st = students.get(r.get("student_id")) or {}
        out.append(
            {
                "id": r["id"],
                "status": r["status"],
                "subject": r["subject"],
                "message": r["message"],
                "parent_name": p.get("name") or p.get("email"),
                "parent_email": p.get("email") or fb.get("email"),
                "parent_phone": p.get("phone") or fb.get("phone"),
                "student_name": st.get("name"),
                "student_route_name": routes.get(st.get("route_id")),
                "created_at": r.get("created_at"),
                "resolved_at": r.get("resolved_at"),
            }
        )
    return {"count": len(out), "reports": out}


@router.post("/{report_id}/resolve")
def resolve_report(report_id: str, current_user: dict = Depends(require_permission("manage_passengers"))):
    org_id = current_user["org_id"]
    require_school_org(org_id)
    r = supabase.table("parent_reports").select("id, status").eq("id", report_id).eq("org_id", org_id).limit(1).execute().data
    if not r:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found.")
    supabase.table("parent_reports").update(
        {"status": "resolved", "resolved_at": datetime.now(timezone.utc).isoformat(), "resolved_by": current_user["id"]}
    ).eq("id", report_id).eq("org_id", org_id).execute()
    return {"id": report_id, "status": "resolved"}
