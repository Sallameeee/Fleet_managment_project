"""Authentication routes — public login endpoint.

Users log in with an ORG-SCOPED handle: `username@org-slug` (e.g.
`john_d@acme-bus`) plus a password. The username is only unique within an
org, so the org slug disambiguates it. We resolve (org slug + username) to
the user's profile, read its stored login email, then run Supabase's
password grant.
"""

import os

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from auth import get_current_user, require_super_admin
from database import SUPABASE_URL, supabase

router = APIRouter(prefix="/auth", tags=["auth"])

SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")

# Reused for every auth failure so we never reveal which part was wrong.
INVALID_CREDENTIALS = "Invalid username or password"
# Super-admin login: identical message for bad password AND not-a-super-admin,
# so the panel never reveals that an account exists or is merely non-admin.
INVALID_ADMIN_CREDENTIALS = "Invalid email or password"


class LoginRequest(BaseModel):
    login: str = Field(
        ...,
        min_length=3,
        description="Org-scoped handle in the form username@org-slug, e.g. john_d@acme-bus",
    )
    password: str = Field(..., min_length=1)


def _split_handle(handle: str):
    """Split `username@org-slug` on the LAST '@'. Returns (username, slug)."""
    username, sep, slug = handle.rpartition("@")
    return username.strip(), slug.strip().lower(), sep


@router.post("/login")
def login(body: LoginRequest):
    username, org_slug, sep = _split_handle(body.login)
    if not sep or not username or not org_slug:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Login must be in the form username@org-slug (e.g. john_d@acme-bus).",
        )

    # Resolve the org by slug, then the profile by (org_id, username).
    try:
        org_result = (
            supabase.table("organizations")
            .select("id, status")
            .eq("slug", org_slug)
            .limit(1)
            .execute()
        )
    except Exception:
        org_result = None

    if not org_result or not org_result.data:
        import logging  # TEMP DIAGNOSTIC — remove after debugging
        logging.getLogger("auth").warning("LOGIN DIAG step=org-not-found slug=%s", org_slug)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_CREDENTIALS)
    org_id = org_result.data[0]["id"]
    org_status = org_result.data[0].get("status")

    try:
        prof_result = (
            supabase.table("profiles")
            .select("id, name, role, org_id, permissions, is_active, email")
            .eq("org_id", org_id)
            .eq("username", username)
            .limit(1)
            .execute()
        )
    except Exception:
        prof_result = None

    if not prof_result or not prof_result.data:
        import logging  # TEMP DIAGNOSTIC — remove after debugging
        logging.getLogger("auth").warning("LOGIN DIAG step=profile-not-found org=%s username=%s", org_id, username)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_CREDENTIALS)
    profile = prof_result.data[0]

    login_email = profile.get("email")
    if not login_email:
        # No stored login email — cannot authenticate this account.
        import logging  # TEMP DIAGNOSTIC — remove after debugging
        logging.getLogger("auth").warning("LOGIN DIAG step=no-login-email profile_id=%s", profile.get("id"))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_CREDENTIALS)

    # Supabase password grant, using the public anon key.
    try:
        resp = httpx.post(
            f"{SUPABASE_URL}/auth/v1/token",
            params={"grant_type": "password"},
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"email": login_email, "password": body.password},
            timeout=15,
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is temporarily unavailable. Please try again.",
        )

    if resp.status_code != 200:
        # TEMP DIAGNOSTIC — remove after debugging. Logs what Supabase returned
        # (status + first 150 chars of body), the project host we called, and the
        # anon-key LENGTH (length only — not the key). No secrets.
        import logging
        from urllib.parse import urlparse
        logging.getLogger("auth").warning(
            "LOGIN DIAG step=grant status=%s project=%s anon_key_len=%s body=%s",
            resp.status_code,
            urlparse(SUPABASE_URL).netloc,
            len(SUPABASE_ANON_KEY or ""),
            resp.text[:150],
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_CREDENTIALS)

    # Credentials valid — block deactivated accounts.
    if profile.get("is_active") is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated. Contact your administrator.",
        )

    # Whole-tenant freeze: a suspended/expired org blocks ALL its users. Checked
    # AFTER credential verification so it isn't a pre-auth enumeration oracle.
    if org_status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This organization is suspended — please contact support.",
        )

    data = resp.json()
    return {
        "access_token": data.get("access_token"),
        "token_type": data.get("token_type", "bearer"),
        "expires_in": data.get("expires_in"),
        "user": {
            "id": profile.get("id"),
            "name": profile.get("name"),
            "role": profile.get("role"),
            "org_id": profile.get("org_id"),
            "permissions": profile.get("permissions"),
        },
    }


@router.get("/me")
def me(current_user: dict = Depends(get_current_user)):
    """Return the current user's profile, resolved from their Bearer token.

    Lets the frontend rehydrate session/UI without re-logging in.
    """
    return {
        "id": current_user.get("id"),
        "name": current_user.get("name"),
        "username": current_user.get("username"),
        "email": current_user.get("email"),
        "phone": current_user.get("phone"),
        "role": current_user.get("role"),
        "org_id": current_user.get("org_id"),
        "permissions": current_user.get("permissions"),
        "is_active": current_user.get("is_active"),
        "created_at": current_user.get("created_at"),
        # Passenger app reads this to force a reset before anything else.
        "must_change_password": current_user.get("must_change_password", False),
        # Present (and truthy) only when this is an impersonated session.
        "impersonation": current_user.get("impersonation"),
    }


# --- Passenger auth (students on the passenger app) ---------------------------

class PassengerLoginRequest(BaseModel):
    email: str = Field(..., min_length=3, description="Passenger's email (their login).")
    password: str = Field(..., min_length=1)


@router.post("/passenger/login", tags=["passenger"])
def passenger_login(body: PassengerLoginRequest):
    """Authenticate a PASSENGER by email + password (they log in with their real
    email). Verifies the account is a passenger profile, then returns the token
    plus `must_change_password` so the app can force a first-login reset."""
    try:
        resp = httpx.post(
            f"{SUPABASE_URL}/auth/v1/token",
            params={"grant_type": "password"},
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"email": body.email, "password": body.password},
            timeout=15,
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is temporarily unavailable. Please try again.",
        )
    if resp.status_code != 200:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_CREDENTIALS)

    data = resp.json()
    uid = (data.get("user") or {}).get("id")
    if not uid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_CREDENTIALS)

    prof = (
        supabase.table("profiles")
        .select("id, name, role, org_id, is_active, must_change_password")
        .eq("id", uid)
        .limit(1)
        .execute()
    )
    # Same generic 401 if not found OR not a passenger — no account-type oracle.
    if not prof.data or prof.data[0].get("role") != "passenger":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_CREDENTIALS)
    profile = prof.data[0]
    if profile.get("is_active") is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated. Contact your administrator.",
        )
    return {
        "access_token": data.get("access_token"),
        "token_type": data.get("token_type", "bearer"),
        "expires_in": data.get("expires_in"),
        "user": {"id": profile["id"], "name": profile.get("name"), "role": "passenger", "org_id": profile.get("org_id")},
        "must_change_password": bool(profile.get("must_change_password")),
    }


class ChangePasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=6)


@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    current_user: dict = Depends(get_current_user),
):
    """Set a new password for the CURRENT user and clear must_change_password.
    Used by the passenger app to complete the forced first-login reset (works
    for any authenticated user)."""
    try:
        supabase.auth.admin.update_user_by_id(current_user["id"], {"password": body.new_password})
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not change password: {exc}")
    supabase.table("profiles").update({"must_change_password": False}).eq("id", current_user["id"]).execute()
    return {"ok": True}


# --- Super-admin auth (platform operators; separate from org users) -----------

class SuperAdminLoginRequest(BaseModel):
    email: str = Field(..., min_length=3, description="Super admin's account email.")
    password: str = Field(..., min_length=1)


@router.post("/super-admin/login", tags=["super-admin"])
def super_admin_login(body: SuperAdminLoginRequest):
    """Authenticate a PLATFORM super admin by email + password.

    Unlike /auth/login (org users), this authenticates the email directly
    against Supabase Auth, then verifies the user is in `super_admins`. Any
    failure — wrong password OR a valid account that isn't a super admin —
    returns the SAME generic 401, so the panel never reveals account existence
    or admin status.
    """
    # Step 1: password grant against Supabase Auth, using the email directly.
    try:
        resp = httpx.post(
            f"{SUPABASE_URL}/auth/v1/token",
            params={"grant_type": "password"},
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"email": body.email, "password": body.password},
            timeout=15,
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is temporarily unavailable. Please try again.",
        )

    if resp.status_code != 200:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_ADMIN_CREDENTIALS)

    data = resp.json()
    auth_user = data.get("user") or {}
    user_id = auth_user.get("id")
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_ADMIN_CREDENTIALS)

    # Step 2: verify the authenticated user is a super admin (same check as
    # require_super_admin). Not in the table -> SAME generic 401.
    try:
        sa = (
            supabase.table("super_admins").select("id, name, email").eq("id", user_id).limit(1).execute()
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not verify super admin status.",
        )
    if not sa.data:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_ADMIN_CREDENTIALS)

    admin = sa.data[0]
    return {
        "access_token": data.get("access_token"),
        "token_type": data.get("token_type", "bearer"),
        "expires_in": data.get("expires_in"),
        "admin": {"id": admin["id"], "name": admin["name"], "email": admin["email"]},
    }


@router.get("/super-admin/me", tags=["super-admin"])
def super_admin_me(admin: dict = Depends(require_super_admin)):
    """Return the current super admin, resolved from their Bearer token.

    Lets the dashboard verify a stored token is still a valid super-admin token
    (server-checked guard) rather than trusting localStorage.
    """
    return {
        "id": admin.get("id"),
        "name": admin.get("name"),
        "email": admin.get("email"),
        "permissions": admin.get("permissions") or {},
        "is_active": admin.get("is_active", True),
    }
