"""Passenger (student) management — manager side, org-scoped.

A passenger is a `profiles` row (role='passenger') + a `passengers` detail row
(university_id + their route). Creating one AUTO-PROVISIONS a Supabase Auth
login using the student's email, with a shared default password and the
`must_change_password` flag set — the passenger app forces a reset on first
login (see routers/auth.py: /auth/passenger/login + /auth/change-password).
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from auth import require_permission
from capacity_logic import require_school_org
from database import supabase

router = APIRouter(prefix="/passengers", tags=["passengers"])
log = logging.getLogger("passengers")

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
    email: str = Field(..., min_length=3)  # login email (parent's email in school orgs)
    university_id: Optional[str] = None
    route_id: str = Field(..., min_length=1)
    # School module (students) — all optional so University is unaffected.
    parent_phone: Optional[str] = None
    parent_email: Optional[str] = None
    student_phone: Optional[str] = None
    grade: Optional[str] = None
    class_name: Optional[str] = None
    drop_off_stop: Optional[str] = None  # stop NAME on the assigned route


class PassengerUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    university_id: Optional[str] = None
    route_id: Optional[str] = None
    is_active: Optional[bool] = None
    parent_phone: Optional[str] = None
    parent_email: Optional[str] = None
    student_phone: Optional[str] = None
    grade: Optional[str] = None
    class_name: Optional[str] = None
    drop_off_stop: Optional[str] = None


# The school-only student detail columns on the `passengers` table.
_STUDENT_FIELDS = ("parent_phone", "parent_email", "student_phone", "grade", "class_name", "drop_off_stop")


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
    # Try by id only when it looks like a UUID (avoids a 22P02 error when a plain
    # route NAME is passed to an id/uuid column in the bulk path).
    if "-" in value and len(value) >= 32:
        by_id = supabase.table("routes").select("id").eq("id", value).eq("org_id", org_id).limit(1).execute()
        if by_id.data:
            return by_id.data[0]["id"]
    # Case-insensitive exact name match (no wildcards) so "maadi morning" matches
    # "Maadi Morning".
    by_name = supabase.table("routes").select("id").ilike("name", value).eq("org_id", org_id).limit(1).execute()
    return by_name.data[0]["id"] if by_name.data else None


def _resolve_stop(route_id: str, value: Optional[str]) -> Optional[str]:
    """Validate a drop-off stop NAME against the stops of `route_id`, returning the
    stop's canonical (DB-cased) name. Empty/None → None (no drop-off stop set).
    Raises 400 if the name isn't one of the route's stops. Matched case-insensitively
    so "main gate" resolves to "Main Gate"."""
    if not value or not value.strip():
        return None
    name = value.strip()
    match = (
        supabase.table("route_stops")
        .select("name")
        .eq("route_id", route_id)
        .ilike("name", name)
        .limit(1)
        .execute()
    )
    if not match.data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{name}' is not a stop on the selected route.",
        )
    return match.data[0]["name"]  # canonical casing from the route


def _create_passenger(
    org_id: str,
    name: str,
    email: str,
    university_id: Optional[str],
    route_id: str,
    extra: Optional[dict] = None,
) -> dict:
    """Create the auth account + profile + passengers row. Raises HTTPException
    on failure (with auth-account rollback). `extra` may carry the school-only
    student fields (parent_phone, grade, …)."""
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
        # University passenger: the student IS their own login, so parent_id = self.
        passenger_row = {"id": created_id, "org_id": org_id, "name": name, "parent_id": created_id, "university_id": university_id, "route_id": route_id}
        if extra:
            passenger_row.update({k: v for k, v in extra.items() if k in _STUDENT_FIELDS})
        supabase.table("passengers").insert(passenger_row).execute()
    except Exception as exc:
        supabase.table("profiles").delete().eq("id", created_id).execute()
        _delete_auth_user(created_id)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not create passenger record: {exc}")

    return prof


# ── School parent → students model ───────────────────────────────────────────
def _get_or_create_parent(org_id: str, parent_email: str) -> tuple:
    """Resolve the PARENT account for `parent_email` in this org, creating it once
    if missing. Returns (parent_profile_id, created_bool). Siblings reuse the same
    parent — no duplicate login. The parent is a profiles row (role='passenger')
    that owns the login (parent tracks the bus)."""
    parent_email = parent_email.strip()
    found = (
        supabase.table("profiles")
        .select("id")
        .eq("org_id", org_id)
        .eq("email", parent_email)
        .eq("role", "passenger")
        .limit(1)
        .execute()
    )
    if found.data:
        return found.data[0]["id"], False  # reuse existing parent (siblings)

    try:
        auth = supabase.auth.admin.create_user(
            {"email": parent_email, "password": DEFAULT_PASSENGER_PASSWORD, "email_confirm": True}
        )
        parent_id = auth.user.id
    except Exception as exc:
        text = str(exc).lower()
        if "already" in text and ("registered" in text or "exist" in text):
            # In Auth but not a passenger profile in THIS org (e.g. a staff email
            # or a parent in another org). A genuine conflict — report it per row.
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"The email '{parent_email}' is already registered to another account.")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not create parent login: {exc}")

    try:
        supabase.table("profiles").insert(
            {
                "id": parent_id,
                "org_id": org_id,
                "name": parent_email,  # parent logs in by email; UI can rename later
                "email": parent_email,
                "username": parent_email,
                "role": "passenger",
                "permissions": {},
                "is_active": True,
                "must_change_password": True,
            }
        ).execute()
    except Exception as exc:
        _delete_auth_user(parent_id)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not create parent profile: {exc}")
    return parent_id, True


def _insert_student(org_id: str, parent_id: str, name: str, route_id: str, extra: dict) -> dict:
    """Insert one STUDENT row linked to a parent. No login for the student."""
    row = {"org_id": org_id, "parent_id": parent_id, "name": name, "route_id": route_id}
    row.update({k: v for k, v in extra.items() if k in _STUDENT_FIELDS})
    return supabase.table("passengers").insert(row).execute().data[0]


def _create_student_by_parent_email(org_id: str, name: str, parent_email: str, route_id: str, extra: dict) -> bool:
    """Create a student, reusing (or creating) the parent by email. Returns whether
    a NEW parent login was created (so the UI can show credentials once)."""
    parent_id, created = _get_or_create_parent(org_id, parent_email)
    _insert_student(org_id, parent_id, name, route_id, extra)
    return created


@router.post("", status_code=status.HTTP_201_CREATED)
def create_passenger(
    body: PassengerCreate,
    current_user: dict = Depends(require_permission("manage_passengers")),
):
    org_id = current_user["org_id"]
    route_id = _resolve_route(org_id, body.route_id)
    if not route_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such route in your organization.")
    extra = {f: getattr(body, f) for f in _STUDENT_FIELDS}
    # Drop-off stop must be one of the route's stops; store the canonical name.
    extra["drop_off_stop"] = _resolve_stop(route_id, body.drop_off_stop)

    # School student: parent_email present → parent→student model (reuse siblings).
    if body.parent_email and body.parent_email.strip():
        parent_email = body.parent_email.strip()
        created = _create_student_by_parent_email(org_id, body.name.strip(), parent_email, route_id, extra)
        return {
            "email": parent_email,
            "default_password": DEFAULT_PASSENGER_PASSWORD if created else "",
            "must_change_password": created,
            "parent_created": created,
        }

    # University passenger: unchanged (the student is their own login).
    _create_passenger(org_id, body.name.strip(), body.email.strip(), body.university_id, route_id, extra)
    return {
        "email": body.email.strip(),
        "default_password": DEFAULT_PASSENGER_PASSWORD,
        "must_change_password": True,
        "parent_created": True,
    }


@router.get("")
def list_passengers(current_user: dict = Depends(require_permission("manage_passengers"))):
    org_id = current_user["org_id"]
    rows = (
        supabase.table("passengers")
        .select("id, name, university_id, route_id, parent_phone, parent_email, student_phone, grade, class_name, drop_off_stop, created_at")
        .eq("org_id", org_id)
        .execute()
        .data
    )
    ids = [r["id"] for r in rows]
    profs, routes = {}, {}
    if ids:
        # University students have their own profile (id == passengers.id); school
        # students don't (only the parent does), so this join simply misses them.
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
                "name": r.get("name") or p.get("name"),  # student name lives on the student row now
                "email": p.get("email") or r.get("parent_email"),  # University = student login; school = parent email
                "is_active": p.get("is_active", True),
                "university_id": r.get("university_id"),
                "route_id": r.get("route_id"),
                "route_name": routes.get(r.get("route_id")),
                "parent_phone": r.get("parent_phone"),
                "parent_email": r.get("parent_email"),
                "student_phone": r.get("student_phone"),
                "grade": r.get("grade"),
                "class_name": r.get("class_name"),
                "drop_off_stop": r.get("drop_off_stop"),
            }
        )
    out.sort(key=lambda x: (x["name"] or "").lower())
    return {"count": len(out), "passengers": out}


@router.get("/parents")
def list_parents(current_user: dict = Depends(require_permission("manage_passengers"))):
    """All PARENTS in the org (school module) with their linked CHILDREN. Read-only,
    reusing the parent→students model: a parent is the profiles row that owns the
    login; a child is a passengers row whose parent_id points at it. Grouped so a
    parent with several children shows all of them."""
    org_id = current_user["org_id"]
    require_school_org(org_id)

    students = (
        supabase.table("passengers")
        .select("id, name, grade, class_name, route_id, drop_off_stop, parent_id, parent_email, parent_phone")
        .eq("org_id", org_id)
        .execute()
        .data
    )
    by_parent: dict = {}
    for s in students:
        pid = s.get("parent_id")
        if pid:
            by_parent.setdefault(pid, []).append(s)

    parent_ids = list(by_parent.keys())
    profs = {}
    if parent_ids:
        profs = {p["id"]: p for p in supabase.table("profiles").select("id, name, email, phone").in_("id", parent_ids).execute().data}
    route_ids = list({s.get("route_id") for s in students if s.get("route_id")})
    routes = {}
    if route_ids:
        routes = {r["id"]: r["name"] for r in supabase.table("routes").select("id, name").in_("id", route_ids).execute().data}

    parents = []
    for pid, kids in by_parent.items():
        prof = profs.get(pid, {})
        # Prefer the parent PROFILE's email/phone; fall back to a child's contact fields.
        email = prof.get("email") or next((k.get("parent_email") for k in kids if k.get("parent_email")), None)
        phone = prof.get("phone") or next((k.get("parent_phone") for k in kids if k.get("parent_phone")), None)
        children = [
            {
                "id": k["id"],
                "name": k.get("name"),
                "grade": k.get("grade"),
                "class_name": k.get("class_name"),
                "route_name": routes.get(k.get("route_id")),
                "drop_off_stop": k.get("drop_off_stop"),
            }
            for k in kids
        ]
        children.sort(key=lambda c: (c["name"] or "").lower())
        parents.append({"id": pid, "name": prof.get("name"), "email": email, "phone": phone, "children": children})
    parents.sort(key=lambda p: (p["name"] or p["email"] or "").lower())
    return {"count": len(parents), "parents": parents}


@router.patch("/{passenger_id}")
def update_passenger(
    passenger_id: str,
    body: PassengerUpdate,
    current_user: dict = Depends(require_permission("manage_passengers")),
):
    org_id = current_user["org_id"]
    existing = supabase.table("passengers").select("id, route_id, drop_off_stop").eq("id", passenger_id).eq("org_id", org_id).limit(1).execute()
    if not existing.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Passenger not found.")

    # is_active lives on the profile (University students; parent for school).
    prof_update: dict = {}
    if body.name is not None:
        prof_update["name"] = body.name  # keeps the University student's profile name in sync
    if body.is_active is not None:
        prof_update["is_active"] = body.is_active
    if prof_update:
        supabase.table("profiles").update(prof_update).eq("id", passenger_id).eq("org_id", org_id).execute()

    pax_update: dict = {}
    if body.name is not None:
        pax_update["name"] = body.name  # student name now lives on the student row
    if "university_id" in body.model_fields_set:
        pax_update["university_id"] = body.university_id
    # Student fields EXCEPT drop_off_stop (validated separately against the route).
    for f in _STUDENT_FIELDS:
        if f != "drop_off_stop" and f in body.model_fields_set:
            pax_update[f] = getattr(body, f)

    route_changed = False
    if body.route_id is not None:
        rid = _resolve_route(org_id, body.route_id)
        if not rid:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such route in your organization.")
        pax_update["route_id"] = rid
        route_changed = rid != existing.data[0].get("route_id")

    # Drop-off stop is validated against the EFFECTIVE route (the new one if the
    # route is changing, else the existing one).
    effective_route = pax_update.get("route_id") or existing.data[0].get("route_id")
    if "drop_off_stop" in body.model_fields_set:
        pax_update["drop_off_stop"] = _resolve_stop(effective_route, body.drop_off_stop) if effective_route else None
    elif route_changed and existing.data[0].get("drop_off_stop"):
        # Route changed but no new stop given → the old stop won't exist on the new
        # route; clear it rather than leave a stale, off-route drop-off.
        pax_update["drop_off_stop"] = None

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
            errors.append({"row": i + 2, "label": row.email, "error": str(exc.detail)})  # +2: header + 1-indexed
        except Exception as exc:
            errors.append({"row": i + 2, "label": row.email, "error": str(exc)})
    return {"created": created, "failed": len(errors), "errors": errors}


class BulkStudentRow(BaseModel):
    name: Optional[str] = None
    parent_phone: Optional[str] = None
    parent_email: Optional[str] = None  # the login (the parent tracks the bus)
    student_phone: Optional[str] = None
    grade: Optional[str] = None
    class_name: Optional[str] = None
    route: Optional[str] = None  # route id OR name
    drop_off_stop: Optional[str] = None  # stop NAME on that route


class BulkStudentRequest(BaseModel):
    rows: List[BulkStudentRow]


@router.post("/bulk-students")
def bulk_create_students(
    body: BulkStudentRequest,
    current_user: dict = Depends(require_permission("manage_passengers")),
):
    """Bulk-create STUDENTS (school module) with the CURRENT fields. Uses the SAME
    working create path as the single-student form — role='passenger', the parent
    email as the login — so it can't hit the enum/role error. Each row is attempted
    independently with per-row error reporting."""
    org_id = current_user["org_id"]
    created = 0
    errors = []
    log.info("bulk-students: received %s rows for org %s", len(body.rows), org_id)
    for i, row in enumerate(body.rows):
        rownum = i + 2  # +2: header row + 1-indexed
        label = (row.name or "").strip() or (row.parent_email or "").strip()
        # Log EXACTLY what was parsed from this row (reveals header/parse issues:
        # all-empty values mean the CSV headers didn't map).
        log.info("bulk-students row %s parsed: %s", rownum, row.model_dump())
        try:
            name = (row.name or "").strip()
            parent_email = (row.parent_email or "").strip()
            parent_phone = (row.parent_phone or "").strip()
            route_val = (row.route or "").strip()
            if not name:
                raise ValueError("Name is required.")
            if not parent_email:
                raise ValueError("Parent email is required.")
            if not parent_phone:
                raise ValueError("Parent phone is required.")
            if not route_val:
                raise ValueError("Route is required.")
            route_id = _resolve_route(org_id, route_val)
            if not route_id:
                raise ValueError(f"Unknown route '{route_val}'.")
            # Optional drop-off stop, validated (by name) against the route's stops.
            drop_off_stop = _resolve_stop(route_id, (row.drop_off_stop or "").strip() or None)
            extra = {
                "parent_phone": parent_phone,
                "parent_email": parent_email,
                "student_phone": (row.student_phone or "").strip() or None,
                "grade": (row.grade or "").strip() or None,
                "class_name": (row.class_name or "").strip() or None,
                "drop_off_stop": drop_off_stop,
            }
            # Parent → student: siblings/duplicate parent emails REUSE the same
            # parent account (no duplicate login). Same path as single create.
            _create_student_by_parent_email(org_id, name, parent_email, route_id, extra)
            created += 1
        except HTTPException as exc:
            log.warning("bulk-students row %s FAILED: %s", rownum, exc.detail)
            errors.append({"row": rownum, "label": label, "error": str(exc.detail)})
        except Exception as exc:
            # Full traceback + message so the REAL cause (e.g. a missing column from
            # an un-run migration) is visible in the server logs and the response.
            log.exception("bulk-students row %s ERROR", rownum)
            errors.append({"row": rownum, "label": label, "error": str(exc)})
    return {"created": created, "failed": len(errors), "errors": errors}
