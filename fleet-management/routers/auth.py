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
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import branding
import features as feature_flags
from auth import get_current_user, require_super_admin
from database import SUPABASE_URL, retry_transient, supabase

router = APIRouter(prefix="/auth", tags=["auth"])

SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")

# Reused for every auth failure so we never reveal which part was wrong.
INVALID_CREDENTIALS = "Invalid username or password"
# Super-admin login: identical message for bad password AND not-a-super-admin,
# so the panel never reveals that an account exists or is merely non-admin.
INVALID_ADMIN_CREDENTIALS = "Invalid email or password"
AUTH_UNAVAILABLE = "Authentication service is temporarily unavailable. Please try again."


def _upstream_down(resp) -> bool:
    """Supabase Auth answered 5xx or rate-limited us. That says nothing about the
    user's password or session, so it must never surface as a 401."""
    return resp.status_code >= 500 or resp.status_code == 429


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
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_CREDENTIALS)
    profile = prof_result.data[0]

    login_email = profile.get("email")
    if not login_email:
        # No stored login email — cannot authenticate this account.
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

    if _upstream_down(resp):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, AUTH_UNAVAILABLE)
    if resp.status_code != 200:
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
    # Org module + enabled features so the app (driver/supervisor) can gate UI.
    org_module, enabled_features = feature_flags.module_and_enabled(profile.get("org_id"))
    return {
        "access_token": data.get("access_token"),
        # The refresh token lets the client RENEW an expired access token silently
        # (POST /auth/refresh) so a session never has to drop the user.
        "refresh_token": data.get("refresh_token"),
        "token_type": data.get("token_type", "bearer"),
        "expires_in": data.get("expires_in"),
        "user": {
            "id": profile.get("id"),
            "name": profile.get("name"),
            "role": profile.get("role"),
            "org_id": profile.get("org_id"),
            "module": org_module,
            "enabled_features": enabled_features,
            "permissions": profile.get("permissions"),
            **branding.org_branding(profile.get("org_id")),
        },
        "module": org_module,
        "enabled_features": enabled_features,
    }


@router.get("/me")
def me(current_user: dict = Depends(get_current_user)):
    """Return the current user's profile, resolved from their Bearer token.

    Lets the frontend rehydrate session/UI without re-logging in.
    """
    # Surface the org's feature module so the dashboard can relabel/gate features
    # (e.g. "Drivers" → "Supervisors" in school orgs). Best-effort; defaults to
    # 'university' so behaviour is unchanged if the lookup fails or is unset.
    org_module, enabled_features = "university", []
    _oid = current_user.get("org_id")
    if _oid:
        org_module, enabled_features = feature_flags.module_and_enabled(_oid)

    return {
        "id": current_user.get("id"),
        "name": current_user.get("name"),
        "username": current_user.get("username"),
        "email": current_user.get("email"),
        "phone": current_user.get("phone"),
        "role": current_user.get("role"),
        "org_id": current_user.get("org_id"),
        "module": org_module,
        # The org's ENABLED features (core + toggleable) so the dashboard/app gate UI.
        "enabled_features": enabled_features,
        "permissions": current_user.get("permissions"),
        "is_active": current_user.get("is_active"),
        "created_at": current_user.get("created_at"),
        # Passenger app reads this to force a reset before anything else.
        "must_change_password": current_user.get("must_change_password", False),
        # Present (and truthy) only when this is an impersonated session.
        "impersonation": current_user.get("impersonation"),
        # Staff ID details for the app profile card (driver: licence; school
        # supervisor: national ID). Read directly: get_current_user may not carry them.
        **_id_details(current_user),
        # Branding: org display name, logo URL (or null) and transport-office phone.
        **branding.org_branding(_oid),
    }


def _id_details(current_user: dict) -> dict:
    out = {"license_number": None, "license_expiry_date": None, "national_id": None}
    uid = current_user.get("id")
    if not uid or current_user.get("role") in ("passenger", None):
        return out
    for cols in ("license_number, license_expiry_date, national_id", "license_number, license_expiry_date"):
        try:
            r = supabase.table("profiles").select(cols).eq("id", uid).limit(1).execute().data
            if r:
                out.update({k: r[0].get(k) for k in out if k in r[0]})
            return out
        except Exception:
            continue  # national_id arrives with migration 035
    return out


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
    if _upstream_down(resp):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, AUTH_UNAVAILABLE)
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
    # Surface the org's module so the passenger app knows which view to show:
    # 'school' -> the account is a PARENT (tracks children); 'university' -> a
    # STUDENT (tracks themselves). Defaults to 'university'.
    org_module, enabled_features = feature_flags.module_and_enabled(profile.get("org_id"))
    return {
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token"),  # for silent renewal (POST /auth/refresh)
        "token_type": data.get("token_type", "bearer"),
        "expires_in": data.get("expires_in"),
        "user": {"id": profile["id"], "name": profile.get("name"), "role": "passenger", "org_id": profile.get("org_id"),
                 **branding.org_branding(profile.get("org_id"))},
        "module": org_module,
        "enabled_features": enabled_features,
        "must_change_password": bool(profile.get("must_change_password")),
    }


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


@router.post("/refresh", tags=["auth"])
def refresh_token(body: RefreshRequest):
    """Exchange a refresh token for a FRESH access token (+ a rotated refresh
    token). This is what keeps a session alive indefinitely: when the short-lived
    access token expires, the client calls this instead of ever forcing a
    re-login. Works for every org user AND passenger (Supabase issues one refresh
    token per session regardless of login route).

    Supabase rotates refresh tokens: each successful refresh returns a NEW refresh
    token and invalidates the one just used, so the client MUST store the returned
    refresh token. A blocked account is cut off here too (403) so a deactivated
    user can't keep renewing.

    Every answer tells the client exactly what to do, so nothing transient can
    ever end a session:
      200  new tokens. Store the NEW refresh token before using the access token.
      401  {"code": ...}: this refresh token cannot be used.
             refresh_token_already_used: another holder of the same session (the
               app's other isolate, another browser tab) rotated it first. Re-read
               the stored token and retry; never sign out.
             anything else (refresh_token_not_found, session_not_found, ...): the
               session is gone. Ask for the password again; keep all local data.
      403  the account is deactivated: the one case a client signs out.
      503  Supabase unreachable, 5xx or rate-limited: keep the session, retry later.
    """
    try:
        # Safe to retry: re-sending a token whose rotation answer got lost returns
        # the session's current token (Supabase accepts the active token's parent).
        resp = retry_transient(
            lambda: httpx.post(
                f"{SUPABASE_URL}/auth/v1/token",
                params={"grant_type": "refresh_token"},
                headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
                json={"refresh_token": body.refresh_token},
                timeout=httpx.Timeout(9.0, connect=5.0),
            ),
            tries=2,
        )
    except Exception:
        return _refresh_unavailable()
    if _upstream_down(resp):
        return _refresh_unavailable("auth_rate_limited" if resp.status_code == 429 else "auth_unavailable")
    if resp.status_code != 200:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Could not refresh session.", "code": _gotrue_code(resp) or "invalid_refresh_token"},
        )
    try:
        data = resp.json()
    except Exception:
        return _refresh_unavailable()
    uid = (data.get("user") or {}).get("id")
    # Cut off a deactivated account at refresh time (so it can't renew forever).
    if uid and _account_deactivated(uid):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated. Contact your administrator.",
        )
    return {
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token"),
        "token_type": data.get("token_type", "bearer"),
        "expires_in": data.get("expires_in"),
        "expires_at": data.get("expires_at"),
    }


def _refresh_unavailable(code: str = "auth_unavailable") -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": AUTH_UNAVAILABLE, "code": code},
        headers={"Retry-After": "5"},
    )


def _gotrue_code(resp) -> str:
    """Supabase Auth's machine-readable error code, e.g. refresh_token_already_used."""
    try:
        b = resp.json()
    except Exception:
        return ""
    if not isinstance(b, dict):
        return ""
    code = b.get("error_code")
    if isinstance(code, str) and code:
        return code
    # Older Auth versions: {"error": "invalid_grant", "error_description": "Invalid Refresh Token: Already Used"}
    if "already used" in str(b.get("error_description") or b.get("msg") or "").lower():
        return "refresh_token_already_used"
    err = b.get("error")
    return err if isinstance(err, str) else ""


def _account_deactivated(uid: str) -> bool:
    """True only when the account is KNOWN to be deactivated (org user or super
    admin). A lookup failure answers False: Supabase has already rotated the token,
    so the new pair must reach the client, and every API call re-checks is_active."""
    try:
        prof = retry_transient(lambda: supabase.table("profiles").select("is_active").eq("id", uid).limit(1).execute().data)
        if prof:
            return prof[0].get("is_active") is False
        sa = retry_transient(lambda: supabase.table("super_admins").select("is_active").eq("id", uid).limit(1).execute().data)
        return bool(sa) and sa[0].get("is_active") is False
    except Exception:
        return False


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

    if _upstream_down(resp):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, AUTH_UNAVAILABLE)
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
        # Lets the panel renew silently via POST /auth/refresh. Without it every
        # super-admin session ended when the 1-hour access token expired.
        "refresh_token": data.get("refresh_token"),
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
