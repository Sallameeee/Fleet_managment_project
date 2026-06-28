"""Staff (org user) management — org-scoped, permission-gated.

Lets an owner/manager create staff accounts (manager / dispatcher / viewer)
under THEIR org. Same create-with-rollback pattern as drivers, plus a
privilege-escalation guard: you cannot grant a permission you don't hold.
"""

from typing import Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from auth import require_permission
from database import supabase
from utils import PERMISSION_KEYS, synthesize_login_email

router = APIRouter(prefix="/users", tags=["users"])

# Roles creatable here. 'owner' is set once at org creation; 'driver' has its
# own endpoint (POST /drivers).
StaffRole = Literal["manager", "dispatcher", "viewer"]

# Sensible starting permissions per role if the caller doesn't specify any.
ROLE_DEFAULT_PERMISSIONS: Dict[str, list] = {
    "manager": [
        "manage_users",
        "manage_drivers",
        "manage_vehicles",
        "manage_devices",
        "manage_routes",
        "manage_trips",
        "view_tracking",
        "view_reports",
    ],
    "dispatcher": ["manage_routes", "manage_trips", "view_tracking", "view_reports"],
    "viewer": ["view_tracking", "view_reports"],
}


class UserCreate(BaseModel):
    name: str = Field(..., min_length=1)
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=6)
    role: StaffRole
    phone: Optional[str] = None
    email: Optional[str] = None
    # Optional explicit permission flags. If omitted, role defaults are used.
    permissions: Optional[Dict[str, bool]] = None


def _delete_auth_user(user_id: str) -> None:
    try:
        supabase.auth.admin.delete_user(user_id)
    except Exception:
        pass


def _looks_like_duplicate(exc: Exception) -> bool:
    text = str(exc).lower()
    return "23505" in text or "duplicate key" in text or "already exists" in text


def _looks_like_email_taken(exc: Exception) -> bool:
    text = str(exc).lower()
    return "already" in text and ("registered" in text or "exist" in text)


def _resolve_permissions(body: UserCreate, current_user: dict) -> Dict[str, bool]:
    """Compute the effective permissions, capped to what the caller may grant.

    - Requested = body.permissions (true flags) or the role defaults.
    - A caller can only grant a flag they themselves hold; owners hold all.
    - Returns a dict of {flag: True} for granted flags only.
    """
    if body.permissions is not None:
        requested = {k for k, v in body.permissions.items() if v is True}
    else:
        requested = set(ROLE_DEFAULT_PERMISSIONS[body.role])

    is_owner = current_user.get("role") == "owner"
    caller_perms = current_user.get("permissions") or {}

    granted = {}
    for flag in requested:
        if flag not in PERMISSION_KEYS:
            continue  # ignore unknown flags
        if is_owner or caller_perms.get(flag) is True:
            granted[flag] = True
    return granted


@router.post("", status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreate,
    current_user: dict = Depends(require_permission("manage_users")),
):
    # Tenant isolation: org is always the caller's own, from their token.
    org_id = current_user["org_id"]

    permissions = _resolve_permissions(body, current_user)
    login_email = body.email or synthesize_login_email(body.username, org_id)

    created_user_id: Optional[str] = None

    # --- Step a: create the auth login account (auto-confirmed) ---
    try:
        auth_response = supabase.auth.admin.create_user(
            {"email": login_email, "password": body.password, "email_confirm": True}
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
            detail=f"Could not create login account for the user: {exc}",
        )

    # --- Step b: insert the profile, scoped to the caller's org ---
    try:
        profile_payload = {
            "id": created_user_id,
            "org_id": org_id,  # caller's org, NOT from the body
            "name": body.name,
            "email": login_email,
            "phone": body.phone,
            "username": body.username,
            "role": body.role,
            "permissions": permissions,
            "is_active": True,
        }
        result = supabase.table("profiles").insert(profile_payload).execute()
    except Exception as exc:
        _delete_auth_user(created_user_id)  # rollback step a
        if _looks_like_duplicate(exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Username '{body.username}' is already taken in your organization.",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not create user profile (login account was rolled back): {exc}",
        )

    u = result.data[0]
    return {
        "id": u["id"],
        "name": u["name"],
        "username": u["username"],
        "login_email": login_email,
        "role": u["role"],
        "permissions": u["permissions"],
    }


# Staff roles listed here — excludes owners and drivers (drivers have their
# own GET /drivers).
STAFF_ROLES = ["manager", "dispatcher", "viewer"]


@router.get("")
def list_users(
    current_user: dict = Depends(require_permission("manage_users")),
):
    # Tenant isolation: only the caller's own org.
    org_id = current_user["org_id"]

    result = (
        supabase.table("profiles")
        .select("id, name, username, role, permissions, is_active, created_at")
        .eq("org_id", org_id)
        .in_("role", STAFF_ROLES)
        .order("created_at", desc=True)  # newest first
        .execute()
    )

    return {"count": len(result.data), "users": result.data}
