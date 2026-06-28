"""Vehicle (bus) management routes — org-scoped, permission-gated.

Same tenant-isolation pattern as drivers: the org is always the caller's own
org (from their token), never anything supplied in the request.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from auth import require_permission
from database import supabase

router = APIRouter(prefix="/vehicles", tags=["vehicles"])


class VehicleCreate(BaseModel):
    bus_number: str = Field(..., min_length=1)
    plate_number: Optional[str] = None


@router.post("", status_code=status.HTTP_201_CREATED)
def create_vehicle(
    body: VehicleCreate,
    current_user: dict = Depends(require_permission("manage_vehicles")),
):
    # Tenant isolation: org is always the caller's own, from their token.
    org_id = current_user["org_id"]

    try:
        result = (
            supabase.table("vehicles")
            .insert(
                {
                    "org_id": org_id,  # caller's org, NOT from the body
                    "bus_number": body.bus_number,
                    "plate_number": body.plate_number,
                    "is_active": True,
                }
            )
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not create vehicle: {exc}",
        )

    v = result.data[0]
    return {
        "id": v["id"],
        "bus_number": v["bus_number"],
        "plate_number": v["plate_number"],
        "share_token": v["share_token"],  # permanent public tracking token
        "is_active": v["is_active"],
        "created_at": v["created_at"],
    }


@router.get("")
def list_vehicles(
    current_user: dict = Depends(require_permission("manage_vehicles")),
):
    # Tenant isolation: only the caller's own org.
    org_id = current_user["org_id"]

    result = (
        supabase.table("vehicles")
        .select("id, bus_number, plate_number, share_token, is_active, created_at")
        .eq("org_id", org_id)
        .order("created_at", desc=True)  # newest first
        .execute()
    )

    return {"count": len(result.data), "vehicles": result.data}
