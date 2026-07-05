"""Passenger (student) management — manager side, org-scoped.

A passenger is a `profiles` row (role='passenger') + a `passengers` detail row
(university_id + their route). Creating one AUTO-PROVISIONS a Supabase Auth
login using the student's email, with a shared default password and the
`must_change_password` flag set — the passenger app forces a reset on first
login (see routers/auth.py: /auth/passenger/login + /auth/change-password).
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from auth import require_permission
from database import supabase

router = APIRouter(prefix="/passengers", tags=["passengers"])

# ---------------------------------------------------------------------------
# SECURITY TRADEOFF (intentional, temporary): every auto-created passenger gets
# this SAME default password. It is safe ONLY because must_change_password is
# set, so the passenger app forces a reset before any data access. To harden,
# swap this single constant for a per-passenger random password (e.g.
# secrets.token_urlsafe(9)) and surface/email it once — the rest of the flow
# (forced change) stays the same.
DEFAULT_PASSENGER_PASSWORD = "123456"
# ---------------------------------------------------------------------------


class PassengerCreate(BaseModel):
    name: str = Field(..., min_length=1)
    email: str = Field(..., min_length=3)
    university_id: Optional[str] = None
    route_id: str = Field(..., min_length=1)


class PassengerUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    university_id: Optional[str] = None
    route_id: Optional[str] = None
    is_active: Optional[bool] = None


class BulkRow(BaseModel):
    name: str = Field(..., min_length=1)
    email: str = Field(..., min_length=3)
    university_id: Optional[str] = None
    route: str = Field(..., min_length=1)  # route id OR route name


class BulkRequest(BaseModel):
    rows: List[BulkRow]


def _delete_auth_user(user_id: str) -> None:
    try:
        supabase.auth.admin.delete_user(user_id)
    except Exception:
        pass


def _resolve_route(org_id: str, value: str) -> Optional[str]:
    """Resolve a route by id first, then by name, within the org. Returns the
    route id or None."""
    by_id = supabase.table("routes").select("id").eq("id", value).eq("org_id", org_id).limit(1).execute()
    if by_id.data:
        return by_id.data[0]["id"]
    by_name = supabase.table("routes").select("id").eq("name", value).eq("org_id", org_id).limit(1).execute()
    return by_name.data[0]["id"] if by_name.data else None


def _create_passenger(org_id: str, name: str, email: str, university_id: Optional[str], route_id: str) -> dict:
    """Create the auth account + profile + passengers row. Raises HTTPException
    on failure (with auth-account rollback)."""
    created_id: Optional[str] = None
    try:
        auth = supabase.auth.admin.create_user(
            {"email": email, "password": DEFAULT_PASSENGER_PASSWORD, "email_confirm": True}
        )
        created_id = auth.user.id
    except Exception as exc:
        text = str(exc).lower()
        if "already" in text and ("registered" in text or "exist" in text):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"A login for '{email}' already exists.")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not create login: {exc}")

    try:
        prof = (
            supabase.table("profiles")
            .insert(
                {
                    "id": created_id,
                    "org_id": org_id,
                    "name": name,
                    "email": email,
                    "username": email,  # passengers log in by email; username kept unique per org
                    "role": "passenger",
                    "permissions": {},  # passengers hold NO management permissions
                    "is_active": True,
                    "must_change_password": True,  # forces first-login reset
                }
            )
            .execute()
        ).data[0]
    except Exception as exc:
        _delete_auth_user(created_id)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not create passenger profile: {exc}")

    try:
        supabase.table("passengers").insert(
            {"id": created_id, "org_id": org_id, "university_id": university_id, "route_id": route_id}
        ).execute()
    except Exception as exc:
        supabase.table("profiles").delete().eq("id", created_id).execute()
        _delete_auth_user(created_id)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not create passenger record: {exc}")

    return prof


@router.post("", status_code=status.HTTP_201_CREATED)
def create_passenger(
    body: PassengerCreate,
    current_user: dict = Depends(require_permission("manage_passengers")),
):
    org_id = current_user["org_id"]
    route_id = _resolve_route(org_id, body.route_id)
    if not route_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such route in your organization.")
    _create_passenger(org_id, body.name.strip(), body.email.strip(), body.university_id, route_id)
    return {
        "email": body.email.strip(),
        "default_password": DEFAULT_PASSENGER_PASSWORD,
        "must_change_password": True,
    }


@router.get("")
def list_passengers(current_user: dict = Depends(require_permission("manage_passengers"))):
    org_id = current_user["org_id"]
    rows = (
        supabase.table("passengers").select("id, university_id, route_id, created_at").eq("org_id", org_id).execute().data
    )
    ids = [r["id"] for r in rows]
    profs, routes = {}, {}
    if ids:
        profs = {
            p["id"]: p
            for p in supabase.table("profiles").select("id, name, email, is_active").in_("id", ids).execute().data
        }
    route_ids = list({r["route_id"] for r in rows if r.get("route_id")})
    if route_ids:
        routes = {r["id"]: r["name"] for r in supabase.table("routes").select("id, name").in_("id", route_ids).execute().data}
    out = []
    for r in rows:
        p = profs.get(r["id"], {})
        out.append(
            {
                "id": r["id"],
                "name": p.get("name"),
                "email": p.get("email"),
                "is_active": p.get("is_active", True),
                "university_id": r.get("university_id"),
                "route_id": r.get("route_id"),
                "route_name": routes.get(r.get("route_id")),
            }
        )
    out.sort(key=lambda x: (x["name"] or "").lower())
    return {"count": len(out), "passengers": out}


@router.patch("/{passenger_id}")
def update_passenger(
    passenger_id: str,
    body: PassengerUpdate,
    current_user: dict = Depends(require_permission("manage_passengers")),
):
    org_id = current_user["org_id"]
    existing = supabase.table("passengers").select("id").eq("id", passenger_id).eq("org_id", org_id).limit(1).execute()
    if not existing.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Passenger not found.")

    prof_update: dict = {}
    if body.name is not None:
        prof_update["name"] = body.name
    if body.is_active is not None:
        prof_update["is_active"] = body.is_active
    if prof_update:
        supabase.table("profiles").update(prof_update).eq("id", passenger_id).eq("org_id", org_id).execute()

    pax_update: dict = {}
    if "university_id" in body.model_fields_set:
        pax_update["university_id"] = body.university_id
    if body.route_id is not None:
        rid = _resolve_route(org_id, body.route_id)
        if not rid:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such route in your organization.")
        pax_update["route_id"] = rid
    if pax_update:
        supabase.table("passengers").update(pax_update).eq("id", passenger_id).eq("org_id", org_id).execute()

    return {"id": passenger_id}


@router.delete("/{passenger_id}", status_code=status.HTTP_200_OK)
def delete_passenger(
    passenger_id: str,
    current_user: dict = Depends(require_permission("manage_passengers")),
):
    org_id = current_user["org_id"]
    existing = supabase.table("passengers").select("id").eq("id", passenger_id).eq("org_id", org_id).limit(1).execute()
    if not existing.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Passenger not found.")
    # passengers row cascades from the profile delete; then remove the auth login.
    supabase.table("passengers").delete().eq("id", passenger_id).eq("org_id", org_id).execute()
    supabase.table("profiles").delete().eq("id", passenger_id).eq("org_id", org_id).execute()
    _delete_auth_user(passenger_id)
    return {"deleted": passenger_id}


@router.post("/bulk")
def bulk_create(
    body: BulkRequest,
    current_user: dict = Depends(require_permission("manage_passengers")),
):
    """Create many passengers from parsed rows. Each row is attempted
    independently; per-row failures (duplicate email, unknown route) are
    reported so the manager sees exactly which rows succeeded/failed."""
    org_id = current_user["org_id"]
    created = 0
    errors = []
    for i, row in enumerate(body.rows):
        try:
            route_id = _resolve_route(org_id, row.route.strip())
            if not route_id:
                raise HTTPException(status_code=400, detail=f"Unknown route '{row.route}'.")
            _create_passenger(org_id, row.name.strip(), row.email.strip(), row.university_id, route_id)
            created += 1
        except HTTPException as exc:
            errors.append({"row": i + 2, "email": row.email, "error": str(exc.detail)})  # +2: header + 1-indexed
        except Exception as exc:
            errors.append({"row": i + 2, "email": row.email, "error": str(exc)})
    return {"created": created, "failed": len(errors), "errors": errors}
