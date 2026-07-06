"""Bus driver management (School module) — data-only entities, NOT app users.

A bus driver is the person physically driving the bus in a school org. They have
NO login/auth — they're just org-scoped data linked to an assignment (alongside
the supervisor, who is the app user who actually tracks). Every route is scoped to
the CALLER'S org, taken from their token — never the request body.

Guarded by `manage_drivers` (the same permission that manages supervisors). The
dashboard only surfaces these for module='school' orgs; University orgs simply
never call them.
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from auth import require_permission
from database import supabase

router = APIRouter(prefix="/bus-drivers", tags=["bus-drivers"])

_SELECT = "id, name, phone, license_number, license_start_date, license_end_date, created_at"
_RETURN_KEYS = ("id", "name", "phone", "license_number", "license_start_date", "license_end_date", "created_at")


class BusDriverCreate(BaseModel):
    name: str = Field(..., min_length=1)
    phone: Optional[str] = None
    license_number: Optional[str] = None
    license_start_date: Optional[date] = None
    license_end_date: Optional[date] = None


class BusDriverUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    phone: Optional[str] = None
    license_number: Optional[str] = None
    license_start_date: Optional[date] = None
    license_end_date: Optional[date] = None


def _out(row: dict) -> dict:
    return {k: row.get(k) for k in _RETURN_KEYS}


def _get_owned(bus_driver_id: str, org_id: str) -> dict:
    res = (
        supabase.table("bus_drivers").select("id").eq("id", bus_driver_id).eq("org_id", org_id).limit(1).execute()
    )
    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such bus driver in your organization.",
        )
    return res.data[0]


@router.get("")
def list_bus_drivers(current_user: dict = Depends(require_permission("manage_drivers"))):
    org_id = current_user["org_id"]
    rows = (
        supabase.table("bus_drivers")
        .select(_SELECT)
        .eq("org_id", org_id)
        .order("name", desc=False)
        .execute()
    ).data
    return {"count": len(rows), "bus_drivers": rows}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_bus_driver(
    body: BusDriverCreate,
    current_user: dict = Depends(require_permission("manage_drivers")),
):
    org_id = current_user["org_id"]
    payload = {
        "org_id": org_id,  # caller's org, NOT from the body
        "name": body.name,
        "phone": body.phone,
        "license_number": body.license_number,
        "license_start_date": body.license_start_date.isoformat() if body.license_start_date else None,
        "license_end_date": body.license_end_date.isoformat() if body.license_end_date else None,
    }
    try:
        row = supabase.table("bus_drivers").insert(payload).execute().data[0]
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not create bus driver: {exc}")
    return _out(row)


@router.patch("/{bus_driver_id}")
def update_bus_driver(
    bus_driver_id: str,
    body: BusDriverUpdate,
    current_user: dict = Depends(require_permission("manage_drivers")),
):
    org_id = current_user["org_id"]
    _get_owned(bus_driver_id, org_id)

    update: dict = {}
    if body.name is not None:
        update["name"] = body.name
    if "phone" in body.model_fields_set:
        update["phone"] = body.phone
    if "license_number" in body.model_fields_set:
        update["license_number"] = body.license_number
    if "license_start_date" in body.model_fields_set:
        update["license_start_date"] = body.license_start_date.isoformat() if body.license_start_date else None
    if "license_end_date" in body.model_fields_set:
        update["license_end_date"] = body.license_end_date.isoformat() if body.license_end_date else None
    if not update:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update.")

    try:
        row = (
            supabase.table("bus_drivers").update(update).eq("id", bus_driver_id).eq("org_id", org_id).execute()
        ).data[0]
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not update bus driver: {exc}")
    return _out(row)


@router.delete("/{bus_driver_id}", status_code=status.HTTP_200_OK)
def delete_bus_driver(
    bus_driver_id: str,
    current_user: dict = Depends(require_permission("manage_drivers")),
):
    """Delete a bus driver. Any assignments referencing them keep their history;
    the FK (ON DELETE SET NULL) just clears the link."""
    org_id = current_user["org_id"]
    _get_owned(bus_driver_id, org_id)
    supabase.table("bus_drivers").delete().eq("id", bus_driver_id).eq("org_id", org_id).execute()
    return {"deleted": bus_driver_id}
