"""Org center/hub locations (the university + optional branches), org-scoped.

Exactly one center is primary; marking a center primary unsets the others.
GET is readable by anyone who can view tracking (Full View uses the primary
center for distance calcs); mutations require manage_settings.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from auth import require_permission
from database import supabase

router = APIRouter(prefix="/centers", tags=["centers"])


class CenterCreate(BaseModel):
    name: str = Field(..., min_length=1)
    lat: float
    lng: float
    is_primary: bool = False


class CenterUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    lat: Optional[float] = None
    lng: Optional[float] = None
    is_primary: Optional[bool] = None


def _get_owned(center_id: str, org_id: str) -> dict:
    res = supabase.table("org_centers").select("*").eq("id", center_id).eq("org_id", org_id).limit(1).execute()
    if not res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Center not found.")
    return res.data[0]


def _clear_primary(org_id: str, except_id: Optional[str] = None) -> None:
    q = supabase.table("org_centers").update({"is_primary": False}).eq("org_id", org_id)
    if except_id:
        q = q.neq("id", except_id)
    q.execute()


@router.get("")
def list_centers(current_user: dict = Depends(require_permission("view_tracking"))):
    org_id = current_user["org_id"]
    rows = (
        supabase.table("org_centers")
        .select("id, name, lat, lng, is_primary, created_at")
        .eq("org_id", org_id)
        .order("is_primary", desc=True)
        .order("created_at", desc=False)
        .execute()
        .data
    )
    return {"count": len(rows), "centers": rows}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_center(
    body: CenterCreate,
    current_user: dict = Depends(require_permission("manage_settings")),
):
    org_id = current_user["org_id"]
    existing = supabase.table("org_centers").select("id").eq("org_id", org_id).execute().data
    # The first center is always primary; otherwise honour the request.
    make_primary = body.is_primary or len(existing) == 0
    if make_primary:
        _clear_primary(org_id)
    row = (
        supabase.table("org_centers")
        .insert({"org_id": org_id, "name": body.name, "lat": body.lat, "lng": body.lng, "is_primary": make_primary})
        .execute()
        .data[0]
    )
    return row


@router.patch("/{center_id}")
def update_center(
    center_id: str,
    body: CenterUpdate,
    current_user: dict = Depends(require_permission("manage_settings")),
):
    org_id = current_user["org_id"]
    _get_owned(center_id, org_id)
    update: dict = {}
    if body.name is not None:
        update["name"] = body.name
    if body.lat is not None:
        update["lat"] = body.lat
    if body.lng is not None:
        update["lng"] = body.lng
    if body.is_primary is True:
        _clear_primary(org_id, except_id=center_id)
        update["is_primary"] = True
    if not update:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update.")
    row = supabase.table("org_centers").update(update).eq("id", center_id).eq("org_id", org_id).execute().data[0]
    return row


@router.delete("/{center_id}", status_code=status.HTTP_200_OK)
def delete_center(
    center_id: str,
    current_user: dict = Depends(require_permission("manage_settings")),
):
    org_id = current_user["org_id"]
    center = _get_owned(center_id, org_id)
    supabase.table("org_centers").delete().eq("id", center_id).eq("org_id", org_id).execute()
    # If we removed the primary, promote the oldest remaining center.
    if center.get("is_primary"):
        remaining = (
            supabase.table("org_centers").select("id").eq("org_id", org_id).order("created_at", desc=False).limit(1).execute().data
        )
        if remaining:
            supabase.table("org_centers").update({"is_primary": True}).eq("id", remaining[0]["id"]).execute()
    return {"deleted": center_id}
