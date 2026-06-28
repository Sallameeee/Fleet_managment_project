"""Organization management routes (super-admin only)."""

from collections import defaultdict
from datetime import date, time
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from auth import (
    mint_impersonation_token,
    require_permission,
    require_super_admin,
)
from database import supabase
from utils import PERMISSION_KEYS, slugify, synthesize_login_email

router = APIRouter(prefix="/organizations", tags=["organizations"])


# Every permission flag, all granted. The org owner gets full control of
# their own tenant. Org-level roles (manager/dispatcher/...) get narrower
# subsets via POST /users.
FULL_PERMISSIONS = {key: True for key in PERMISSION_KEYS}


class OrganizationCreate(BaseModel):
    name: str = Field(..., min_length=1, description="Organization / company name.")
    username: str = Field(
        ..., min_length=1, description="Login username for the org owner."
    )
    password: str = Field(
        ..., min_length=6, description="Owner login password (never stored or returned)."
    )
    address: Optional[str] = None
    email: Optional[str] = Field(
        default=None, description="Real email for the owner login, if available."
    )
    phone: Optional[str] = None
    slug: Optional[str] = Field(
        default=None,
        description="Optional login slug for the org (e.g. 'acme-bus'). "
        "Auto-generated from the name if omitted.",
    )
    plan: Literal["basic", "pro", "enterprise"] = "basic"
    max_devices: int = 10
    monthly_fee: float = 0
    subscription_expiry: Optional[date] = None


class OrgUpdate(BaseModel):
    plan: Optional[Literal["basic", "pro", "enterprise"]] = None
    max_devices: Optional[int] = None
    monthly_fee: Optional[float] = None
    subscription_expiry: Optional[date] = None
    address: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


class OrgStatusUpdate(BaseModel):
    status: Literal["active", "suspended", "expired"]


def _delete_auth_user(user_id: str) -> None:
    """Best-effort rollback of a created auth user."""
    try:
        supabase.auth.admin.delete_user(user_id)
    except Exception:
        pass


def _delete_org(org_id: str) -> None:
    """Best-effort rollback of a created organization row."""
    try:
        supabase.table("organizations").delete().eq("id", org_id).execute()
    except Exception:
        pass


def _generate_unique_slug(base: str) -> str:
    """Build a slug that isn't already taken, appending -2, -3, ... if needed."""
    base = slugify(base) or "org"
    try:
        existing = (
            supabase.table("organizations")
            .select("slug")
            .or_(f"slug.eq.{base},slug.like.{base}-%")
            .execute()
        )
        taken = {r["slug"] for r in existing.data if r.get("slug")}
    except Exception:
        taken = set()

    if base not in taken:
        return base
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"


class TrackingHoursUpdate(BaseModel):
    # Both None => always-on. Both set => live only inside the window.
    tracking_start_time: Optional[time] = Field(
        None, description="Local (Egypt UTC+2) start of live-tracking, e.g. 07:00."
    )
    tracking_end_time: Optional[time] = Field(
        None, description="Local end of live-tracking, e.g. 18:00."
    )


@router.get("/tracking-hours", tags=["tracking (public)"])
def get_tracking_hours(
    current_user: dict = Depends(require_permission("manage_settings")),
):
    """Current org-wide tracking window (caller's own org)."""
    org_id = current_user["org_id"]
    res = (
        supabase.table("organizations")
        .select("tracking_start_time, tracking_end_time")
        .eq("id", org_id)
        .limit(1)
        .execute()
    ).data
    if not res:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found.")
    o = res[0]
    start, end = o.get("tracking_start_time"), o.get("tracking_end_time")
    return {
        "tracking_start_time": start,
        "tracking_end_time": end,
        "mode": "always_on" if (not start or not end) else "windowed",
    }


@router.patch("/tracking-hours", tags=["tracking (public)"])
def set_tracking_hours(
    body: TrackingHoursUpdate,
    current_user: dict = Depends(require_permission("manage_settings")),
):
    """Set the org-wide public-tracking window. Org-scoped: always the caller's
    own org (from token). Pass both times for a window, or neither for always-on.
    """
    org_id = current_user["org_id"]
    start, end = body.tracking_start_time, body.tracking_end_time

    # A window needs BOTH bounds (the tracking logic only enforces when both are
    # set). One-set/one-null is ambiguous -> reject.
    if (start is None) != (end is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide BOTH start and end (a window), or NEITHER (always-on).",
        )
    if start is not None and start == end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tracking_start_time and tracking_end_time must differ.",
        )

    payload = {
        "tracking_start_time": start.isoformat() if start else None,
        "tracking_end_time": end.isoformat() if end else None,
    }
    try:
        result = (
            supabase.table("organizations").update(payload).eq("id", org_id).execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not update tracking hours: {exc}",
        )
    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found.",
        )

    o = result.data[0]
    return {
        "tracking_start_time": o.get("tracking_start_time"),
        "tracking_end_time": o.get("tracking_end_time"),
        "mode": "always_on" if start is None else "windowed",
    }


@router.get("")
def list_organizations(_admin: dict = Depends(require_super_admin)):
    """List ALL organizations on the platform (super-admin only), newest first.

    Counts (profiles/drivers/vehicles) are computed with TWO bulk queries grouped
    in memory — not N+1. Fine at current scale; revisit with a DB aggregate if
    these tables grow large platform-wide.
    """
    orgs = (
        supabase.table("organizations")
        .select(
            "id, name, slug, status, plan, max_devices, monthly_fee, "
            "subscription_expiry, created_at"
        )
        .order("created_at", desc=True)
        .execute()
    ).data

    profiles = supabase.table("profiles").select("org_id, role").execute().data
    vehicles = supabase.table("vehicles").select("org_id").execute().data

    prof_count: dict = defaultdict(int)
    driver_count: dict = defaultdict(int)
    veh_count: dict = defaultdict(int)
    for p in profiles:
        prof_count[p["org_id"]] += 1
        if p.get("role") == "driver":
            driver_count[p["org_id"]] += 1
    for v in vehicles:
        veh_count[v["org_id"]] += 1

    for o in orgs:
        o["counts"] = {
            "profiles": prof_count.get(o["id"], 0),
            "drivers": driver_count.get(o["id"], 0),
            "vehicles": veh_count.get(o["id"], 0),
        }

    return {"count": len(orgs), "organizations": orgs}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_organization(
    body: OrganizationCreate,
    _admin: dict = Depends(require_super_admin),
):
    created_user_id: Optional[str] = None
    created_org_id: Optional[str] = None

    # --- Step a: insert the organization first (we need its id to build the
    #     org-scoped login email before creating the auth user) ---
    try:
        org_slug = _generate_unique_slug(body.slug or body.name)
        org_payload = {
            "name": body.name,
            "slug": org_slug,
            "address": body.address,
            "email": body.email,
            "phone": body.phone,
            "status": "active",
            "plan": body.plan,
            "max_devices": body.max_devices,
            "monthly_fee": body.monthly_fee,
            "subscription_expiry": (
                body.subscription_expiry.isoformat()
                if body.subscription_expiry
                else None
            ),
        }
        org_response = supabase.table("organizations").insert(org_payload).execute()
        created_org_id = org_response.data[0]["id"]
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not create organization: {exc}",
        )

    # Orgs may not have a real email. Supabase Auth requires one, so synthesize
    # an org-scoped login email from the username when none is provided.
    login_email = body.email or synthesize_login_email(body.username, created_org_id)

    # --- Step b: create the auth user (auto-confirmed) ---
    try:
        auth_response = supabase.auth.admin.create_user(
            {
                "email": login_email,
                "password": body.password,
                "email_confirm": True,  # auto-confirm: no verification email needed
            }
        )
        created_user_id = auth_response.user.id
    except Exception as exc:
        _delete_org(created_org_id)  # rollback step a
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not create login account for '{login_email}' (organization was rolled back): {exc}",
        )

    # --- Step c: insert the owner profile linked to the org ---
    try:
        profile_payload = {
            "id": created_user_id,  # matches the auth user id
            "org_id": created_org_id,
            "name": body.name,
            "email": login_email,
            "phone": body.phone,
            "username": body.username,
            "role": "owner",
            "permissions": FULL_PERMISSIONS,
            "is_active": True,
        }
        supabase.table("profiles").insert(profile_payload).execute()
    except Exception as exc:
        _delete_auth_user(created_user_id)  # rollback step b
        _delete_org(created_org_id)  # rollback step a
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not create owner profile (login account and org were rolled back): {exc}",
        )

    # --- Success: return org details + owner login info, never the password ---
    return {
        "status": "ok",
        "message": "Organization created successfully.",
        "organization": org_response.data[0],
        "owner": {
            "id": created_user_id,
            "username": body.username,
            "login": f"{body.username}@{org_slug}",  # what the owner logs in with
            "login_email": login_email,
            "role": "owner",
        },
    }


def _load_org_or_404(org_id: str) -> dict:
    res = (
        supabase.table("organizations").select("*").eq("id", org_id).limit(1).execute()
    )
    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No organization with id '{org_id}'.",
        )
    return res.data[0]


@router.get("/{org_id}")
def get_organization(org_id: str, _admin: dict = Depends(require_super_admin)):
    """Full detail of one org: its fields, profiles, vehicles, and counts."""
    org = _load_org_or_404(org_id)

    profiles = (
        supabase.table("profiles")
        .select("id, name, username, role, is_active")
        .eq("org_id", org_id)
        .order("role", desc=False)
        .execute()
    ).data
    vehicles = (
        supabase.table("vehicles")
        .select("id, bus_number, plate_number, is_active")
        .eq("org_id", org_id)
        .order("created_at", desc=True)
        .execute()
    ).data

    org["counts"] = {
        "profiles": len(profiles),
        "drivers": sum(1 for p in profiles if p.get("role") == "driver"),
        "vehicles": len(vehicles),
    }
    org["profiles"] = profiles
    org["vehicles"] = vehicles
    return org


@router.patch("/{org_id}")
def update_organization(
    org_id: str,
    body: OrgUpdate,
    _admin: dict = Depends(require_super_admin),
):
    """Edit subscription/contact fields. Only provided fields are updated."""
    fields = body.model_dump(exclude_unset=True)
    if "subscription_expiry" in fields and fields["subscription_expiry"] is not None:
        fields["subscription_expiry"] = fields["subscription_expiry"].isoformat()

    if not fields:
        return _load_org_or_404(org_id)

    try:
        res = (
            supabase.table("organizations").update(fields).eq("id", org_id).execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not update organization: {exc}",
        )
    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No organization with id '{org_id}'.",
        )
    return res.data[0]


@router.patch("/{org_id}/status")
def set_organization_status(
    org_id: str,
    body: OrgStatusUpdate,
    _admin: dict = Depends(require_super_admin),
):
    """Freeze/unfreeze an org: status = active | suspended | expired."""
    res = (
        supabase.table("organizations")
        .update({"status": body.status})
        .eq("id", org_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No organization with id '{org_id}'.",
        )
    return res.data[0]


# Tables holding the org's data, deleted CHILDREN-FIRST so it's safe regardless
# of FK cascade config. route_stops has no org_id (cleaned via route ids).
_ORG_CHILD_TABLES_BY_ORG = [
    "alerts", "stop_events", "location_pings", "trips",
    "assignments", "alert_rules", "payments",
]
_ORG_OWNED_TABLES = ["routes", "vehicles", "profiles"]


@router.delete("/{org_id}")
def delete_organization(
    org_id: str,
    admin: dict = Depends(require_super_admin),
):
    """PERMANENTLY delete an org and ALL its data, including its users' Supabase
    Auth accounts. Irreversible. Children are removed before parents, and auth
    users are deleted explicitly (DB cascade can't touch Supabase Auth)."""
    org = _load_org_or_404(org_id)

    profile_ids = [
        p["id"] for p in supabase.table("profiles").select("id").eq("org_id", org_id).execute().data
    ]
    route_ids = [
        r["id"] for r in supabase.table("routes").select("id").eq("org_id", org_id).execute().data
    ]

    deleted: dict = {}
    for table in _ORG_CHILD_TABLES_BY_ORG:
        res = supabase.table(table).delete().eq("org_id", org_id).execute()
        deleted[table] = len(res.data or [])
    if route_ids:
        rs = supabase.table("route_stops").delete().in_("route_id", route_ids).execute()
        deleted["route_stops"] = len(rs.data or [])
    for table in _ORG_OWNED_TABLES:
        res = supabase.table(table).delete().eq("org_id", org_id).execute()
        deleted[table] = len(res.data or [])

    # Supabase Auth accounts are a separate system — delete them explicitly so
    # we never leave orphaned logins.
    auth_deleted = 0
    for pid in profile_ids:
        try:
            supabase.auth.admin.delete_user(pid)
            auth_deleted += 1
        except Exception:
            pass
    deleted["auth_users"] = auth_deleted

    supabase.table("organizations").delete().eq("id", org_id).execute()

    # Audit with org_id NULL (the org no longer exists); identity kept in meta.
    try:
        supabase.table("audit_log").insert(
            {
                "actor_id": admin["id"],
                "action": "organization.delete",
                "org_id": None,
                "target": org_id,
                "meta": {"org_name": org["name"], "deleted": deleted},
            }
        ).execute()
    except Exception:
        pass

    return {
        "status": "deleted",
        "organization": {"id": org_id, "name": org["name"]},
        "deleted": deleted,
    }


@router.post("/{org_id}/impersonate")
def impersonate_organization(
    org_id: str,
    admin: dict = Depends(require_super_admin),
):
    """Mint a short-lived token that lets the super admin act AS this org's owner
    in the manager dashboard. Every issuance is written to audit_log."""
    org = _load_org_or_404(org_id)

    owners = (
        supabase.table("profiles")
        .select("id, name")
        .eq("org_id", org_id)
        .eq("role", "owner")
        .limit(1)
        .execute()
    ).data
    if not owners:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This organization has no owner account to impersonate.",
        )
    owner = owners[0]

    minted = mint_impersonation_token(owner["id"], org_id, admin["id"])

    # Audit while the org still exists (org_id is valid here).
    try:
        supabase.table("audit_log").insert(
            {
                "actor_id": admin["id"],
                "action": "organization.impersonate",
                "org_id": org_id,
                "target": owner["id"],
                "meta": {"org_name": org["name"], "owner_name": owner["name"]},
            }
        ).execute()
    except Exception:
        pass

    return {
        "access_token": minted["token"],
        "token_type": "bearer",
        "expires_in": minted["expires_in"],
        "impersonation": {
            "org_id": org_id,
            "org_name": org["name"],
            "org_slug": org["slug"],
            "owner_name": owner["name"],
            "by": admin["name"],
        },
    }
