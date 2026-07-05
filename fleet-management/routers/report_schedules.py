"""Scheduled emailed reports — CRUD only (org-scoped, view_reports gated).

Storage + management ship now; ACTUAL email delivery is deferred to the Firebase
phase. These endpoints let a manager save, list, and remove recurring report
schedules; a later worker will read `report_schedules` and send the emails.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from auth import require_permission
from database import supabase

router = APIRouter(prefix="/report-schedules", tags=["report-schedules"])

VALID_FREQ = ["daily", "weekly", "monthly"]
VALID_SUBJECT = ["all", "vehicle", "driver"]
VALID_PERIOD = ["today", "week", "month"]
VALID_TYPES = ["drivers", "trips", "kilometers", "speed"]


class ScheduleCreate(BaseModel):
    name: Optional[str] = None
    frequency: str = Field(..., description="daily|weekly|monthly")
    subject_kind: str = Field("all", description="all|vehicle|driver")
    subject_id: Optional[str] = None
    types: List[str] = Field(..., min_length=1)
    period: str = Field("week", description="today|week|month")
    email: str = Field(..., min_length=3)


def _validate(body: ScheduleCreate) -> None:
    if body.frequency not in VALID_FREQ:
        raise HTTPException(400, f"frequency must be one of {VALID_FREQ}.")
    if body.subject_kind not in VALID_SUBJECT:
        raise HTTPException(400, f"subject_kind must be one of {VALID_SUBJECT}.")
    if body.period not in VALID_PERIOD:
        raise HTTPException(400, f"period must be one of {VALID_PERIOD}.")
    bad = [t for t in body.types if t not in VALID_TYPES]
    if bad:
        raise HTTPException(400, f"Unknown report type(s) {bad}. Valid: {VALID_TYPES}.")
    if body.subject_kind != "all" and not body.subject_id:
        raise HTTPException(400, "subject_id is required when subject_kind is not 'all'.")


@router.get("")
def list_schedules(current_user: dict = Depends(require_permission("view_reports"))):
    org_id = current_user["org_id"]
    rows = (
        supabase.table("report_schedules")
        .select("*")
        .eq("org_id", org_id)
        .order("created_at", desc=True)
        .execute()
        .data
    )
    return {"count": len(rows), "schedules": rows}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_schedule(
    body: ScheduleCreate,
    current_user: dict = Depends(require_permission("view_reports")),
):
    _validate(body)
    org_id = current_user["org_id"]
    payload = {
        "org_id": org_id,  # caller's org, never from the body
        "name": body.name,
        "frequency": body.frequency,
        "subject_kind": body.subject_kind,
        "subject_id": body.subject_id if body.subject_kind != "all" else None,
        "types": ",".join(body.types),
        "period": body.period,
        "email": body.email,
        "is_active": True,
    }
    try:
        row = supabase.table("report_schedules").insert(payload).execute().data[0]
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not create schedule: {exc}",
        )
    return row


@router.delete("/{schedule_id}", status_code=status.HTTP_200_OK)
def delete_schedule(
    schedule_id: str,
    current_user: dict = Depends(require_permission("view_reports")),
):
    org_id = current_user["org_id"]
    existing = (
        supabase.table("report_schedules")
        .select("id")
        .eq("id", schedule_id)
        .eq("org_id", org_id)
        .limit(1)
        .execute()
    )
    if not existing.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Schedule not found.",
        )
    supabase.table("report_schedules").delete().eq("id", schedule_id).eq("org_id", org_id).execute()
    return {"deleted": schedule_id}
