"""Authorization dependencies.

Building blocks:
  * `bearer_scheme`        - HTTPBearer security scheme (Swagger padlock).
  * `get_current_user`     - authenticate the token and load the caller's
                             profile row (org_id, role, permissions, ...).
  * `require_super_admin`  - allow only platform super admins.
  * `require_permission`   - factory: allow only org users who hold a given
                             permission flag (owners always pass).

All raise 401 for a missing/invalid token and 403 when the caller is
authenticated but not allowed.
"""

import os
from datetime import datetime, timezone
from types import SimpleNamespace

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from database import supabase

# auto_error=False so we can return our own clear 401 when the header is
# missing, instead of FastAPI's generic "Not authenticated" response.
bearer_scheme = HTTPBearer(auto_error=False)

# --- Impersonation tokens ----------------------------------------------------
# Super admins can mint a short-lived token that lets them ACT AS an org's owner.
# It is our OWN HS256 JWT (separate from Supabase tokens), signed server-side, so
# it cannot be forged and never grants super-admin access (its subject is the
# org owner, not a super admin).
_IMP_SECRET = os.getenv("IMPERSONATION_SECRET") or os.getenv("SUPABASE_SERVICE_KEY") or "dev-only"
_IMP_ALG = "HS256"
_IMP_TTL_SECONDS = 30 * 60  # 30 minutes


def mint_impersonation_token(owner_id: str, org_id: str, actor_id: str) -> dict:
    """Sign an impersonation token for `owner_id` on behalf of super admin
    `actor_id`. Returns {token, expires_in}."""
    now = int(datetime.now(timezone.utc).timestamp())
    payload = {
        "sub": owner_id,  # the org owner we impersonate
        "org_id": org_id,
        "act": actor_id,  # the super admin doing it (for /me + audit)
        "imp": True,
        "iat": now,
        "exp": now + _IMP_TTL_SECONDS,
    }
    return {
        "token": jwt.encode(payload, _IMP_SECRET, algorithm=_IMP_ALG),
        "expires_in": _IMP_TTL_SECONDS,
    }


def _try_impersonation_token(token: str):
    """Return the decoded payload if `token` is a valid impersonation JWT, else
    None (so the caller falls back to normal Supabase verification)."""
    try:
        payload = jwt.decode(token, _IMP_SECRET, algorithms=[_IMP_ALG])
    except Exception:
        return None
    return payload if payload.get("imp") is True else None


def _verify_token(credentials: HTTPAuthorizationCredentials):
    """Validate the Bearer token and return the auth user.

    Tries our impersonation JWT first (cheap, local). If that doesn't apply,
    falls back to Supabase Auth verification — so genuine tokens are unaffected.
    Raises 401 if the token is missing, malformed, expired, or invalid.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer token. Provide an Authorization: Bearer <token> header.",
        )

    imp = _try_impersonation_token(credentials.credentials)
    if imp:
        # Synthetic user whose id is the impersonated owner. The impersonation
        # context rides along so get_current_user can surface it.
        return SimpleNamespace(
            id=imp["sub"],
            is_impersonation=True,
            impersonation={"by": imp.get("act"), "org_id": imp.get("org_id")},
        )

    try:
        user_response = supabase.auth.get_user(credentials.credentials)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
        )

    user = getattr(user_response, "user", None)
    if user is None or not getattr(user, "id", None):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token could not be verified.",
        )
    return user


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """Authenticate the caller and return their `profiles` row.

    Use this for any endpoint that needs to know *who* is calling (org_id,
    role, permissions). Raises 403 if the account has no profile or is
    deactivated.
    """
    user = _verify_token(credentials)

    try:
        result = (
            supabase.table("profiles").select("*").eq("id", user.id).limit(1).execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not load user profile: {exc}",
        )

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No profile is associated with this account.",
        )

    profile = result.data[0]
    if profile.get("is_active") is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated. Contact your administrator.",
        )
    # Surface impersonation context so /auth/me can tell the manager dashboard
    # it's an impersonated session (and show the banner).
    if getattr(user, "is_impersonation", False):
        profile["impersonation"] = user.impersonation
    return profile


def require_super_admin(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """Allow only platform super admins (a row in `super_admins`).

    An impersonation token's subject is an org owner, NOT a super admin, so it
    can never pass this — impersonation cannot reach super-admin endpoints.
    """
    user = _verify_token(credentials)

    try:
        result = (
            supabase.table("super_admins").select("*").eq("id", user.id).execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not verify super admin status: {exc}",
        )

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required. Your account is not authorized for this action.",
        )
    admin = result.data[0]
    if admin.get("is_active") is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This platform account has been deactivated.",
        )
    return admin


def require_super_permission(permission: str):
    """Build a dependency that requires a super admin to hold `permission`.

    Platform staff carry a `permissions` JSON on their super_admins row. The
    `view_all` flag is the root/owner bypass (holds every permission).
    """

    def dependency(admin: dict = Depends(require_super_admin)) -> dict:
        perms = admin.get("permissions") or {}
        if perms.get("view_all") is True or perms.get(permission) is True:
            return admin
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Your platform account lacks the '{permission}' permission.",
        )

    return dependency


def require_role(*roles: str):
    """Build a dependency that requires the caller's role to be one of `roles`.

    For endpoints gated by WHO the caller is rather than a permission flag —
    e.g. driver-only actions (starting/ending their own trip). Drivers carry an
    empty `permissions` map, so `require_permission` can't express this.

    Usage:
        def handler(current_user: dict = Depends(require_role("driver"))): ...

    Returns the caller's profile so the endpoint can use id, org_id, etc.
    """

    allowed = set(roles)

    def dependency(current_user: dict = Depends(get_current_user)) -> dict:
        if current_user.get("role") in allowed:
            return current_user

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This action requires one of these roles: {', '.join(sorted(allowed))}.",
        )

    return dependency


def require_permission(permission: str):
    """Build a dependency that requires the caller to hold `permission`.

    Usage:
        @router.post("/drivers", dependencies=[Depends(require_permission("manage_drivers"))])
        # or, to also use the caller's profile in the handler:
        def handler(current_user: dict = Depends(require_permission("manage_drivers"))): ...

    Rules:
      * Org owners always pass (full control of their own tenant).
      * Otherwise the caller's `permissions` JSON must have the flag set true.
    Returns the caller's profile so the endpoint can use org_id, etc.
    """

    def dependency(current_user: dict = Depends(get_current_user)) -> dict:
        role = current_user.get("role")
        permissions = current_user.get("permissions") or {}

        if role == "owner" or permissions.get(permission) is True:
            return current_user

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You do not have permission to perform this action ({permission}).",
        )

    return dependency
