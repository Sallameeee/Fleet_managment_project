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
    capacity: Optional[int] = Field(default=None, ge=0)


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
                    "capacity": body.capacity,
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
        "capacity": v.get("capacity"),
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
        .select("id, bus_number, plate_number, capacity, share_token, is_active, created_at")
        .eq("org_id", org_id)
        .order("created_at", desc=True)  # newest first
        .execute()
    )

    return {"count": len(result.data), "vehicles": result.data}


class VehicleUpdate(BaseModel):
    bus_number: Optional[str] = Field(None, min_length=1)
    plate_number: Optional[str] = None
    capacity: Optional[int] = Field(default=None, ge=0)
    is_active: Optional[bool] = None


def _get_owned_vehicle(vehicle_id: str, org_id: str) -> dict:
    res = (
        supabase.table("vehicles").select("id").eq("id", vehicle_id).eq("org_id", org_id).limit(1).execute()
    )
    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such vehicle in your organization.",
        )
    return res.data[0]


@router.patch("/{vehicle_id}")
def update_vehicle(
    vehicle_id: str,
    body: VehicleUpdate,
    current_user: dict = Depends(require_permission("manage_vehicles")),
):
    org_id = current_user["org_id"]
    _get_owned_vehicle(vehicle_id, org_id)

    update: dict = {}
    if body.bus_number is not None:
        update["bus_number"] = body.bus_number
    if "plate_number" in body.model_fields_set:
        update["plate_number"] = body.plate_number
    if "capacity" in body.model_fields_set:
        update["capacity"] = body.capacity
    if body.is_active is not None:
        update["is_active"] = body.is_active
    if not update:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update.")

    try:
        row = (
            supabase.table("vehicles").update(update).eq("id", vehicle_id).eq("org_id", org_id).execute()
        ).data[0]
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not update vehicle: {exc}")
    return {
        "id": row["id"],
        "bus_number": row["bus_number"],
        "plate_number": row["plate_number"],
        "capacity": row.get("capacity"),
        "share_token": row["share_token"],
        "is_active": row["is_active"],
        "created_at": row["created_at"],
    }


@router.delete("/{vehicle_id}", status_code=status.HTTP_200_OK)
def delete_vehicle(
    vehicle_id: str,
    current_user: dict = Depends(require_permission("manage_vehicles")),
):
    """Delete a vehicle. Blocked if any assignment still references it (409).
    Vehicles with trip history usually can't be hard-deleted (FK from trips);
    in that case we report it so you can deactivate instead."""
    org_id = current_user["org_id"]
    _get_owned_vehicle(vehicle_id, org_id)

    used = (
        supabase.table("assignments").select("id", count="exact").eq("vehicle_id", vehicle_id).execute()
    )
    if (used.count or 0) > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This vehicle is used by {used.count} assignment(s). Remove those first, or deactivate the vehicle instead.",
        )

    try:
        supabase.table("vehicles").delete().eq("id", vehicle_id).eq("org_id", org_id).execute()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This vehicle has trip history and can't be deleted. Deactivate it instead.",
        )
    return {"deleted": vehicle_id}
