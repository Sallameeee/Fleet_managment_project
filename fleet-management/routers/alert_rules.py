"""Alert rule management — manager-defined, org-scoped rules that configure
which alerts fire and to whom they apply.

Gated with manage_trips (same as trips/assignments — alerting is part of trip
operations; a dedicated `manage_alerts` permission would be the alternative if
you later want to split it from trip scheduling).

Phase 1: rules drive panel alerts and *record* whether email/push is wanted.
Actual email/push delivery is deferred to the Firebase work.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from auth import require_permission
from database import supabase

router = APIRouter(prefix="/alert-rules", tags=["alert-rules"])

ALERT_TYPES = {"speeding", "off_route", "short_stop", "offline"}
TARGET_KINDS = {"all", "vehicles", "drivers"}
# Types whose threshold is meaningful (and required, > 0). short_stop uses the
# route_stops.dwell_minutes instead, so its threshold is ignored.
THRESHOLD_TYPES = {"speeding", "off_route", "offline"}
THRESHOLD_UNIT = {"speeding": "km/h", "off_route": "meters", "offline": "minutes"}


class AlertRuleCreate(BaseModel):
    name: str = Field(..., min_length=1)
    type: str
    threshold: Optional[float] = None
    target_kind: str = "all"
    target_ids: Optional[List[str]] = None
    notify_panel: bool = True
    notify_email: bool = False
    notify_push: bool = False
    is_active: bool = True


class AlertRuleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    type: Optional[str] = None
    threshold: Optional[float] = None
    target_kind: Optional[str] = None
    target_ids: Optional[List[str]] = None
    notify_panel: Optional[bool] = None
    notify_email: Optional[bool] = None
    notify_push: Optional[bool] = None
    is_active: Optional[bool] = None


def _bad(detail: str):
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _targets_key(target_ids):
    """Order-independent key for target_ids ([a,b] == [b,a]); None/[] => ()."""
    return tuple(sorted(target_ids or []))


def _norm_targets(target_ids):
    """Canonical storage form: sorted list, or None for empty/null. Storing them
    sorted makes the DB unique index order-independent (set equality)."""
    return sorted(target_ids) if target_ids else None


def _assert_no_active_duplicate(
    org_id: str, type_: str, threshold, target_kind: str, target_ids, exclude_id=None
) -> None:
    """409 if an ACTIVE rule with the same (type, threshold, target_kind,
    target_ids) already exists. target_ids compared order-independently.
    `exclude_id` skips the rule being edited (so a PATCH can't conflict itself).
    """
    existing = (
        supabase.table("alert_rules")
        .select("id, name, threshold, target_kind, target_ids")
        .eq("org_id", org_id)
        .eq("type", type_)
        .eq("target_kind", target_kind)
        .eq("is_active", True)
        .execute()
    ).data

    want_targets = _targets_key(target_ids)
    for r in existing:
        if exclude_id and r["id"] == exclude_id:
            continue
        # numeric threshold compare (None == None; short_stop both None).
        same_threshold = (r["threshold"] is None and threshold is None) or (
            r["threshold"] is not None
            and threshold is not None
            and float(r["threshold"]) == float(threshold)
        )
        if same_threshold and _targets_key(r["target_ids"]) == want_targets:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"An active rule with the same type/threshold/target already "
                    f"exists (id '{r['id']}', name '{r['name']}')."
                ),
            )


def _validate_type(type_: str) -> None:
    if type_ not in ALERT_TYPES:
        raise _bad(f"type must be one of {sorted(ALERT_TYPES)}.")


def _validate_threshold(type_: str, threshold: Optional[float]) -> None:
    if type_ in THRESHOLD_TYPES:
        if threshold is None or threshold <= 0:
            raise _bad(
                f"A {type_} rule needs a positive threshold in {THRESHOLD_UNIT[type_]}."
            )
    # short_stop: threshold ignored; no validation needed.


def _validate_targets(target_kind: str, target_ids, org_id: str) -> None:
    """Check target_kind and that any specific ids belong to the caller's org."""
    if target_kind not in TARGET_KINDS:
        raise _bad(f"target_kind must be one of {sorted(TARGET_KINDS)}.")
    if target_kind == "all" or not target_ids:
        return  # NULL/empty => applies to all; nothing to verify.

    if target_kind == "vehicles":
        rows = (
            supabase.table("vehicles")
            .select("id")
            .eq("org_id", org_id)
            .in_("id", target_ids)
            .execute()
        ).data
        label = "vehicle"
    else:  # drivers
        rows = (
            supabase.table("profiles")
            .select("id")
            .eq("org_id", org_id)
            .eq("role", "driver")
            .in_("id", target_ids)
            .execute()
        ).data
        label = "driver"

    found = {r["id"] for r in rows}
    missing = [tid for tid in target_ids if tid not in found]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"These {label} ids are not in your organization: {missing}",
        )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_alert_rule(
    body: AlertRuleCreate,
    current_user: dict = Depends(require_permission("manage_trips")),
):
    org_id = current_user["org_id"]

    _validate_type(body.type)
    _validate_threshold(body.type, body.threshold)
    _validate_targets(body.target_kind, body.target_ids, org_id)

    # Normalize threshold the same way it's stored, so the duplicate check
    # compares apples to apples (short_stop threshold is dropped to None).
    norm_threshold = body.threshold if body.type in THRESHOLD_TYPES else None
    if body.is_active:
        _assert_no_active_duplicate(
            org_id, body.type, norm_threshold, body.target_kind, body.target_ids
        )

    payload = {
        "org_id": org_id,  # from token, never the body
        "name": body.name,
        "type": body.type,
        # Drop a meaningless threshold for short_stop.
        "threshold": body.threshold if body.type in THRESHOLD_TYPES else None,
        "target_kind": body.target_kind,
        "target_ids": _norm_targets(body.target_ids),  # sorted, canonical
        "notify_panel": body.notify_panel,
        "notify_email": body.notify_email,
        "notify_push": body.notify_push,
        "is_active": body.is_active,
    }
    try:
        result = supabase.table("alert_rules").insert(payload).execute()
    except Exception as exc:
        raise _bad(f"Could not create alert rule: {exc}")
    return result.data[0]


@router.get("")
def list_alert_rules(
    current_user: dict = Depends(require_permission("manage_trips")),
):
    org_id = current_user["org_id"]
    result = (
        supabase.table("alert_rules")
        .select("*")
        .eq("org_id", org_id)
        .order("created_at", desc=True)
        .execute()
    )
    return {"count": len(result.data), "alert_rules": result.data}


def _load_org_rule(rule_id: str, org_id: str) -> dict:
    res = (
        supabase.table("alert_rules")
        .select("*")
        .eq("id", rule_id)
        .eq("org_id", org_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No alert rule with id '{rule_id}' exists in your organization.",
        )
    return res.data[0]


@router.patch("/{rule_id}")
def update_alert_rule(
    rule_id: str,
    body: AlertRuleUpdate,
    current_user: dict = Depends(require_permission("manage_trips")),
):
    org_id = current_user["org_id"]
    existing = _load_org_rule(rule_id, org_id)

    # Effective values after the patch (fall back to existing).
    fields = body.model_dump(exclude_unset=True)
    eff_type = fields.get("type", existing["type"])
    eff_kind = fields.get("target_kind", existing["target_kind"])
    eff_ids = fields.get("target_ids", existing["target_ids"])
    eff_threshold = fields.get("threshold", existing["threshold"])

    if "type" in fields:
        _validate_type(eff_type)
    if {"type", "threshold"} & fields.keys():
        _validate_threshold(eff_type, eff_threshold)
    if {"target_kind", "target_ids"} & fields.keys():
        _validate_targets(eff_kind, eff_ids, org_id)

    # Keep threshold consistent with the (possibly new) type.
    if "type" in fields or "threshold" in fields:
        fields["threshold"] = eff_threshold if eff_type in THRESHOLD_TYPES else None
    if "target_ids" in fields:
        fields["target_ids"] = _norm_targets(fields["target_ids"])  # sorted, canonical

    # If the patched rule would be ACTIVE, make sure it doesn't duplicate another
    # active rule (excluding itself). Catches e.g. editing a threshold to match,
    # or re-activating into an existing duplicate.
    eff_is_active = fields.get("is_active", existing["is_active"])
    if eff_is_active:
        norm_eff_threshold = eff_threshold if eff_type in THRESHOLD_TYPES else None
        _assert_no_active_duplicate(
            org_id, eff_type, norm_eff_threshold, eff_kind, eff_ids, exclude_id=rule_id
        )

    if not fields:
        return existing  # nothing to change

    try:
        result = (
            supabase.table("alert_rules")
            .update(fields)
            .eq("id", rule_id)
            .eq("org_id", org_id)
            .execute()
        )
    except Exception as exc:
        raise _bad(f"Could not update alert rule: {exc}")
    return result.data[0]


@router.delete("/{rule_id}", status_code=status.HTTP_200_OK)
def delete_alert_rule(
    rule_id: str,
    current_user: dict = Depends(require_permission("manage_trips")),
):
    org_id = current_user["org_id"]
    _load_org_rule(rule_id, org_id)  # 404 if not in org
    supabase.table("alert_rules").delete().eq("id", rule_id).eq("org_id", org_id).execute()
    return {"deleted": rule_id}
