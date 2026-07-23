"""Driver management routes (org-scoped).

Adding a driver mirrors org creation: create a Supabase Auth login account,
then a `profiles` row — with rollback if anything fails. Everything is scoped
to the CALLER'S org (taken from their token, never the request body).
"""

from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from auth import require_permission
from database import supabase
from utils import synthesize_login_email

router = APIRouter(prefix="/drivers", tags=["drivers"])

# `profiles.national_id` arrives with migration 035. Until that runs, selecting or
# writing it would 42703 and break the whole drivers page, so probe ONCE and fall
# back to the pre-migration shape. Deploy order therefore doesn't matter.
_HAS_NATIONAL_ID: Optional[bool] = None


def _has_national_id() -> bool:
    global _HAS_NATIONAL_ID
    if _HAS_NATIONAL_ID is None:
        try:
            supabase.table("profiles").select("national_id").limit(1).execute()
            _HAS_NATIONAL_ID = True
        except Exception:
            _HAS_NATIONAL_ID = False
    return _HAS_NATIONAL_ID


class DriverCreate(BaseModel):
    name: str = Field(..., min_length=1)
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=6)
    phone: Optional[str] = None
    email: Optional[str] = None
    license_number: Optional[str] = None
    license_start_date: Optional[date] = None
    license_expiry_date: Optional[date] = None
    # SCHOOL supervisors carry a national ID instead of a driving licence.
    national_id: Optional[str] = None


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


def _provision_driver(
    org_id: str,
    *,
    name: str,
    username: str,
    password: str,
    phone: Optional[str] = None,
    email: Optional[str] = None,
    license_number: Optional[str] = None,
    license_start_date: Optional[str] = None,  # ISO string or None
    license_expiry_date: Optional[str] = None,
    national_id: Optional[str] = None,
) -> dict:
    """Create the auth login + driver profile (role='driver'), scoped to `org_id`,
    rolling back the auth user if the profile insert fails. Raises HTTPException on
    any failure. Shared by the single-create endpoint and the bulk import."""
    login_email = email or synthesize_login_email(username, org_id)
    created_user_id: Optional[str] = None
    try:
        auth_response = supabase.auth.admin.create_user(
            {"email": login_email, "password": password, "email_confirm": True}
        )
        created_user_id = auth_response.user.id
    except Exception as exc:
        if _looks_like_email_taken(exc):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"A login account for '{login_email}' already exists.")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not create login account for the driver: {exc}")

    try:
        profile_payload = {
            "id": created_user_id,
            "org_id": org_id,
            "name": name,
            "email": login_email,
            "phone": phone,
            "username": username,
            "role": "driver",
            "permissions": {},
            "is_active": True,
            "license_number": license_number,
            "license_start_date": license_start_date,
            "license_expiry_date": license_expiry_date,
            **({"national_id": national_id} if _has_national_id() else {}),
        }
        result = supabase.table("profiles").insert(profile_payload).execute()
    except Exception as exc:
        _delete_auth_user(created_user_id)  # never orphan an auth account
        if _looks_like_duplicate(exc):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Username '{username}' is already taken in your organization.")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not create driver profile (login account was rolled back): {exc}")
    return result.data[0]


def _bulk_msg(exc: Exception) -> str:
    return str(exc.detail) if isinstance(exc, HTTPException) else str(exc)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_driver(
    body: DriverCreate,
    current_user: dict = Depends(require_permission("manage_drivers")),
):
    # Tenant isolation: org is ALWAYS the caller's own, from their token.
    org_id = current_user["org_id"]
    driver = _provision_driver(
        org_id,
        name=body.name,
        username=body.username,
        password=body.password,
        phone=body.phone,
        email=body.email,
        license_number=body.license_number,
        license_start_date=body.license_start_date.isoformat() if body.license_start_date else None,
        license_expiry_date=body.license_expiry_date.isoformat() if body.license_expiry_date else None,
        national_id=body.national_id,
    )
    return {
        "id": driver["id"],
        "name": driver["name"],
        "username": driver["username"],
        "login_email": driver["email"],
        "role": driver["role"],
    }


class BulkDriverRow(BaseModel):
    name: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    phone: Optional[str] = None
    national_id: Optional[str] = None


class BulkDriverRequest(BaseModel):
    rows: List[BulkDriverRow]


@router.post("/bulk")
def bulk_create_drivers(
    body: BulkDriverRequest,
    current_user: dict = Depends(require_permission("manage_drivers")),
):
    """Create many drivers/supervisors from parsed rows. Each row is attempted
    independently; per-row failures (missing field, duplicate username) are
    reported with the CSV row number so one bad row can't break the batch."""
    org_id = current_user["org_id"]
    created = 0
    errors = []
    for i, row in enumerate(body.rows):
        label = (row.username or "").strip() or (row.name or "").strip()
        try:
            name = (row.name or "").strip()
            username = (row.username or "").strip()
            password = row.password or ""
            if not name:
                raise ValueError("Name is required.")
            if not username:
                raise ValueError("Username is required.")
            if len(password) < 6:
                raise ValueError("Password must be at least 6 characters.")
            _provision_driver(
                org_id, name=name, username=username, password=password,
                phone=(row.phone or "").strip() or None,
                national_id=(row.national_id or "").strip() or None,
            )
            created += 1
        except Exception as exc:
            errors.append({"row": i + 2, "label": label, "error": _bulk_msg(exc)})  # +2: header + 1-indexed
    return {"created": created, "failed": len(errors), "errors": errors}


@router.get("")
def list_drivers(
    current_user: dict = Depends(require_permission("manage_drivers")),
):
    # Tenant isolation: only the caller's own org, from their token/profile.
    org_id = current_user["org_id"]

    drivers = (
        supabase.table("profiles")
        .select(
            "id, name, username, phone, is_active, created_at, license_number, "
            "license_start_date, license_expiry_date" + (", national_id" if _has_national_id() else "")
        )
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


class DriverUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    phone: Optional[str] = None
    is_active: Optional[bool] = None
    license_number: Optional[str] = None
    license_start_date: Optional[date] = None
    license_expiry_date: Optional[date] = None
    national_id: Optional[str] = None


def _get_owned_driver(driver_id: str, org_id: str) -> dict:
    res = (
        supabase.table("profiles")
        .select("id")
        .eq("id", driver_id)
        .eq("org_id", org_id)
        .eq("role", "driver")
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such driver in your organization.",
        )
    return res.data[0]


@router.patch("/{driver_id}")
def update_driver(
    driver_id: str,
    body: DriverUpdate,
    current_user: dict = Depends(require_permission("manage_drivers")),
):
    org_id = current_user["org_id"]
    _get_owned_driver(driver_id, org_id)

    update: dict = {}
    if body.name is not None:
        update["name"] = body.name
    if "phone" in body.model_fields_set:
        update["phone"] = body.phone
    if body.is_active is not None:
        update["is_active"] = body.is_active
    if "license_number" in body.model_fields_set:
        update["license_number"] = body.license_number
    if "license_start_date" in body.model_fields_set:
        update["license_start_date"] = body.license_start_date.isoformat() if body.license_start_date else None
    if "license_expiry_date" in body.model_fields_set:
        update["license_expiry_date"] = body.license_expiry_date.isoformat() if body.license_expiry_date else None
    if "national_id" in body.model_fields_set and _has_national_id():
        update["national_id"] = body.national_id
    if not update:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update.")

    try:
        row = (
            supabase.table("profiles")
            .update(update)
            .eq("id", driver_id)
            .eq("org_id", org_id)
            .execute()
        ).data[0]
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not update driver: {exc}")
    return {
        "id": row["id"],
        "name": row["name"],
        "username": row["username"],
        "phone": row.get("phone"),
        "is_active": row["is_active"],
        "license_number": row.get("license_number"),
        "license_start_date": row.get("license_start_date"),
        "license_expiry_date": row.get("license_expiry_date"),
    }


@router.delete("/{driver_id}", status_code=status.HTTP_200_OK)
def delete_driver(
    driver_id: str,
    current_user: dict = Depends(require_permission("manage_drivers")),
):
    """Delete a driver. Blocked if any assignment still references them (409) —
    reassign/remove those first. Drivers with trip history usually can't be
    hard-deleted (FK from trips); in that case we report it so you can instead
    deactivate (PATCH is_active=false)."""
    org_id = current_user["org_id"]
    _get_owned_driver(driver_id, org_id)

    used = (
        supabase.table("assignments").select("id", count="exact").eq("driver_id", driver_id).execute()
    )
    if (used.count or 0) > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This driver is used by {used.count} assignment(s). Remove those first, or deactivate the driver instead.",
        )

    # Clean up group membership so the FK doesn't block the delete.
    try:
        supabase.table("driver_group_members").delete().eq("driver_id", driver_id).execute()
    except Exception:
        pass

    try:
        supabase.table("profiles").delete().eq("id", driver_id).eq("org_id", org_id).execute()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This driver has trip history and can't be deleted. Deactivate them instead.",
        )
    _delete_auth_user(driver_id)  # profiles.id == the auth user id
    return {"deleted": driver_id}
