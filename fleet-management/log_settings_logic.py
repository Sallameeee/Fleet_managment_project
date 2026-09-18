"""Per-organization "Edit Logs" settings: which event types are logged and with
what threshold. ONE catalog, shared by both modules; each entry declares the
modules it applies to so a module-specific type can never leak into the other.

Storage: organizations.log_settings (jsonb, migration 043):
    {"speeding":   {"enabled": true, "threshold": 80},          # km/h
     "off_route":  {"enabled": true, "threshold": 300,          # metres
                    "duration_s": 60},                          # must persist
     "long_stop":  {"enabled": true, "threshold": 5},           # minutes
     "short_stop": {"enabled": true},                           # per-stop dwell
     "offline":    {"enabled": true, "threshold": 5}}           # minutes w/o GPS

Resilience: if the column does not exist yet, or a type is missing from the
JSON, the catalog DEFAULT applies and the entry is reported as not explicit.
Detection (routers/trips.py) reads `effective_log_settings(org_id)` once per
ping batch. For speeding/off_route the org-wide value here supersedes any
legacy org-wide (`target_kind='all'`) alert_rules row ONLY once a manager has
saved Edit Logs (explicit); targeted rules (specific vehicles/drivers) keep
working on top.
"""

from __future__ import annotations

from typing import Optional

from database import supabase

# Order = display order on the Edit Logs page.
LOG_EVENT_CATALOG: list[dict] = [
    {"type": "speeding", "label": "Speeding", "label_ar": "تجاوز السرعة", "unit": "km/h", "threshold_kind": "limit",
     "default_enabled": True, "default_threshold": 80, "min": 10, "max": 200,
     "modules": ["school", "university"],
     "help": "Logged when GPS speed exceeds this limit.",
     "help_ar": "يُسجَّل عندما تتجاوز سرعة الـGPS هذا الحد."},
    {"type": "off_route", "label": "Off route", "label_ar": "الخروج عن المسار", "unit": "metres", "threshold_kind": "distance",
     "default_enabled": True, "default_threshold": 300, "min": 50, "max": 5000,
     "duration_s": 60, "duration_min_s": 0, "duration_max_s": 900,
     "modules": ["school", "university"],
     "help": "Logged when the bus is farther than this from the planned route line for longer than the duration.",
     "help_ar": "يُسجَّل عندما تبتعد الحافلة عن خط المسار المخطط أكثر من هذه المسافة لأطول من المدة المحددة."},
    {"type": "long_stop", "label": "Long stop", "label_ar": "التوقف الطويل", "unit": "minutes", "threshold_kind": "duration",
     "default_enabled": True, "default_threshold": 5, "min": 1, "max": 180,
     "modules": ["school", "university"],
     "help": "Logged when the bus stands still away from any scheduled stop for longer than this.",
     "help_ar": "يُسجَّل عندما تقف الحافلة ثابتة بعيدًا عن أي محطة مجدولة لأطول من هذه المدة."},
    {"type": "short_stop", "label": "Stop shorter than required", "label_ar": "توقف أقصر من المطلوب", "unit": None, "threshold_kind": None,
     "default_enabled": True, "default_threshold": None,
     "modules": ["school", "university"],
     "help": "Logged when the bus leaves a scheduled stop before its required waiting time (per-stop dwell).",
     "help_ar": "يُسجَّل عندما تغادر الحافلة محطة مجدولة قبل انتهاء مدة الانتظار المطلوبة لها."},
    {"type": "offline", "label": "Went offline", "label_ar": "انقطاع الإرسال", "unit": "minutes", "threshold_kind": "duration",
     "default_enabled": True, "default_threshold": 5, "min": 1, "max": 120,
     "modules": ["school", "university"],
     "help": "Logged when no GPS data arrives for longer than this during an active trip.",
     "help_ar": "يُسجَّل عندما لا تصل أي بيانات GPS لأطول من هذه المدة أثناء رحلة نشطة."},
    # ── Trip lifecycle log points (trip_lifecycle.py; migration 045) ──
    {"type": "trip_started", "label": "Trip started", "label_ar": "بدء الرحلة", "unit": None, "threshold_kind": None,
     "default_enabled": True, "default_threshold": None,
     "modules": ["school", "university"],
     "help": "Logged when a trip starts; the marker is the first accepted GPS fix.",
     "help_ar": "يُسجَّل عند بدء الرحلة؛ العلامة على الخريطة هي أول موقع مقبول."},
    {"type": "connection_lost", "label": "Connection lost", "label_ar": "انقطاع الاتصال", "unit": "seconds", "threshold_kind": "duration",
     "default_enabled": True, "default_threshold": 90, "min": 30, "max": 900,
     "modules": ["school", "university"],
     "help": "Logged when no GPS fix has ARRIVED from the phone for longer than this during an active trip (marker = last known fix).",
     "help_ar": "يُسجَّل عندما لا يصل أي موقع من الهاتف لأطول من هذه المدة أثناء رحلة نشطة (العلامة = آخر موقع معروف)."},
    {"type": "connection_restored", "label": "Connection restored", "label_ar": "عودة الاتصال", "unit": None, "threshold_kind": None,
     "default_enabled": True, "default_threshold": None,
     "modules": ["school", "university"],
     "help": "Logged when fixes resume after a connection loss, with the gap and how many buffered fixes came back with their original timestamps.",
     "help_ar": "يُسجَّل عند عودة المواقع بعد انقطاع، مع مدة الانقطاع وعدد النقاط المخزّنة التي رُفعت بتوقيتها الأصلي."},
    {"type": "trip_ended", "label": "Trip ended (with reason)", "label_ar": "انتهاء الرحلة (مع السبب)", "unit": "minutes", "threshold_kind": "duration",
     "default_enabled": True, "default_threshold": 30, "min": 5, "max": 240,
     "modules": ["school", "university"],
     "help": "Logged when a trip ends and HOW (End Trip, cancelled, app closed, network lost, device off, uncertain). The limit is how long a trip may stay silent (no fix, no heartbeat) before it is closed automatically.",
     "help_ar": "يُسجَّل عند انتهاء الرحلة وكيف انتهت (إنهاء من السائق، إلغاء، إغلاق التطبيق، انقطاع الشبكة، انطفاء الجهاز، غير مؤكد). الحد هو أقصى مدة صمت (لا موقع ولا نبضة) قبل إغلاق الرحلة تلقائيًا."},
]

CATALOG_BY_TYPE = {e["type"]: e for e in LOG_EVENT_CATALOG}


def catalog_for_module(module: str) -> list[dict]:
    return [e for e in LOG_EVENT_CATALOG if module in e["modules"]]


def _load_raw(org_id: str) -> tuple[Optional[dict], bool]:
    """(json or None, column_exists)."""
    try:
        rows = supabase.table("organizations").select("log_settings").eq("id", org_id).limit(1).execute().data
        raw = rows[0].get("log_settings") if rows else None
        return (raw if isinstance(raw, dict) else None), True
    except Exception as exc:
        if "log_settings" in str(exc):
            return None, False
        return None, True


def effective_log_settings(org_id: str, module: Optional[str] = None) -> dict:
    """Merged view: {type: {enabled, threshold, duration_s, explicit}} for the
    org's module (all types when module is None) + '_configurable' (column
    exists). Never raises."""
    raw, exists = _load_raw(org_id)
    raw = raw or {}
    out: dict = {"_configurable": exists}
    for e in LOG_EVENT_CATALOG:
        if module and module not in e["modules"]:
            continue
        saved = raw.get(e["type"]) if isinstance(raw.get(e["type"]), dict) else None
        entry = {
            "enabled": bool(saved.get("enabled", e["default_enabled"])) if saved else e["default_enabled"],
            "threshold": e["default_threshold"],
            "duration_s": e.get("duration_s"),
            "explicit": saved is not None,
        }
        if saved and e["default_threshold"] is not None and saved.get("threshold") is not None:
            try:
                entry["threshold"] = float(saved["threshold"])
            except (TypeError, ValueError):
                pass
        if saved and e.get("duration_s") is not None and saved.get("duration_s") is not None:
            try:
                entry["duration_s"] = int(saved["duration_s"])
            except (TypeError, ValueError):
                pass
        out[e["type"]] = entry
    return out


def validate_and_merge(org_id: str, module: str, patch: dict) -> dict:
    """Validate a PATCH body {type: {enabled?, threshold?, duration_s?}} against
    the catalog (module-gated) and return the JSON to store (existing saved
    values preserved for untouched types). Raises ValueError on bad input."""
    raw, exists = _load_raw(org_id)
    if not exists:
        raise LookupError("organizations.log_settings is missing — run migration 043_org_log_settings.sql")
    merged = dict(raw or {})
    allowed = {e["type"] for e in catalog_for_module(module)}
    for t, v in (patch or {}).items():
        if t not in allowed:
            raise ValueError(f"'{t}' is not a loggable event type for this organization.")
        if not isinstance(v, dict):
            raise ValueError(f"Settings for '{t}' must be an object.")
        e = CATALOG_BY_TYPE[t]
        cur = dict(merged.get(t) or {})
        if "enabled" in v:
            cur["enabled"] = bool(v["enabled"])
        if "threshold" in v and e["default_threshold"] is not None:
            try:
                th = float(v["threshold"])
            except (TypeError, ValueError):
                raise ValueError(f"'{t}' threshold must be a number.")
            if not (e["min"] <= th <= e["max"]):
                raise ValueError(f"'{t}' threshold must be between {e['min']} and {e['max']} {e['unit']}.")
            cur["threshold"] = th
        if "duration_s" in v and e.get("duration_s") is not None:
            try:
                d = int(v["duration_s"])
            except (TypeError, ValueError):
                raise ValueError(f"'{t}' duration must be a whole number of seconds.")
            if not (e["duration_min_s"] <= d <= e["duration_max_s"]):
                raise ValueError(f"'{t}' duration must be between {e['duration_min_s']} and {e['duration_max_s']} seconds.")
            cur["duration_s"] = d
        cur.setdefault("enabled", e["default_enabled"])
        if e["default_threshold"] is not None:
            cur.setdefault("threshold", e["default_threshold"])
        if e.get("duration_s") is not None:
            cur.setdefault("duration_s", e["duration_s"])
        merged[t] = cur
    return merged
