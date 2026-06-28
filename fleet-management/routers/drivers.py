"""Driver management routes (org-scoped).

Adding a driver mirrors org creation: create a Supabase Auth login account,
then a `profiles` row — with rollback if anything fails. Everything is scoped
to the CALLER'S org (taken from their token, never the request body).
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from auth import require_permission
from database import supabase
from utils import synthesize_login_email

router = APIRouter(prefix="/drivers", tags=["drivers"])


class DriverCreate(BaseModel):
    name: str = Field(..., min_length=1)
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=6)
    phone: Optional[str] = None
    email: Optional[str] = None


def _delete_auth_user(user_id: str) -> None:
    """Best-effort rollback of a created auth user."""
    try:
        supabase.auth.admin.delete_user(user_id)
    except Exception:
        pass


def _looks_like_duplicate(exc: Exception) -> bool:
    """True if the exception is a Postgres unique-constraint violation."""
    text = str(exc).lower()
    return "23505" in text or "duplicate key" in text or "already exists" in text


def _looks_like_email_taken(exc: Exception) -> bool:
    """True if Supabase Auth rejected the email as already registered."""
    text = str(exc).lower()
    return "already" in text and ("registered" in text or "exist" in text)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_driver(
    body: DriverCreate,
    current_user: dict = Depends(require_permission("manage_drivers")),
):
    # Tenant isolation: the org is ALWAYS the caller's own org, from their
    # token/profile — never anything supplied in the request body.
    org_id = current_user["org_id"]

    # Drivers may not have real emails; synthesize an org-scoped one so the
    # same username can exist in other orgs without colliding in Supabase Auth.
    login_email = body.email or synthesize_login_email(body.username, org_id)

    created_user_id: Optional[str] = None

    # --- Step a: create the auth login account (auto-confirmed) ---
    try:
        auth_response = supabase.auth.admin.create_user(
            {
                "email": login_email,
                "password": body.password,
                "email_confirm": True,
            }
        )
        created_user_id = auth_response.user.id
    except Exception as exc:
        if _looks_like_email_taken(exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A login account for '{login_email}' already exists.",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not create login account for the driver: {exc}",
        )

    # --- Step b: insert the driver profile, scoped to the caller's org ---
    try:
        profile_payload = {
            "id": created_user_id,  # matches the auth user id
            "org_id": org_id,  # caller's org, NOT from the body
            "name": body.name,
            "email": login_email,
            "phone": body.phone,
            "username": body.username,
            "role": "driver",
            "permissions": {},  # drivers get no management permissions
            "is_active": True,
        }
        result = supabase.table("profiles").insert(profile_payload).execute()
    except Exception as exc:
        # Roll back the auth user so a failed insert never orphans an account.
        _delete_auth_user(created_user_id)
        if _looks_like_duplicate(exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Username '{body.username}' is already taken in your organization.",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not create driver profile (login account was rolled back): {exc}",
        )

    driver = result.data[0]
    return {
        "id": driver["id"],
        "name": driver["name"],
        "username": driver["username"],
        "login_email": login_email,
        "role": driver["role"],
    }


@router.get("")
def list_drivers(
    current_user: dict = Depends(require_permission("manage_drivers")),
):
    # Tenant isolation: only the caller's own org, from their token/profile.
    org_id = current_user["org_id"]

    drivers = (
        supabase.table("profiles")
        .select("id, name, username, phone, is_active, created_at")
        .eq("org_id", org_id)
        .eq("role", "driver")
        .order("created_at", desc=True)  # newest first
        .execute()
    ).data

    # Enrich with online status + current vehicle. Same efficient pattern as
    # /admin/drivers: bound to this org's ACTIVE trips, and the "online" query is
    # restricted to those trips' ids + a 2-minute window (no full pings scan).
    active = (
        supabase.table("trips")
        .select("id, driver_id, vehicle_id")
        .eq("org_id", org_id)
        .eq("status", "active")
        .execute()
    ).data
    trip_by_driver: dict = {}
    for t in active:
        trip_by_driver.setdefault(t["driver_id"], t)
    active_trip_ids = [t["id"] for t in active]

    vehicle_ids = list({t["vehicle_id"] for t in active if t.get("vehicle_id")})
    bus: dict = {}
    if vehicle_ids:
        for v in (
            supabase.table("vehicles").select("id, bus_number").in_("id", vehicle_ids).execute().data
        ):
            bus[v["id"]] = v["bus_number"]

    online_ids: set = set()
    if active_trip_ids:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        recent = (
            supabase.table("location_pings")
            .select("driver_id")
            .in_("trip_id", active_trip_ids)
            .gte("recorded_at", cutoff)
            .execute()
        ).data
        online_ids = {p["driver_id"] for p in recent}

    for d in drivers:
        trip = trip_by_driver.get(d["id"])
        veh_id = trip["vehicle_id"] if trip else None
        d["online"] = d["id"] in online_ids
        d["current_vehicle"] = bus.get(veh_id) if veh_id else None

    return {"count": len(drivers), "drivers": drivers}
