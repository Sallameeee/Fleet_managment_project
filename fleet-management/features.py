"""Feature-flag registry — the SINGLE source of truth for which features exist,
which module they belong to, and which are always-on CORE.

Module scoping is strict: a feature with module='school' is ONLY ever offered to /
enabled for a school org, and 'university' only for a university org. 'both' means
it applies to either module. Nothing here ever blends the two modules.

Storage: organizations.enabled_features (jsonb array of TOGGLEABLE keys).
  * NULL  → legacy org → ALL of its module's features ON (backward compatibility).
  * [...] → core (always) + exactly these toggleable keys (filtered to the module).
"""

from fastapi import Depends, HTTPException, status

from database import supabase

# key -> (label, module, core). module ∈ {"both", "school", "university"}.
FEATURES: dict[str, dict] = {
    # ── CORE — always on, cannot be disabled (the base tracking stack). ──────────
    "tracking": {"label": "Live Tracking", "module": "both", "core": True},
    "history": {"label": "History", "module": "both", "core": True},
    "drivers": {"label": "Drivers / Supervisors", "module": "both", "core": True},
    "vehicles": {"label": "Vehicles", "module": "both", "core": True},
    "routes": {"label": "Routes", "module": "both", "core": True},
    "assignments": {"label": "Assignments", "module": "both", "core": True},
    "settings": {"label": "Settings", "module": "both", "core": True},
    # ── TOGGLEABLE — both modules. ──────────────────────────────────────────────
    "passengers": {"label": "Passengers / Students", "module": "both", "core": False},
    "alerts": {"label": "Alerts", "module": "both", "core": False},
    "reports": {"label": "Issue Reports", "module": "both", "core": False},
    # ── TOGGLEABLE — SCHOOL only. ───────────────────────────────────────────────
    "attendance": {"label": "Attendance", "module": "school", "core": False},
    "change_requests": {"label": "Bus-change Requests", "module": "school", "core": False},
    "capacity": {"label": "Bus Capacity (FULL / seats)", "module": "school", "core": False},
    "profile_requests": {"label": "Profile-edit Requests", "module": "school", "core": False},
    "parents_page": {"label": "Parents Directory", "module": "school", "core": False},
    "directory": {"label": "Staff Directory", "module": "school", "core": False},
    "bus_drivers": {"label": "Bus Drivers", "module": "school", "core": False},
    "buses_map": {"label": "All-Buses Map", "module": "school", "core": False},
    "buses_today": {"label": "Buses Today", "module": "school", "core": False},
    "performance": {"label": "Performance Monitoring", "module": "school", "core": False},
    "logs": {"label": "Event Logs", "module": "school", "core": False},
    "notifications": {"label": "Notifications", "module": "school", "core": False},
}

_MODULES = ("university", "school")


def _applies(feat_module: str, org_mod: str) -> bool:
    return feat_module == "both" or feat_module == org_mod


def features_for_module(org_mod: str) -> set[str]:
    """Every feature key valid for a module (core + toggleable)."""
    return {k for k, v in FEATURES.items() if _applies(v["module"], org_mod)}


def core_features(org_mod: str) -> set[str]:
    return {k for k, v in FEATURES.items() if v["core"] and _applies(v["module"], org_mod)}


def catalog(org_mod: str) -> dict:
    """For the super-admin toggle UI: the module's core (locked-on) + toggleable
    features, each with key + label. STRICTLY module-scoped."""
    mod = org_mod if org_mod in _MODULES else "university"
    core = [{"key": k, "label": FEATURES[k]["label"]} for k, v in FEATURES.items() if v["core"] and _applies(v["module"], mod)]
    toggle = [{"key": k, "label": FEATURES[k]["label"]} for k, v in FEATURES.items() if not v["core"] and _applies(v["module"], mod)]
    return {"module": mod, "core": core, "toggleable": toggle}


def sanitize_enabled(org_mod: str, keys) -> list[str]:
    """Validate a proposed enabled set from the super admin: keep only TOGGLEABLE
    keys valid for the module (core is implicit; cross-module keys are dropped)."""
    if not keys:
        return []
    valid = {k for k, v in FEATURES.items() if not v["core"] and _applies(v["module"], org_mod)}
    seen, out = set(), []
    for k in keys:
        if k in valid and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def enabled_for_org(org_row: dict) -> set[str]:
    """The EFFECTIVE enabled feature keys for an org row {module, enabled_features}:
    core (always) + stored toggleable; NULL stored = legacy → ALL module features."""
    mod = (org_row or {}).get("module") or "university"
    stored = (org_row or {}).get("enabled_features")
    if stored is None:
        return features_for_module(mod)  # legacy org → everything on
    valid = features_for_module(mod)
    return core_features(mod) | {k for k in stored if k in valid}


def _org_row(org_id: str):
    """Fetch {module, enabled_features} RESILIENTLY. If the enabled_features column
    doesn't exist yet (migration 033 not run), fall back to selecting just `module`
    so the row has NO enabled_features key → treated as legacy → ALL features ON.
    This guarantees a deploy-before-migration can never gate everything off."""
    try:
        r = supabase.table("organizations").select("module, enabled_features").eq("id", org_id).limit(1).execute().data
        return r[0] if r else None
    except Exception:
        try:
            r = supabase.table("organizations").select("module").eq("id", org_id).limit(1).execute().data
            return r[0] if r else None  # no enabled_features key → legacy (all-on)
        except Exception:
            return None


def org_enabled_features(org_id: str) -> set[str]:
    """Effective enabled features for an org id (one resilient DB read)."""
    row = _org_row(org_id)
    return enabled_for_org(row) if row else set()


def module_and_enabled(org_id: str) -> tuple[str, list[str]]:
    """(module, sorted enabled feature keys) for the payloads — resilient to a
    missing column (→ legacy all-on)."""
    row = _org_row(org_id)
    if not row:
        return "university", []
    return (row.get("module") or "university"), sorted(enabled_for_org(row))


def has_feature(org_id: str, key: str) -> bool:
    return key in org_enabled_features(org_id)


def require_feature(key: str):
    """Dependency: 403 if `key` is not enabled for the caller's org. Stack it
    alongside the existing permission/role dependency on a feature's endpoints so a
    disabled feature genuinely refuses (not just hidden in the UI). Legacy orgs
    (enabled_features NULL) have everything on, so existing behaviour is preserved."""

    from auth import get_current_user  # lazy → avoids an auth<->features load cycle

    def dep(current_user: dict = Depends(get_current_user)) -> dict:
        if not has_feature(current_user.get("org_id"), key):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"The '{key}' feature is not enabled for your organization.",
            )
        return current_user

    return dep
