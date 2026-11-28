"""GPS plausibility filter — the SINGLE source of truth for "is this fix real?".

Shared by both modules (school + university) and by every consumer of
`location_pings`:
  * ingest  (routers/trips.py post_pings)  — tags outliers / drops duplicates and
    keeps detection (speeding, off_route, long_stop, geofence) on clean fixes;
  * readers (history, trip pings, live/public/passenger position, reports,
    dashboard km) — re-apply the same rule at the read boundary, so the map is
    clean even for pings stored before the filter existed and before migration
    041 (outlier columns) is applied.
The driver app's tracking isolate carries an IDENTICAL copy of the rule in Dart
(tracking_service.dart `_SpikeGate`) for the live marker / arrival detection —
same constants, same decisions — because those run on the phone before the fix
ever reaches the server. Raw fixes are still buffered and uploaded unchanged.

THE RULE (physically impossible only — never "smoothing", never snapping):
  1. Exact duplicates (same timestamp AND same coordinates) are transport
     artefacts (a re-sent buffer row / a cached GPS fix) — dropped.
  2. Two different positions with the SAME timestamp cannot both be true:
     the first wins, the second is dropped.
  3. Implied speed = distance from the previous ACCEPTED fix / elapsed time,
     with the elapsed time floored at MIN_DT_S (guards divide-by-~zero: two
     fixes 0.2 s apart 24 m away are noise, not 359 km/h). If the implied
     speed is above SPIKE_CEILING_KMH the fix is impossible for a bus and is
     rejected. A single out-and-back spike therefore loses exactly ONE point:
     the return leg is measured from the last accepted (pre-spike) fix and is
     plausible again. Sustained fast driving (a highway at 100 km/h) stays
     under the ceiling and is never touched.
  4. Re-anchor: if REANCHOR_AFTER consecutive fixes are all "impossible"
     relative to the anchor, the ANCHOR was the bad point (or GPS restarted
     somewhere new) — the current fix is accepted and becomes the new anchor.
Original timestamps are never modified.

REAL-WORLD CORRECTIONS (2026-11-28, from the 24 Nov / September real-phone
forensics — see docs/tracking-fix-progress.md):
  * Accuracy allowance: two honest fixes can each be off by their reported
    accuracy, so the distance used for the implied speed is reduced by
    acc_prev + acc_cur (DEFAULT_ACCURACY_M each when the phone does not say,
    capped at MAX_ALLOWANCE_M each). The old rule turned +/-30 m of noise on a
    5 s cadence at 100 km/h into "impossible".
  * Ceiling 160 km/h (was 120): highway driving at 120-140 km/h is real; true
    spikes are hundreds to thousands of km/h and are still rejected.
  * Cached repeats: a phone re-sending the SAME coordinates with fresh
    timestamps (an emulator, or an app falling back to a last-known position)
    did not take a new fix.
    The repeat is accepted (it is where the phone believes it is), but the
    anchor keeps the time the position was FIRST seen, so the catch-up jump
    after a cached stretch is measured over the true elapsed time instead of one
    poll interval. Repeats are counted (`stale_repeat` flag) for the decision log.
  * low_accuracy: a fix whose reported accuracy is worse than LOW_ACCURACY_M is
    a network/cell estimate, not a GPS position — tagged, never drawn.
  * before_trip_start: a fix recorded more than BEFORE_START_TOLERANCE_S before
    the trip started is a cached last-known position (ingest only).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Iterable, Optional

SPIKE_CEILING_KMH = 160.0   # no bus/car on these roads sustains this; real spikes are far above
MIN_DT_S = 1.0              # elapsed-time floor for the implied-speed division
REANCHOR_AFTER = 3          # consecutive impossible fixes -> the anchor was wrong
DEFAULT_ACCURACY_M = 20.0   # assumed horizontal accuracy when the phone reports none
MAX_ALLOWANCE_M = 100.0     # per-fix cap on the accuracy allowance
LOW_ACCURACY_M = 150.0      # worse than this = network/cell estimate, not GPS
BEFORE_START_TOLERANCE_S = 120.0

# Points closer than this with the same timestamp are the same fix.
_SAME_POINT_M = 0.5
# Coordinates identical to this precision = the phone re-sent a cached fix.
_REPEAT_EPS_DEG = 1e-7


def _acc(p: dict) -> float:
    a = p.get("accuracy")
    if a is None or not isinstance(a, (int, float)) or a != a or a < 0:
        return DEFAULT_ACCURACY_M
    return min(float(a), MAX_ALLOWANCE_M)


def _same_coords(a: dict, b: dict) -> bool:
    return abs(a["lat"] - b["lat"]) <= _REPEAT_EPS_DEG and abs(a["lng"] - b["lng"]) <= _REPEAT_EPS_DEG


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def parse_dt(value) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _pt(row: dict) -> dict:
    """Internal shape: keeps the original row, adds a parsed recorded_dt."""
    if "recorded_dt" not in row:
        row = dict(row)
        row["recorded_dt"] = parse_dt(row["recorded_at"])
    return row


def _reject(p: dict, reason: str, **why) -> dict:
    q = dict(p)
    q["reject_reason"] = reason
    q["implied_kmh"] = why.pop("implied_kmh", None)
    q["why"] = why
    return q


def filter_fixes(points: Iterable[dict], *, anchor: Optional[dict] = None,
                 ceiling_kmh: float = SPIKE_CEILING_KMH, trip_started_at=None):
    """Run THE RULE over chronologically ordered fixes.

    `points`: dicts with lat, lng, recorded_at (ISO) or recorded_dt, optional
              accuracy (m).
    `anchor`: the last ACCEPTED fix from before this sequence (ingest passes
              the stored predecessor so a batch is judged against real history).
    `trip_started_at`: enables the before_trip_start tag (ingest only).
    Returns (accepted, rejected). Rejected entries carry `reject_reason`
    ('duplicate' | 'same_timestamp' | 'impossible_speed' | 'low_accuracy' |
    'before_trip_start'), `implied_kmh` for speed rejections, and `why` (the
    values that triggered the decision, for the trip decision log). Accepted
    entries are returned unchanged plus recorded_dt (and `stale_repeat` /
    `reanchored` flags) so callers can pass them straight through.
    """
    accepted: list = []
    rejected: list = []
    prev = _pt(anchor) if anchor else None
    if prev is not None:
        prev.setdefault("seen_dt", prev["recorded_dt"])
    last_seen = prev  # most recent fix processed, accepted OR rejected
    streak = 0
    start_dt = parse_dt(trip_started_at) if trip_started_at else None
    for raw in points:
        p = _pt(raw)
        # Rule 1 first, against the last fix SEEN (even a rejected one): a re-sent
        # copy of a spike is still just a copy — it must not count as another
        # "impossible" fix or it would defeat the re-anchor logic.
        if last_seen is not None and (p["recorded_dt"] - last_seen["recorded_dt"]).total_seconds() <= 0 \
                and haversine_m(last_seen["lat"], last_seen["lng"], p["lat"], p["lng"]) <= _SAME_POINT_M:
            rejected.append(_reject(p, "duplicate", same_as=str(last_seen.get("recorded_at"))))
            continue
        last_seen = p
        if start_dt is not None and (start_dt - p["recorded_dt"]).total_seconds() > BEFORE_START_TOLERANCE_S:
            rejected.append(_reject(p, "before_trip_start", trip_started_at=start_dt.isoformat(),
                                    age_before_start_s=round((start_dt - p["recorded_dt"]).total_seconds(), 1)))
            continue
        acc = p.get("accuracy")
        if isinstance(acc, (int, float)) and acc == acc and acc > LOW_ACCURACY_M:
            rejected.append(_reject(p, "low_accuracy", accuracy_m=round(float(acc), 1), limit_m=LOW_ACCURACY_M))
            continue
        if prev is None:
            p["seen_dt"] = p["recorded_dt"]
            accepted.append(p)
            prev = p
            continue
        dt = (p["recorded_dt"] - prev["recorded_dt"]).total_seconds()
        dist = haversine_m(prev["lat"], prev["lng"], p["lat"], p["lng"])
        if dt <= 0:
            # Same instant (or older than the anchor): duplicate resend, or two
            # providers disagreeing at one timestamp. Never two truths at once.
            rejected.append(_reject(p, "duplicate" if dist <= _SAME_POINT_M else "same_timestamp",
                                    anchor_at=str(prev.get("recorded_at")), dist_m=round(dist, 1)))
            continue
        if _same_coords(prev, p):
            # Cached fix re-sent with a fresh timestamp: keep the first-seen time.
            p["stale_repeat"] = True
            p["seen_dt"] = prev.get("seen_dt", prev["recorded_dt"])
            streak = 0
            accepted.append(p)
            prev = p
            continue
        ref_dt = (p["recorded_dt"] - prev.get("seen_dt", prev["recorded_dt"])).total_seconds()
        allowance = _acc(prev) + _acc(p)
        eff = max(0.0, dist - allowance)
        implied = eff / max(ref_dt, MIN_DT_S) * 3.6
        if implied > ceiling_kmh:
            streak += 1
            if streak >= REANCHOR_AFTER:
                # Three fixes in a row disagree with the anchor -> the anchor was
                # the outlier. Accept and re-anchor here.
                p["reanchored"] = True
                p["seen_dt"] = p["recorded_dt"]
                accepted.append(p)
                prev = p
                streak = 0
                continue
            rejected.append(_reject(p, "impossible_speed", implied_kmh=round(implied, 1), dist_m=round(dist, 1),
                                    allowance_m=round(allowance, 1), elapsed_s=round(ref_dt, 1),
                                    ceiling_kmh=ceiling_kmh, anchor_at=str(prev.get("recorded_at"))))
            continue
        streak = 0
        p["seen_dt"] = p["recorded_dt"]
        accepted.append(p)
        prev = p
    return accepted, rejected


def clean_pings(rows: list) -> list:
    """Reader-side cleanup for one trip: chronological order + THE RULE.
    Rows already tagged `is_outlier` (post-migration 041) are dropped up front;
    everything is re-checked anyway so old rows come out clean too."""
    live = [r for r in rows if not r.get("is_outlier")]
    live.sort(key=lambda r: (parse_dt(r["recorded_at"]), str(r.get("id", ""))))
    accepted, _ = filter_fixes(live)
    return accepted


def latest_position(supabase, trip_id: str, *, window: int = 40) -> Optional[dict]:
    """The most recent ACCEPTED fix of a trip (for live / public / parent maps),
    i.e. never a spike that jumped away and came back. Looks at the last
    `window` stored fixes only — cheap, and enough to judge the newest one.
    Tolerant of migration 041 not being applied (no is_outlier column)."""
    rows = select_pings_tolerant(
        lambda cols: supabase.table("location_pings")
        .select(cols)
        .eq("trip_id", trip_id)
        .order("recorded_at", desc=True)
        .limit(window)
    )
    if not rows:
        return None
    clean = clean_pings(rows)
    if not clean:
        return None
    p = clean[-1]
    return {"lat": p["lat"], "lng": p["lng"], "recorded_at": p["recorded_at"]}


def clean_grouped(rows: list) -> list:
    """Reader-side cleanup for a multi-trip ping list (reports / dashboard km):
    groups by trip_id, cleans each trip, returns trips in first-seen order with
    their fixes chronological — the shape those single-pass loops expect."""
    by_trip: dict = {}
    for r in rows:
        by_trip.setdefault(r["trip_id"], []).append(r)
    out: list = []
    for tid, lst in by_trip.items():
        out.extend(clean_pings(lst))
    return out


def _strip_missing_outlier_col(rows: list) -> list:
    # PostgREST returns the column only when it exists; nothing to do otherwise.
    return rows


def select_ping_columns(with_outlier: bool = True) -> str:
    """Column list for readers. `is_outlier` is included so tagged rows are
    skipped cheaply; callers that hit a pre-041 database fall back to the
    plain list (see `select_pings_tolerant`)."""
    base = "id, trip_id, lat, lng, speed, heading, recorded_at, created_at"
    return base + (", is_outlier" if with_outlier else "")


def select_pings_tolerant(query_builder):
    """Execute a location_pings query built by `query_builder(select_cols)`,
    retrying without the outlier column when migration 041 is not applied."""
    try:
        return query_builder(select_ping_columns(True)).execute().data
    except Exception as exc:
        if "is_outlier" not in str(exc):
            raise
        return query_builder(select_ping_columns(False)).execute().data


# ── route geometry helpers (shared by off-route detection) ──────────────────

def point_to_polyline_m(lat: float, lng: float, coords: list) -> Optional[float]:
    """Shortest distance (m) from a point to a GeoJSON LineString's coordinate
    list [[lng, lat], ...], using a local equirectangular projection (accurate
    to well under 1 % at route scale). None if the line has < 2 points."""
    if not coords or len(coords) < 2:
        return None
    k = 111_320.0
    cos_lat = math.cos(math.radians(lat))
    px, py = lng * k * cos_lat, lat * k
    best = float("inf")
    ax, ay = coords[0][0] * k * cos_lat, coords[0][1] * k
    for c in coords[1:]:
        bx, by = c[0] * k * cos_lat, c[1] * k
        dx, dy = bx - ax, by - ay
        len2 = dx * dx + dy * dy
        t = 0.0 if len2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / len2))
        cx, cy = ax + t * dx, ay + t * dy
        d = math.hypot(px - cx, py - cy)
        if d < best:
            best = d
        ax, ay = bx, by
    return best


def route_line_coords(geometry) -> Optional[list]:
    """Extract [[lng, lat], ...] from a routes.geometry value (LineString or
    Feature wrapping one). None when there is no usable line."""
    g = geometry
    if isinstance(g, dict) and g.get("type") == "Feature":
        g = g.get("geometry")
    if isinstance(g, dict) and g.get("type") == "LineString":
        coords = g.get("coordinates") or []
        return coords if len(coords) >= 2 else None
    return None


def resolve_naive_timestamp(dt: datetime, server_now: datetime, local_tz, tolerance) -> tuple:
    """A device timestamp WITHOUT an offset is ambiguous. The contract is UTC,
    but a build that serialised local time ("18:10:00" meaning 16:10Z in Cairo)
    would land hours "in the future" and be tagged future_timestamp. When the
    UTC reading is in the future beyond `tolerance` and the local reading is
    within `tolerance` of now, the local reading is the only plausible one.
    The offset comes from the tz database (DST-aware), never a constant.
    Returns (aware UTC datetime, 1 if the local reading was used else 0)."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc), 0
    as_utc = dt.replace(tzinfo=timezone.utc)
    as_local = dt.replace(tzinfo=local_tz).astimezone(timezone.utc)
    if as_utc > server_now + tolerance and abs((as_local - server_now).total_seconds()) <= tolerance.total_seconds():
        return as_local, 1
    return as_utc, 0
