"""Trip-lifecycle LOG POINTS + multi-signal END-REASON inference.

SHARED logic: one implementation for school and university orgs (no module
branching anywhere in this file). Log points are ordinary rows in `alerts`
(migration 045 adds the four types), so they appear in Logs / Alerts / History
/ the passenger track feeds like every other event, and are switchable per org
in Edit Logs (log_settings_logic catalog).

Events
  trip_started        at POST /trips/start; marker = first ACCEPTED fix (patched in)
  trip_ended          at End Trip / cancel / deactivate / stale auto-close, with HOW
  connection_lost     sweeper: no ping ARRIVED for > grace (default 90 s); marker =
                      last accepted fix
  connection_restored ping path: pings resumed after an open episode; meta carries
                      the gap and how many buffered fixes came back with their
                      original timestamps

End reason (never a guess dressed as a fact — "uncertain" is a valid answer)
  normal               driver pressed End Trip
  cancelled            manager cancelled (or deleted the assignment)
  driver_deactivated   manager deactivated the driver mid-trip
  app_closed           the app's graceful "closed" beacon arrived and nothing came after it
  network_lost_resumed the trip was auto-closed after a silence but data RESUMED
                       (buffered fixes / a heartbeat came back) → it was the network
  device_power         silence never resumed and the last battery report was low
  uncertain            silence never resumed, no better evidence

Signals (migration 046, all optional): trips.last_signal_at / last_heartbeat_at /
last_battery / last_net_state / app_closed_at / conn_lost_at / end_reason,
trip_heartbeats, location_pings.battery/net_state, alerts.meta. Every column is
probed once; when one is missing the code just proceeds without that signal.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import gps_filter
from capacity_logic import LOCAL_TZ
from database import supabase
from log_settings_logic import effective_log_settings

log = logging.getLogger("trip_lifecycle")

EVENT_TYPES = ("trip_started", "trip_ended", "connection_lost", "connection_restored")
END_REASONS = ("normal", "cancelled", "driver_deactivated", "app_closed", "network_lost_resumed", "device_power", "uncertain")
INFERRED_REASONS = ("app_closed", "device_power", "uncertain")  # can be revised to network_lost_resumed

DEFAULT_CONN_LOST_GRACE_S = 90     # Edit Logs: connection_lost.threshold (seconds)
DEFAULT_STALE_CLOSE_MIN = 30       # Edit Logs: trip_ended.threshold (minutes without ANY signal)
PRE_MIGRATION_STALE_CLOSE_H = 6    # without 046 there are no heartbeats: only close very old silences
LOW_BATTERY_PCT = 15
SWEEP_INTERVAL_S = 30
PLACE_MAX_M = 300                  # a fix within this of a route stop is "at <stop>"
TRIP_LIFECYCLE_COLS = "last_signal_at, last_heartbeat_at, last_battery, last_net_state, app_closed_at, conn_lost_at, end_reason, end_detail"


# ── schema probes (cached; re-checked every few minutes so a migration applied
#    while the server runs is picked up without a restart) ──────────────────
_probe: dict = {}
_PROBE_TTL_S = 300


def _cached(key: str, fn) -> bool:
    hit = _probe.get(key)
    if hit and time.time() - hit[1] < _PROBE_TTL_S:
        return hit[0]
    try:
        ok = bool(fn())
    except Exception:
        ok = False
    _probe[key] = (ok, time.time())
    return ok


def has_col(table: str, col: str) -> bool:
    def _f():
        supabase.table(table).select(col).limit(1).execute()
        return True
    return _cached(f"{table}.{col}", _f)


def has_table(table: str) -> bool:
    def _f():
        supabase.table(table).select("id").limit(1).execute()
        return True
    return _cached(f"table:{table}", _f)


def lifecycle_types_available() -> bool:
    """True once migration 045 added the four enum values."""
    def _f():
        supabase.table("alerts").select("id").eq("type", "trip_started").limit(1).execute()
        return True
    return _cached("enum:lifecycle", _f)


# ── time / text helpers ───────────────────────────────────────────────────────
_AR_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(v) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        return gps_filter.parse_dt(v)
    except Exception:
        return None


def fmt_time_en(dt: Optional[datetime]) -> str:
    if not dt:
        return "—"
    return dt.astimezone(LOCAL_TZ).strftime("%I:%M %p").lstrip("0")


def fmt_time_ar(dt: Optional[datetime]) -> str:
    if not dt:
        return "—"
    local = dt.astimezone(LOCAL_TZ)
    hm = local.strftime("%I:%M").lstrip("0").translate(_AR_DIGITS)
    return f"{hm} {'ص' if local.hour < 12 else 'م'}"


def ar_num(n) -> str:
    return str(n).translate(_AR_DIGITS)


def ar_minutes(n: int) -> str:
    if n == 1:
        return "دقيقة واحدة"
    if n == 2:
        return "دقيقتين"
    if 3 <= n <= 10:
        return f"{ar_num(n)} دقايق"
    return f"{ar_num(n)} دقيقة"


def en_minutes(n: int) -> str:
    return "1 min" if n == 1 else f"{n} min"


def nearest_stop_name(route_id: Optional[str], lat, lng) -> Optional[str]:
    """Place name for a marker = the route stop within PLACE_MAX_M, if any.
    (No server-side reverse geocoding exists; stops are the names drivers,
    parents and managers already know.)"""
    if not route_id or lat is None or lng is None:
        return None
    try:
        stops = supabase.table("route_stops").select("name, lat, lng").eq("route_id", route_id).execute().data
    except Exception:
        return None
    best, best_d = None, PLACE_MAX_M
    for s in stops:
        try:
            d = gps_filter.haversine_m(float(lat), float(lng), float(s["lat"]), float(s["lng"]))
        except Exception:
            continue
        if d <= best_d:
            best, best_d = s.get("name"), d
    return best


# ── bilingual messages ───────────────────────────────────────────────────────
def build_messages(type_: str, p: dict) -> tuple[str, str]:
    """(message_en, message_ar) for a log point. `p` = params (time, place,
    gap_min, buffered, reason, battery, last_signal)."""
    t = parse_dt(p.get("time"))
    place = p.get("place")
    at_en = f" at {place}" if place else ""
    at_ar = f" عند {place}" if place else ""
    if type_ == "trip_started":
        return (f"Trip started {fmt_time_en(t)}{at_en}", f"الرحلة بدأت {fmt_time_ar(t)}{at_ar}")
    if type_ == "connection_lost":
        near_en = f" near {place}" if place else ""
        near_ar = f" قرب {place}" if place else ""
        alive = p.get("app_alive")
        tail_en = " — app still alive, GPS not reporting" if alive else ""
        tail_ar = " — التطبيق شغّال لكن الـGPS لا يرسل" if alive else ""
        return (f"Connection lost {fmt_time_en(t)} (last known fix{near_en}){tail_en}",
                f"انقطع الاتصال {fmt_time_ar(t)} (آخر موقع معروف{near_ar}){tail_ar}")
    if type_ == "connection_restored":
        gap = int(p.get("gap_min") or 0)
        buffered = int(p.get("buffered") or 0)
        en = f"Connection restored {fmt_time_en(t)} after {en_minutes(gap)} offline"
        ar = f"عاد الاتصال {fmt_time_ar(t)} بعد انقطاع {ar_minutes(gap)}"
        if buffered:
            en += f" — {buffered} buffered {'fix' if buffered == 1 else 'fixes'} uploaded with {'its' if buffered == 1 else 'their'} original {'timestamp' if buffered == 1 else 'timestamps'}"
            ar += f" — تم رفع {ar_num(buffered)} نقطة مخزّنة بتوقيتها الأصلي"
        return (en, ar)
    if type_ == "trip_ended":
        r = p.get("reason") or "uncertain"
        ls = parse_dt(p.get("last_signal"))
        batt = p.get("battery")
        if r == "normal":
            return (f"Trip ended normally {fmt_time_en(t)}{at_en} — driver pressed End Trip",
                    f"انتهت الرحلة {fmt_time_ar(t)}{at_ar}: أنهى السائق الرحلة")
        if r == "cancelled":
            return (f"Trip cancelled by the manager {fmt_time_en(t)}", f"تم إلغاء الرحلة من الإدارة {fmt_time_ar(t)}")
        if r == "driver_deactivated":
            return (f"Trip ended {fmt_time_en(t)}: the driver's account was deactivated",
                    f"انتهت الرحلة {fmt_time_ar(t)}: تم إيقاف حساب السائق")
        if r == "app_closed":
            return (f"Trip ended: driver closed the app (last signal {fmt_time_en(ls)}{at_en})",
                    f"انتهت الرحلة: أغلق السائق التطبيق (آخر إشارة {fmt_time_ar(ls)}{at_ar})")
        if r == "network_lost_resumed":
            gap = int(p.get("gap_min") or 0)
            return (f"Network connection was lost ({en_minutes(gap)}) then resumed — the trip was closed at {fmt_time_en(ls)}",
                    f"انقطع الاتصال بالشبكة ({ar_minutes(gap)}) ثم عاد — أُغلقت الرحلة {fmt_time_ar(ls)}")
        if r == "device_power":
            b_en = f", last battery {batt}%" if batt is not None else ""
            b_ar = f"، آخر شحن {ar_num(batt)}٪" if batt is not None else ""
            return (f"Connection lost — device likely powered off / battery (last signal {fmt_time_en(ls)}{b_en})",
                    f"انقطع الاتصال — غالبًا انطفأ الجهاز أو نفدت البطارية (آخر إشارة {fmt_time_ar(ls)}{b_ar})")
        return (f"Connection lost — cause uncertain (no signal since {fmt_time_en(ls)}{at_en})",
                f"انقطع الاتصال — السبب غير مؤكد (لا توجد إشارة منذ {fmt_time_ar(ls)}{at_ar})")
    return (type_, type_)


# ── low-level persistence (never raises) ─────────────────────────────────────
def update_trip(trip_id: str, fields: dict) -> None:
    fields = {k: v for k, v in fields.items() if has_col("trips", k)}
    if not fields:
        return
    try:
        supabase.table("trips").update(fields).eq("id", trip_id).execute()
    except Exception as exc:
        log.warning("update_trip %s failed: %s", trip_id, str(exc)[:120])


def load_signals(trip_id: str) -> dict:
    """The trip's lifecycle columns (empty dict when 046 is not applied)."""
    if not has_col("trips", "last_signal_at"):
        return {}
    try:
        rows = supabase.table("trips").select(TRIP_LIFECYCLE_COLS).eq("id", trip_id).limit(1).execute().data
        return rows[0] if rows else {}
    except Exception:
        return {}


def _enabled(org_id: str, type_: str) -> bool:
    try:
        return bool(effective_log_settings(org_id).get(type_, {}).get("enabled", True))
    except Exception:
        return True


def org_thresholds(org_id: str) -> tuple[int, int]:
    """(connection-lost grace seconds, stale auto-close minutes) for the org."""
    grace, stale = DEFAULT_CONN_LOST_GRACE_S, DEFAULT_STALE_CLOSE_MIN
    try:
        s = effective_log_settings(org_id)
        grace = int(s.get("connection_lost", {}).get("threshold") or grace)
        stale = int(s.get("trip_ended", {}).get("threshold") or stale)
    except Exception:
        pass
    return grace, stale


def log_event(trip: dict, type_: str, *, occurred_at: datetime, lat=None, lng=None, params: Optional[dict] = None) -> Optional[dict]:
    """Insert one log point (an `alerts` row). Honours Edit Logs, needs 045.
    Deduped on (trip, type, occurred_at). Returns the row or None. Never raises."""
    try:
        if type_ not in EVENT_TYPES or not lifecycle_types_available() or not _enabled(trip["org_id"], type_):
            return None
        p = dict(params or {})
        p.setdefault("time", occurred_at.isoformat())
        if p.get("place") is None and lat is not None and lng is not None:
            p["place"] = nearest_stop_name(trip.get("route_id"), lat, lng)
        en, ar = build_messages(type_, p)
        row = {"org_id": trip["org_id"], "trip_id": trip["id"], "driver_id": trip.get("driver_id"), "type": type_,
               "lat": lat, "lng": lng, "detail": en, "occurred_at": occurred_at.isoformat()}
        if has_col("alerts", "meta"):
            row["meta"] = {"event": type_, **{k: v for k, v in p.items() if k != "time"}, "message_en": en, "message_ar": ar}
        dup = (
            supabase.table("alerts").select("id").eq("trip_id", trip["id"]).eq("type", type_)
            .eq("occurred_at", occurred_at.isoformat()).limit(1).execute().data
        )
        if dup:
            return dup[0]
        res = supabase.table("alerts").insert(row).execute()
        return res.data[0] if res.data else row
    except Exception as exc:
        log.warning("log_event %s/%s failed: %s", trip.get("id"), type_, str(exc)[:160])
        return None


def _update_event(alert_id: str, fields: dict) -> None:
    if "meta" in fields and not has_col("alerts", "meta"):
        fields = {k: v for k, v in fields.items() if k != "meta"}
    try:
        supabase.table("alerts").update(fields).eq("id", alert_id).execute()
    except Exception as exc:
        log.warning("update event %s failed: %s", alert_id, str(exc)[:120])


def _find_event(trip_id: str, type_: str) -> Optional[dict]:
    try:
        rows = (
            supabase.table("alerts").select("id, lat, lng, occurred_at, detail" + (", meta" if has_col("alerts", "meta") else ""))
            .eq("trip_id", trip_id).eq("type", type_).order("occurred_at", desc=True).limit(1).execute().data
        )
        return rows[0] if rows else None
    except Exception:
        return None


# ── signals from the phone ───────────────────────────────────────────────────
def record_signal(trip_id: str, *, at: Optional[datetime] = None, battery=None, net_state=None,
                  heartbeat: bool = False, app_state: Optional[str] = None) -> None:
    at = at or now_utc()
    fields: dict = {"last_signal_at": at.isoformat()}
    if heartbeat:
        fields["last_heartbeat_at"] = at.isoformat()
    if battery is not None:
        fields["last_battery"] = int(battery)
    if net_state:
        fields["last_net_state"] = str(net_state)[:16]
    if app_state == "detached":
        fields["app_closed_at"] = at.isoformat()
    elif app_state == "resumed":
        fields["app_closed_at"] = None
    update_trip(trip_id, fields)


def store_heartbeats(trip: dict, beats: list) -> int:
    """Append heartbeats (deduped on trip+sent_at). Returns rows stored (0 pre-046)."""
    if not beats or not has_table("trip_heartbeats"):
        return 0
    rows = [{"trip_id": trip["id"], "org_id": trip["org_id"], "driver_id": trip.get("driver_id"),
             "sent_at": b["sent_at"].isoformat(), "battery": b.get("battery"), "net_state": b.get("net_state")} for b in beats]
    try:
        return len(supabase.table("trip_heartbeats").upsert(rows, on_conflict="trip_id,sent_at", ignore_duplicates=True).execute().data)
    except Exception:
        stored = 0
        for r in rows:
            try:
                stored += len(supabase.table("trip_heartbeats").insert(r).execute().data)
            except Exception:
                pass
        return stored


# ── lifecycle hooks ──────────────────────────────────────────────────────────
def on_trip_started(trip: dict) -> None:
    started = parse_dt(trip.get("started_at")) or now_utc()
    record_signal(trip["id"], at=started)
    log_event(trip, "trip_started", occurred_at=started)


def on_first_fix(trip: dict, lat, lng, recorded_dt: datetime) -> None:
    """Give the trip_started log point its marker: the first ACCEPTED fix."""
    ev = _find_event(trip["id"], "trip_started")
    if not ev or ev.get("lat") is not None:
        return
    place = nearest_stop_name(trip.get("route_id"), lat, lng)
    p = {"time": ev.get("occurred_at"), "place": place, "first_fix_at": recorded_dt.isoformat()}
    en, ar = build_messages("trip_started", p)
    fields = {"lat": lat, "lng": lng, "detail": en}
    meta = ev.get("meta") if isinstance(ev.get("meta"), dict) else {}
    fields["meta"] = {**meta, "place": place, "first_fix_at": recorded_dt.isoformat(), "message_en": en, "message_ar": ar}
    _update_event(ev["id"], fields)


def _open_episode(trip_id: str, sig: dict) -> Optional[datetime]:
    """Start of an OPEN connection-loss episode, or None. Uses trips.conn_lost_at
    (046); before that, the newest connection_lost vs connection_restored rows."""
    if has_col("trips", "conn_lost_at"):
        return parse_dt(sig.get("conn_lost_at"))
    lost = _find_event(trip_id, "connection_lost")
    if not lost:
        return None
    restored = _find_event(trip_id, "connection_restored")
    if restored and parse_dt(restored["occurred_at"]) >= parse_dt(lost["occurred_at"]):
        return None
    return parse_dt(lost["occurred_at"])


def on_pings_arrived(trip: dict, accepted: list, *, now: datetime, battery=None, net_state=None, first_fix: bool = False) -> Optional[dict]:
    """Ping path hook (after the batch is stored). Closes an open connection-loss
    episode (connection_restored), patches the trip_started marker, records the
    phone's state. Returns the restored event (if any). Never raises."""
    try:
        sig = load_signals(trip["id"])
        restored = None
        lost_at = _open_episode(trip["id"], sig)
        if lost_at is not None and accepted:
            grace, _ = org_thresholds(trip["org_id"])
            gap_min = max(1, int(round((now - lost_at).total_seconds() / 60)))
            buffered = sum(1 for n in accepted if (now - n["recorded_dt"]).total_seconds() > grace)
            first = accepted[0]
            restored = log_event(trip, "connection_restored", occurred_at=now, lat=first["lat"], lng=first["lng"],
                                 params={"gap_min": gap_min, "buffered": buffered, "lost_at": lost_at.isoformat(),
                                         "original_timestamps": buffered > 0})
            update_trip(trip["id"], {"conn_lost_at": None})
        record_signal(trip["id"], at=now, battery=battery, net_state=net_state)
        if first_fix and accepted:
            f = accepted[0]
            on_first_fix(trip, f["lat"], f["lng"], f["recorded_dt"])
        return restored
    except Exception as exc:
        log.warning("on_pings_arrived failed: %s", str(exc)[:160])
        return None


def last_fix(trip_id: str) -> Optional[dict]:
    """Newest ACCEPTED fix of the trip ({lat, lng, recorded_at}) or None."""
    try:
        return gps_filter.latest_position(supabase, trip_id)
    except Exception:
        return None


def on_trip_ended(trip: dict, reason: str, *, at: Optional[datetime] = None, detail: Optional[dict] = None,
                  gap_min: Optional[int] = None) -> None:
    """Record HOW the trip ended (trips.end_reason) + the trip_ended log point at
    the last accepted fix. `at` = the trip's ended_at."""
    at = at or now_utc()
    sig = load_signals(trip["id"])
    fields: dict = {"end_reason": reason}
    if detail is not None:
        fields["end_detail"] = detail
    update_trip(trip["id"], fields)
    fx = last_fix(trip["id"]) or {}
    params = {"reason": reason, "last_signal": (sig.get("last_signal_at") or fx.get("recorded_at") or at.isoformat()),
              "battery": sig.get("last_battery"), "net_state": sig.get("last_net_state"), "gap_min": gap_min}
    log_event(trip, "trip_ended", occurred_at=at, lat=fx.get("lat"), lng=fx.get("lng"), params=params)


def infer_end_reason(sig: dict, last_signal_at: Optional[datetime]) -> tuple[str, dict]:
    """Multi-signal inference for a trip that went silent and never came back."""
    closed = parse_dt(sig.get("app_closed_at"))
    batt = sig.get("last_battery")
    evidence = {"last_signal_at": last_signal_at.isoformat() if last_signal_at else None,
                "app_closed_at": closed.isoformat() if closed else None,
                "last_battery": batt, "last_net_state": sig.get("last_net_state"),
                "last_heartbeat_at": sig.get("last_heartbeat_at")}
    # The graceful "closed" beacon counts only if nothing arrived AFTER it.
    if closed and (last_signal_at is None or closed >= last_signal_at - timedelta(seconds=60)):
        return "app_closed", evidence
    if batt is not None and int(batt) <= LOW_BATTERY_PCT:
        return "device_power", evidence
    return "uncertain", evidence


def on_signal_after_close(trip: dict, *, kind: str, at: datetime) -> bool:
    """A completed trip whose end was INFERRED just got a signal (late backlog,
    heartbeat, beacon): the silence was a network outage. Revise the reason and
    rewrite the trip_ended log point. Returns True if revised."""
    try:
        sig = load_signals(trip["id"])
        reason = sig.get("end_reason")
        if reason not in INFERRED_REASONS:
            return False
        # A graceful "closed" beacon is strong evidence; only a BACKLOG of fixes
        # recorded during the silence (the phone kept tracking, the network did
        # not) overrides it. A mere later heartbeat/beacon (driver reopened the
        # app) does not turn "app closed" into "network lost".
        if reason == "app_closed" and kind != "backlog":
            return False
        ended = parse_dt(trip.get("ended_at")) or at
        gap_min = max(1, int(round((at - ended).total_seconds() / 60)))
        detail = dict(sig.get("end_detail") or {})
        detail.update({"revised_from": sig.get("end_reason"), "resumed_at": at.isoformat(), "resumed_by": kind, "gap_min": gap_min})
        update_trip(trip["id"], {"end_reason": "network_lost_resumed", "end_detail": detail})
        ev = _find_event(trip["id"], "trip_ended")
        if ev:
            p = {"time": ev.get("occurred_at"), "reason": "network_lost_resumed", "last_signal": ended.isoformat(), "gap_min": gap_min}
            en, ar = build_messages("trip_ended", p)
            meta = ev.get("meta") if isinstance(ev.get("meta"), dict) else {}
            _update_event(ev["id"], {"detail": en, "meta": {**meta, "reason": "network_lost_resumed", "gap_min": gap_min,
                                                              "resumed_by": kind, "message_en": en, "message_ar": ar}})
        return True
    except Exception as exc:
        log.warning("on_signal_after_close failed: %s", str(exc)[:160])
        return False


# ── the sweeper: connection lost + stale auto-close ──────────────────────────
def _last_arrival(trip_id: str) -> Optional[dict]:
    """Newest ping by ARRIVAL time (created_at), any quality."""
    try:
        rows = (
            supabase.table("location_pings").select("recorded_at, created_at, lat, lng")
            .eq("trip_id", trip_id).order("created_at", desc=True).limit(1).execute().data
        )
        return rows[0] if rows else None
    except Exception:
        return None


def _auto_close(trip: dict, sig: dict, last_signal_at: datetime, now: datetime) -> None:
    reason, evidence = infer_end_reason(sig, last_signal_at)
    evidence["auto_closed_at"] = now.isoformat()
    ended_at = last_signal_at  # honest: the trip effectively ended when the phone went silent
    try:
        upd = (
            supabase.table("trips").update({"status": "completed", "ended_at": ended_at.isoformat()})
            .eq("id", trip["id"]).eq("status", "active").execute()
        ).data
    except Exception as exc:
        log.warning("auto-close %s failed: %s", trip["id"], str(exc)[:120])
        return
    if not upd:
        return
    on_trip_ended({**trip, **upd[0]}, reason, at=ended_at, detail=evidence)
    try:  # school-only best-effort metrics, exactly as End Trip does
        from routers.trips import _compute_trip_performance
        _compute_trip_performance(upd[0])
    except Exception:
        pass
    log.info("auto-closed trip %s (%s) — silent since %s", trip["id"], reason, ended_at.isoformat())


def sweep_once(only_trip_ids: Optional[set] = None, now: Optional[datetime] = None) -> dict:
    """One pass over ACTIVE trips (all orgs). Returns counters. `only_trip_ids`
    scopes a pass to specific trips (tests); `now` overrides the clock (tests)."""
    out = {"checked": 0, "connection_lost": 0, "auto_closed": 0}
    now = now or now_utc()
    cols = "id, org_id, driver_id, route_id, started_at, status"
    full = has_col("trips", "last_signal_at")
    try:
        q = supabase.table("trips").select(cols + (", " + TRIP_LIFECYCLE_COLS if full else "")).eq("status", "active")
        if only_trip_ids:
            q = q.in_("id", list(only_trip_ids))
        trips = q.execute().data
    except Exception as exc:
        log.warning("sweep: could not list active trips: %s", str(exc)[:120])
        return out
    thresholds: dict = {}
    for trip in trips:
        out["checked"] += 1
        try:
            org = trip["org_id"]
            if org not in thresholds:
                thresholds[org] = org_thresholds(org)
            grace_s, stale_min = thresholds[org]
            sig = trip if full else {}
            started = parse_dt(trip.get("started_at")) or now
            arrival = _last_arrival(trip["id"])
            arrived_at = parse_dt(arrival["created_at"]) if arrival else None
            last_signal = max([d for d in (started, arrived_at, parse_dt(sig.get("last_signal_at")), parse_dt(sig.get("last_heartbeat_at"))) if d])

            # 1. connection lost: no ping ARRIVED for > grace, and no open episode.
            silent_since = arrived_at or started
            if (now - silent_since).total_seconds() > grace_s and _open_episode(trip["id"], sig) is None and lifecycle_types_available():
                fx = last_fix(trip["id"]) or {}
                hb = parse_dt(sig.get("last_heartbeat_at"))
                app_alive = bool(hb and (now - hb).total_seconds() <= grace_s * 2)
                occurred = parse_dt(fx.get("recorded_at")) or silent_since
                ev = log_event(trip, "connection_lost", occurred_at=occurred, lat=fx.get("lat"), lng=fx.get("lng"),
                               params={"app_alive": app_alive, "silent_since": silent_since.isoformat()})
                if ev:
                    update_trip(trip["id"], {"conn_lost_at": silent_since.isoformat()})
                    out["connection_lost"] += 1
                    if not has_col("trips", "conn_lost_at"):
                        pass  # pre-046: _open_episode reads the alert rows instead

            # 2. stale auto-close: no signal of ANY kind for > threshold.
            limit = timedelta(minutes=stale_min) if full else timedelta(hours=PRE_MIGRATION_STALE_CLOSE_H)
            if now - last_signal > limit:
                _auto_close(trip, sig, last_signal, now)
                out["auto_closed"] += 1
        except Exception as exc:
            log.warning("sweep: trip %s: %s", trip.get("id"), str(exc)[:160])
    return out


_sweeper_started = False


def start_sweeper() -> None:
    """Background thread; disable with LIFECYCLE_SWEEP=0 (e.g. in tests)."""
    global _sweeper_started
    if _sweeper_started or os.getenv("LIFECYCLE_SWEEP", "1") == "0":
        return
    _sweeper_started = True

    def _loop():
        time.sleep(5)
        while True:
            try:
                sweep_once()
            except Exception as exc:  # never let the loop die
                log.warning("sweep failed: %s", str(exc)[:160])
            time.sleep(SWEEP_INTERVAL_S)

    threading.Thread(target=_loop, name="trip-lifecycle-sweeper", daemon=True).start()
