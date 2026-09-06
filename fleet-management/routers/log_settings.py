"""Edit Logs — manager settings for what gets logged (per event type: on/off +
threshold). Org-scoped, module-aware (the catalog is filtered to the caller's
module), shared implementation for school and university.

  GET   /log-settings   -> catalog rows merged with the org's saved values
  PATCH /log-settings   -> {type: {enabled?, threshold?, duration_s?}}

Gated on manage_settings (it is organization configuration). Detection in
routers/trips.py reads the same effective settings on every ping batch.
"""

from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from auth import require_permission
from capacity_logic import org_module
from database import supabase
from log_settings_logic import catalog_for_module, effective_log_settings, validate_and_merge

router = APIRouter(prefix="/log-settings", tags=["log-settings"])


class LogTypePatch(BaseModel):
    enabled: Optional[bool] = None
    threshold: Optional[float] = None
    duration_s: Optional[int] = None


def _view(org_id: str, module: str) -> dict:
    eff = effective_log_settings(org_id, module)
    rows = []
    for e in catalog_for_module(module):
        v = eff[e["type"]]
        rows.append(
            {
                "type": e["type"],
                "label": e["label"],
                "unit": e["unit"],
                "threshold_kind": e["threshold_kind"],
                "help": e["help"],
                "min": e.get("min"),
                "max": e.get("max"),
                "enabled": v["enabled"],
                "threshold": v["threshold"],
                "duration_s": v["duration_s"],
                "has_duration": e.get("duration_s") is not None,
                "default_threshold": e["default_threshold"],
                "explicit": v["explicit"],
            }
        )
    return {"module": module, "configurable": eff["_configurable"], "events": rows}


@router.get("")
def get_log_settings(current_user: dict = Depends(require_permission("manage_settings"))):
    org_id = current_user["org_id"]
    return _view(org_id, org_module(org_id))


@router.patch("")
def patch_log_settings(
    body: Dict[str, LogTypePatch],
    current_user: dict = Depends(require_permission("manage_settings")),
):
    org_id = current_user["org_id"]
    module = org_module(org_id)
    patch = {t: v.model_dump(exclude_none=True) for t, v in body.items()}
    try:
        merged = validate_and_merge(org_id, module, patch)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    try:
        supabase.table("organizations").update({"log_settings": merged}).eq("id", org_id).execute()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not save log settings: {exc}")
    # Keep the legacy org-wide alert_rules rows (Alerts page) in step so the two
    # screens never disagree about the org-wide speeding / off-route limits.
    _sync_legacy_rules(org_id, merged)
    return _view(org_id, module)


def _sync_legacy_rules(org_id: str, merged: dict) -> None:
    """Best-effort: mirror enabled/threshold of speeding & off_route onto the
    org-wide (target_kind='all') alert_rules rows, creating none if absent."""
    try:
        rows = (
            supabase.table("alert_rules")
            .select("id, type, target_kind")
            .eq("org_id", org_id)
            .eq("target_kind", "all")
            .in_("type", ["speeding", "off_route"])
            .execute()
            .data
        )
        for r in rows:
            s = merged.get(r["type"])
            if not isinstance(s, dict):
                continue
            upd = {"is_active": bool(s.get("enabled", True))}
            if s.get("threshold") is not None:
                upd["threshold"] = s["threshold"]
            supabase.table("alert_rules").update(upd).eq("id", r["id"]).execute()
    except Exception:
        pass
