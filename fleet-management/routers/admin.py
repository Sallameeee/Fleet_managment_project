"""Platform-operator (super-admin) cross-org views.

Distinct from the org-scoped routers: these span ALL organizations.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from auth import require_super_admin, require_super_permission
from database import supabase

router = APIRouter(prefix="/admin", tags=["admin"])

# The permission vocabulary for PLATFORM staff (super_admins.permissions). These
# gate the super-admin panel itself, distinct from org users' permissions.
PLATFORM_PERMISSION_KEYS = [
    "manage_orgs",
    "manage_orgs_status",
    "view_finance",
    "manage_platform_users",
    "view_all",  # root/owner flag: bypasses individual checks
]


def _clean_permissions(raw: dict) -> dict:
    """Keep only known platform permission keys, coerced to bools."""
    raw = raw or {}
    return {k: bool(raw.get(k)) for k in PLATFORM_PERMISSION_KEYS if k in raw}


@router.get("/vehicles")
def all_vehicles(_admin: dict = Depends(require_super_admin)):
    """Every vehicle across every org, with its org name and live-trip flag."""
    vehicles = (
        supabase.table("vehicles")
        .select("id, org_id, bus_number, plate_number, is_active")
        .order("created_at", desc=True)
        .execute()
    ).data

    # Org names (one query).
    org_ids = list({v["org_id"] for v in vehicles if v.get("org_id")})
    org_names: dict = {}
    if org_ids:
        for o in (
            supabase.table("organizations").select("id, name").in_("id", org_ids).execute().data
        ):
            org_names[o["id"]] = o["name"]

    # Vehicles with an active trip right now (one query -> set).
    active_vehicle_ids = {
        t["vehicle_id"]
        for t in supabase.table("trips").select("vehicle_id").eq("status", "active").execute().data
        if t.get("vehicle_id")
    }

    out = [
        {
            "id": v["id"],
            "org_id": v["org_id"],
            "org_name": org_names.get(v["org_id"]),
            "bus_number": v["bus_number"],
            "plate_number": v["plate_number"],
            "is_active": v["is_active"],
            "has_active_trip": v["id"] in active_vehicle_ids,
        }
        for v in vehicles
    ]
    return {"count": len(out), "vehicles": out}


@router.get("/drivers")
def all_drivers(_admin: dict = Depends(require_super_admin)):
    """Every driver across every org, with org name, online flag, and the bus
    they're currently on (if running an active trip)."""
    drivers = (
        supabase.table("profiles")
        .select("id, name, username, org_id, is_active")
        .eq("role", "driver")
        .order("created_at", desc=True)
        .execute()
    ).data

    # Org names (one query).
    org_ids = list({d["org_id"] for d in drivers if d.get("org_id")})
    org_names: dict = {}
    if org_ids:
        for o in (
            supabase.table("organizations").select("id, name").in_("id", org_ids).execute().data
        ):
            org_names[o["id"]] = o["name"]

    # Active trips -> first active trip per driver (one query).
    active = (
        supabase.table("trips")
        .select("id, driver_id, vehicle_id")
        .eq("status", "active")
        .execute()
    ).data
    trip_by_driver: dict = {}
    for t in active:
        trip_by_driver.setdefault(t["driver_id"], t)
    active_trip_ids = [t["id"] for t in active]

    # Bus numbers for those active trips' vehicles (one query).
    vehicle_ids = list({t["vehicle_id"] for t in active if t.get("vehicle_id")})
    bus: dict = {}
    if vehicle_ids:
        for v in (
            supabase.table("vehicles").select("id, bus_number").in_("id", vehicle_ids).execute().data
        ):
            bus[v["id"]] = v["bus_number"]

    # ONLINE = a ping in the last 2 minutes. Efficiency: we DON'T scan the whole
    # pings table — we restrict to the (few) active trips' ids AND recorded_at >=
    # cutoff, so PostgREST filters on the indexed trip_id + timestamp only.
    online_drivers: set = set()
    if active_trip_ids:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        recent = (
            supabase.table("location_pings")
            .select("driver_id")
            .in_("trip_id", active_trip_ids)
            .gte("recorded_at", cutoff)
            .execute()
        ).data
        online_drivers = {p["driver_id"] for p in recent}

    out = []
    for d in drivers:
        trip = trip_by_driver.get(d["id"])
        veh_id = trip["vehicle_id"] if trip else None
        out.append(
            {
                "id": d["id"],
                "name": d["name"],
                "username": d["username"],
                "org_id": d["org_id"],
                "org_name": org_names.get(d["org_id"]),
                "is_active": d["is_active"],
                "online": d["id"] in online_drivers,
                "current_vehicle": bus.get(veh_id) if veh_id else None,
            }
        )
    return {"count": len(out), "drivers": out}


# --- Platform staff (super-admin-panel users) --------------------------------

class PlatformUserCreate(BaseModel):
    name: str = Field(..., min_length=1)
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=6)
    permissions: dict = Field(default_factory=dict)


class PlatformUserUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    permissions: Optional[dict] = None
    is_active: Optional[bool] = None


def _public_staff(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row.get("name"),
        "email": row.get("email"),
        "permissions": row.get("permissions") or {},
        "is_active": row.get("is_active", True),
        "created_at": row.get("created_at"),
    }


@router.get("/users")
def list_platform_users(
    _admin: dict = Depends(require_super_permission("manage_platform_users")),
):
    rows = (
        supabase.table("super_admins")
        .select("id, name, email, permissions, is_active, created_at")
        .order("created_at", desc=False)
        .execute()
    ).data
    return {"count": len(rows), "users": [_public_staff(r) for r in rows]}


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_platform_user(
    body: PlatformUserCreate,
    admin: dict = Depends(require_super_permission("manage_platform_users")),
):
    perms = _clean_permissions(body.permissions)

    # Create the Supabase Auth login (auto-confirmed), then the super_admins row.
    try:
        au = supabase.auth.admin.create_user(
            {"email": body.email, "password": body.password, "email_confirm": True}
        )
        uid = au.user.id
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not create login account: {exc}",
        )

    try:
        row = (
            supabase.table("super_admins")
            .insert(
                {
                    "id": uid,
                    "name": body.name,
                    "email": body.email,
                    "permissions": perms,
                    "is_active": True,
                }
            )
            .execute()
        ).data[0]
    except Exception as exc:
        try:
            supabase.auth.admin.delete_user(uid)  # rollback the auth user
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not create platform user (login account rolled back): {exc}",
        )

    try:
        supabase.table("audit_log").insert(
            {"actor_id": admin["id"], "action": "platform_user.create", "target": uid,
             "meta": {"email": body.email}}
        ).execute()
    except Exception:
        pass
    return _public_staff(row)


@router.patch("/users/{user_id}")
def update_platform_user(
    user_id: str,
    body: PlatformUserUpdate,
    admin: dict = Depends(require_super_permission("manage_platform_users")),
):
    fields: dict = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.permissions is not None:
        fields["permissions"] = _clean_permissions(body.permissions)
    if body.is_active is not None:
        # Guard against locking yourself out.
        if user_id == admin["id"] and body.is_active is False:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot deactivate your own platform account.",
            )
        fields["is_active"] = body.is_active

    if not fields:
        cur = supabase.table("super_admins").select("*").eq("id", user_id).limit(1).execute().data
        if not cur:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No platform user '{user_id}'.")
        return _public_staff(cur[0])

    res = supabase.table("super_admins").update(fields).eq("id", user_id).execute()
    if not res.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No platform user '{user_id}'.")
    return _public_staff(res.data[0])


@router.delete("/users/{user_id}")
def delete_platform_user(
    user_id: str,
    admin: dict = Depends(require_super_permission("manage_platform_users")),
):
    if user_id == admin["id"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own platform account.",
        )
    cur = supabase.table("super_admins").select("id, email").eq("id", user_id).limit(1).execute().data
    if not cur:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No platform user '{user_id}'.")

    supabase.table("super_admins").delete().eq("id", user_id).execute()
    try:
        supabase.auth.admin.delete_user(user_id)
    except Exception:
        pass
    try:
        supabase.table("audit_log").insert(
            {"actor_id": admin["id"], "action": "platform_user.delete", "target": user_id,
             "meta": {"email": cur[0]["email"]}}
        ).execute()
    except Exception:
        pass
    return {"deleted": user_id}
