"""Server runtime for the shared stop progression (stop_progress.py).

Owns the per-trip state (trips.progress_state, migration 048) and turns the
pure decisions into side effects:
  * stop_visits upserts (status visited | passed | skipped, closest_m,
    decided_by='server') — NEVER downgrading a stop (visited > passed > skipped),
    so an app-reported arrival is never overwritten by a weaker server verdict;
  * trip decision log rows (stop_entered / stop_arrived / stop_passed /
    stop_skipped / stop_departed / next_stop_changed / stop_upgraded / ...).

Concurrency: two ping requests of one trip can run at once (a buffered flush
racing a live fix). The state is saved with an optimistic version check
(trips.progress_version); the loser reloads and re-applies only the fixes newer
than the winner's last fix. Fixes older than the state's last fix are ignored
for progression (the decision log says so) — the pings themselves are stored.

Module-agnostic: the same code for School and University; config comes from the
trip's own org (organizations.log_settings.stop_progress.arrive_radius_m).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import stop_progress as sp
import trip_events
import trip_lifecycle as lifecycle
from database import supabase

log = logging.getLogger("progress_runtime")

_RANK = {"skipped": 1, "passed": 2, "visited": 3}
MAX_RETRIES = 3


def available() -> bool:
    return lifecycle.has_col("trips", "progress_state") and lifecycle.has_col("stop_visits", "closest_m")


def load_stops(route_id: Optional[str]) -> list:
    if not route_id:
        return []
    rows = (supabase.table("route_stops").select("id, stop_order, name, lat, lng, dwell_minutes")
            .eq("route_id", route_id).order("stop_order", desc=False).execute().data) or []
    return [r for r in rows if r.get("lat") is not None and r.get("lng") is not None]


def org_config(org_id: str) -> dict:
    try:
        rows = supabase.table("organizations").select("log_settings").eq("id", org_id).limit(1).execute().data
        ls = (rows[0].get("log_settings") or {}) if rows else {}
        return sp.config(ls.get("stop_progress") if isinstance(ls, dict) else None)
    except Exception:
        return sp.config()


def _load_state(trip_id: str) -> tuple:
    rows = supabase.table("trips").select("progress_state, progress_version").eq("id", trip_id).limit(1).execute().data
    if not rows:
        return sp.new_state(), 0
    st = rows[0].get("progress_state")
    if not isinstance(st, dict) or st.get("v") != sp.VERSION:
        st = sp.new_state()
    return st, int(rows[0].get("progress_version") or 0)


def _save_state(trip_id: str, state: dict, version: int) -> bool:
    res = (supabase.table("trips").update({"progress_state": state, "progress_version": version + 1})
           .eq("id", trip_id).eq("progress_version", version).execute())
    return bool(res.data)


def _apply_visits(trip: dict, visits: list) -> int:
    """Upsert visit decisions, never downgrading an existing row."""
    if not visits:
        return 0
    existing = {}
    try:
        for r in (supabase.table("stop_visits").select("stop_id, status").eq("trip_id", trip["id"]).execute().data or []):
            existing[r["stop_id"]] = r.get("status") or "visited"
    except Exception:
        pass
    stops = {s["id"]: s for s in load_stops(trip.get("route_id"))}
    written = 0
    final: dict = {}
    for v in visits:  # the last decision per stop wins (arrival then departure)
        final[v["stop_id"]] = {**final.get(v["stop_id"], {}), **{k: x for k, x in v.items() if x is not None}}
    for sid, v in final.items():
        have = existing.get(sid)
        if have and _RANK.get(v["status"], 0) < _RANK.get(have, 0):
            continue
        st = stops.get(sid) or {}
        arrival = v.get("arrival_time")
        departure = v.get("departure_time") if v["status"] == "visited" else None
        dwell = None
        if arrival and departure:
            dwell = max(0, int((sp._parse(departure) - sp._parse(arrival)).total_seconds()))
        row = {"trip_id": trip["id"], "org_id": trip["org_id"], "stop_id": sid, "stop_order": v.get("stop_order"),
               "arrival_time": arrival or datetime.now(timezone.utc).isoformat(), "departure_time": departure,
               "planned_dwell_seconds": (st.get("dwell_minutes") or 0) * 60, "actual_dwell_seconds": dwell,
               "status": v["status"], "closest_m": v.get("closest_m"), "decided_by": "server"}
        try:
            supabase.table("stop_visits").upsert(row, on_conflict="trip_id,stop_id").execute()
            written += 1
        except Exception as exc:
            log.warning("stop_visits upsert %s/%s failed: %s", trip["id"], sid, str(exc)[:160])
    return written


def _events(trip: dict, decisions: list, cfg: dict) -> list:
    rows = []
    for d in decisions:
        if d["type"] == "visit":
            continue
        payload = {**d.get("payload", {}), "stop_id": d.get("stop_id"), "stop_order": d.get("stop_order"),
                   "arrive_radius_m": cfg["arrive_radius_m"], "pass_radius_m": cfg["pass_radius_m"]}
        rows.append(trip_events.make(trip, d["type"], d["message"], payload, occurred_at=d["at"]))
    return rows


def apply_fixes(trip: dict, accepted: list) -> Optional[dict]:
    """Run the progression over newly ACCEPTED fixes (chronological). Returns the
    summary (next stop / progress) or None when unavailable. Never raises."""
    if not accepted or not available():
        return None
    try:
        stops = load_stops(trip.get("route_id"))
        if not stops:
            return None
        cfg = org_config(trip["org_id"])
        fixes = [{"lat": a["lat"], "lng": a["lng"], "speed": a.get("speed"),
                  "recorded_at": a["recorded_dt"].isoformat() if a.get("recorded_dt") else a["recorded_at"]}
                 for a in accepted]
        for _ in range(MAX_RETRIES):
            state, version = _load_state(trip["id"])
            decisions: list = []
            for f in fixes:
                state, ds = sp.step(state, stops, f, cfg)
                decisions += ds
            if _save_state(trip["id"], state, version):
                _apply_visits(trip, [d for d in decisions if d["type"] == "visit"])
                ignored = [d for d in decisions if d["type"] == "fix_out_of_order"]
                rows = _events(trip, [d for d in decisions if d["type"] != "fix_out_of_order"], cfg)
                if ignored:
                    rows.append(trip_events.make(trip, "progress_fixes_ignored",
                                                 f"{len(ignored)} fix(es) older than the last one used were ignored for stop progress",
                                                 {"count": len(ignored), "first": ignored[0]["payload"]}))
                trip_events.write(rows)
                return sp.summary(state, stops, cfg)
        trip_events.emit(trip, "progress_conflict", "stop progress not updated: concurrent updates kept winning",
                         {"fixes": len(fixes)})
    except Exception as exc:
        log.warning("progress apply %s failed: %s", trip.get("id"), str(exc)[:200])
        trip_events.emit(trip, "progress_error", f"stop progress error: {str(exc)[:200]}", {})
    return None


def finalize(trip: dict, at: datetime) -> None:
    """Driver ended the trip: close the stop it is at, mark the rest skipped."""
    if not available():
        return
    try:
        stops = load_stops(trip.get("route_id"))
        if not stops:
            return
        cfg = org_config(trip["org_id"])
        for _ in range(MAX_RETRIES):
            state, version = _load_state(trip["id"])
            state, decisions = sp.finalize(state, stops, at, cfg)
            if _save_state(trip["id"], state, version):
                _apply_visits(trip, [d for d in decisions if d["type"] == "visit"])
                trip_events.write(_events(trip, decisions, cfg))
                return
    except Exception as exc:
        log.warning("progress finalize %s failed: %s", trip.get("id"), str(exc)[:200])


def view(trip: dict) -> dict:
    """Current progress summary for a trip (empty progression when unavailable)."""
    stops = load_stops(trip.get("route_id"))
    cfg = org_config(trip["org_id"])
    state = sp.new_state()
    if available():
        try:
            state, _ = _load_state(trip["id"])
        except Exception:
            pass
    return sp.summary(state, stops, cfg)
