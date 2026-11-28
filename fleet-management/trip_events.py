"""TRIP DECISION LOG — one row per decision the system makes during a trip.

SHARED by both modules. Every row carries the trip's org_id AND its module, and
readers (routers/trip_debug.py) only ever return rows of the caller's own org, so
a University trip's log can never surface in a School org or vice versa.

Writers
  server  routers/trips.py (ping batches, rejections + the values that triggered
          them, request validation failures, heartbeats, app-state beacons,
          stop progression decisions), trip_lifecycle.py (live-state transitions,
          lifecycle points), main.py (422s on the tracking endpoints).
  app     POST /trips/{id}/events — the driver app's own decisions (fix sampled,
          dropped by SpikeGate / duplicate gate, queued, sent, server response,
          permission + background state, app version/build), batched and
          size-capped exactly like pings.

Design rules
  * Logging NEVER fails or slows the tracking path: every write is best-effort,
    one bulk insert per request, and tolerant of migration 048 not being applied
    (probed once, re-probed every 5 min).
  * No secrets and no personal data beyond what the decision needs: payloads hold
    coordinates, timestamps, distances, speeds, counts, reasons and app build
    info — never tokens, names, phone numbers or emails (see `_scrub`).
  * Bounded: message <= 500 chars, payload <= PAYLOAD_MAX_BYTES (oversized
    payloads are replaced by a truncation marker), per-request row cap.
  * Retention: purge() deletes rows older than TRIP_EVENTS_RETENTION_DAYS
    (default 30), called by the lifecycle sweeper every 6 h.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Iterable, Optional

from database import supabase

log = logging.getLogger("trip_events")

PAYLOAD_MAX_BYTES = 4096
MESSAGE_MAX = 500
MAX_ROWS_PER_WRITE = 500
RETENTION_DAYS = max(1, int(os.getenv("TRIP_EVENTS_RETENTION_DAYS", "30") or 30))

# Keys that must never be persisted, whatever a caller passes.
_FORBIDDEN_KEYS = {"token", "access_token", "refresh_token", "authorization", "password",
                   "secret", "apikey", "api_key", "email", "phone", "name", "national_id"}

_probe = {"ok": None, "at": 0.0}
_PROBE_TTL_S = 300
_module_cache: dict = {}


def available() -> bool:
    """trip_events exists (migration 048)? Cached; re-probed every 5 minutes."""
    now = time.time()
    if _probe["ok"] is not None and now - _probe["at"] < _PROBE_TTL_S:
        return bool(_probe["ok"])
    try:
        supabase.table("trip_events").select("id").limit(1).execute()
        ok = True
    except Exception:
        ok = False
    _probe.update(ok=ok, at=now)
    return ok


def org_module(org_id: str) -> str:
    """The org's module, cached per process (an org never changes module)."""
    m = _module_cache.get(org_id)
    if m:
        return m
    m = "university"
    try:
        r = supabase.table("organizations").select("module").eq("id", org_id).limit(1).execute().data
        if r and r[0].get("module") in ("university", "school"):
            m = r[0]["module"]
    except Exception:
        return m  # don't cache a failure
    _module_cache[org_id] = m
    return m


def _iso(v) -> str:
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()
    return str(v)


def _scrub(obj, depth: int = 0):
    """Drop forbidden keys recursively; make datetimes JSON-safe."""
    if depth > 6:
        return None
    if isinstance(obj, dict):
        return {str(k): _scrub(v, depth + 1) for k, v in obj.items() if str(k).lower() not in _FORBIDDEN_KEYS}
    if isinstance(obj, (list, tuple)):
        return [_scrub(v, depth + 1) for v in obj[:200]]
    if isinstance(obj, datetime):
        return _iso(obj)
    if isinstance(obj, float):
        return round(obj, 7) if obj == obj and obj not in (float("inf"), float("-inf")) else None
    return obj


def _bounded_payload(payload: Optional[dict]) -> dict:
    p = _scrub(payload or {})
    raw = json.dumps(p, ensure_ascii=False, default=str)
    if len(raw.encode("utf-8")) <= PAYLOAD_MAX_BYTES:
        return p
    return {"truncated": True, "bytes": len(raw.encode("utf-8")), "keys": sorted(p.keys())[:40]}


def make(trip: dict, type_: str, message: str, payload: Optional[dict] = None, *,
         source: str = "server", occurred_at=None, client_event_id: Optional[str] = None) -> dict:
    """Build one row (does not write). `trip` needs id + org_id."""
    return {
        "trip_id": trip["id"],
        "org_id": trip["org_id"],
        "module": org_module(trip["org_id"]),
        "source": source if source in ("app", "server") else "server",
        "type": str(type_)[:48] or "unknown",
        "occurred_at": _iso(occurred_at or datetime.now(timezone.utc)),
        "message": (message or "")[:MESSAGE_MAX],
        "payload": _bounded_payload(payload),
        **({"client_event_id": str(client_event_id)[:64]} if client_event_id else {}),
    }


def write(rows: Iterable[dict]) -> int:
    """Best-effort bulk insert. Returns rows written (0 when 048 is missing or on
    any error — logging must never break tracking). App rows with a
    client_event_id are idempotent (re-sent batches are ignored)."""
    rows = [r for r in rows if r]
    if not rows or not available():
        return 0
    written = 0
    try:
        for i in range(0, len(rows), MAX_ROWS_PER_WRITE):
            chunk = rows[i:i + MAX_ROWS_PER_WRITE]
            with_client = [r for r in chunk if r.get("client_event_id")]
            plain = [r for r in chunk if not r.get("client_event_id")]
            if plain:
                written += len(supabase.table("trip_events").insert(plain).execute().data or [])
            if with_client:
                res = (supabase.table("trip_events")
                       .upsert(with_client, on_conflict="trip_id,client_event_id", ignore_duplicates=True)
                       .execute())
                written += len(res.data or [])
    except Exception as exc:
        log.warning("trip_events write failed: %s", str(exc)[:200])
    return written


def emit(trip: dict, type_: str, message: str, payload: Optional[dict] = None, **kw) -> int:
    """Build + write one server event."""
    return write([make(trip, type_, message, payload, **kw)])


def purge(days: Optional[int] = None) -> Optional[int]:
    """Delete events older than the retention window. None when unavailable."""
    if not available():
        return None
    try:
        res = supabase.rpc("purge_trip_events", {"days": int(days or RETENTION_DAYS)}).execute()
        return int(res.data) if res.data is not None else 0
    except Exception as exc:
        log.warning("trip_events purge failed: %s", str(exc)[:200])
        return None


def read(trip_id: str, org_id: str, *, limit: int = 20000) -> list:
    """All events of one trip of ONE org, chronological. Empty when unavailable."""
    if not available():
        return []
    out, off, page = [], 0, 1000
    while off < limit:
        rows = (supabase.table("trip_events")
                .select("id, trip_id, org_id, module, source, type, occurred_at, message, payload, created_at")
                .eq("trip_id", trip_id).eq("org_id", org_id)
                .order("occurred_at", desc=False).order("id", desc=False)
                .range(off, off + page - 1).execute().data) or []
        out += rows
        if len(rows) < page:
            break
        off += page
    return out
