"""Organization branding endpoints (logo + display name).

  GET    /branding/me                    any signed-in user: their org's name, logo, phone
  PUT    /branding/logo                  manager (manage_settings): upload own org's logo
  DELETE /branding/logo                  manager (manage_settings): remove it
  PUT    /branding/orgs/{org_id}/logo    super-admin: upload any org's logo
  DELETE /branding/orgs/{org_id}/logo    super-admin: remove it

Uploads are JSON `{"data_base64": "...", "filename": "logo.png"}` (no multipart
dependency). The type is sniffed from the bytes; see branding.py.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

import branding
from auth import get_current_user, require_permission, require_super_admin
from database import supabase

router = APIRouter(prefix="/branding", tags=["branding"])


class LogoUpload(BaseModel):
    data_base64: str = Field(..., min_length=8, description="The image file, base64 (a data: URL prefix is accepted).")
    filename: Optional[str] = Field(None, max_length=200)


def _store(org_id: str, body: LogoUpload) -> dict:
    try:
        data = branding.decode_upload(body.data_base64)
        branding.upload_logo(org_id, data)
    except branding.LogoError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Could not store the logo right now. Please try again.")
    return branding.org_branding(org_id)


def _org_exists(org_id: str) -> None:
    try:
        r = supabase.table("organizations").select("id").eq("id", org_id).limit(1).execute().data
    except Exception:
        r = None
    if not r:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found.")


@router.get("/me")
def my_branding(current_user: dict = Depends(get_current_user)):
    return branding.org_branding(current_user.get("org_id"))


@router.put("/logo")
def upload_my_logo(body: LogoUpload, current_user: dict = Depends(require_permission("manage_settings"))):
    return _store(current_user["org_id"], body)


@router.delete("/logo")
def delete_my_logo(current_user: dict = Depends(require_permission("manage_settings"))):
    branding.delete_logo(current_user["org_id"])
    return branding.org_branding(current_user["org_id"])


@router.put("/orgs/{org_id}/logo")
def upload_org_logo(org_id: str, body: LogoUpload, _admin: dict = Depends(require_super_admin)):
    _org_exists(org_id)
    return _store(org_id, body)


@router.delete("/orgs/{org_id}/logo")
def delete_org_logo(org_id: str, _admin: dict = Depends(require_super_admin)):
    _org_exists(org_id)
    branding.delete_logo(org_id)
    return branding.org_branding(org_id)


@router.get("/orgs/{org_id}")
def org_branding_admin(org_id: str, _admin: dict = Depends(require_super_admin)):
    _org_exists(org_id)
    return branding.org_branding(org_id)
