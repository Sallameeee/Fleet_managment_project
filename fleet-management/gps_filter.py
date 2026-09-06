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
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Iterable, Optional

SPIKE_CEILING_KMH = 120.0   # a school/university bus cannot exceed this
MIN_DT_S = 1.0              # elapsed-time floor for the implied-speed division
REANCHOR_AFTER = 3          # consecutive impossible fixes -> the anchor was wrong

# Points closer than this with the same timestamp are the same fix.
_SAME_POINT_M = 0.5


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


def filter_fixes(points: Iterable[dict], *, anchor: Optional[dict] = None,
                 ceiling_kmh: float = SPIKE_CEILING_KMH):
    """Run THE RULE over chronologically ordered fixes.

    `points`: dicts with lat, lng and recorded_at (ISO) or recorded_dt.
    `anchor`: the last ACCEPTED fix from before this sequence (ingest passes
              the stored predecessor so a batch is judged against real history).
    Returns (accepted, rejected). Rejected entries carry `reject_reason`
    ('duplicate' | 'same_timestamp' | 'impossible_speed') and, for speed
    rejections, `implied_kmh`. Accepted entries are returned unchanged (plus
    recorded_dt) so callers can pass them straight through.
    """
    accepted: list = []
    rejected: list = []
    prev = _pt(anchor) if anchor else None
    last_seen = prev  # most recent fix processed, accepted OR rejected
    streak = 0
    for raw in points:
        p = _pt(raw)
        # Rule 1 first, against the last fix SEEN (even a rejected one): a re-sent
        # copy of a spike is still just a copy — it must not count as another
        # "impossible" fix or it would defeat the re-anchor logic.
        if last_seen is not None and (p["recorded_dt"] - last_seen["recorded_dt"]).total_seconds() <= 0                 and haversine_m(last_seen["lat"], last_seen["lng"], p["lat"], p["lng"]) <= _SAME_POINT_M:
            q = dict(p)
            q["reject_reason"] = "duplicate"
            q["implied_kmh"] = None
            rejected.append(q)
            continue
        last_seen = p
        if prev is None:
            accepted.append(p)
            prev = p
            continue
        dt = (p["recorded_dt"] - prev["recorded_dt"]).total_seconds()
        dist = haversine_m(prev["lat"], prev["lng"], p["lat"], p["lng"])
        if dt <= 0:
            # Same instant (or older than the anchor): duplicate resend, or two
            # providers disagreeing at one timestamp. Never two truths at once.
            q = dict(p)
            q["reject_reason"] = "duplicate" if dist <= _SAME_POINT_M else "same_timestamp"
            q["implied_kmh"] = None
            rejected.append(q)
            continue
        implied = dist / max(dt, MIN_DT_S) * 3.6
        if implied > ceiling_kmh:
            streak += 1
            if streak >= REANCHOR_AFTER:
                # Three fixes in a row disagree with the anchor -> the anchor was
                # the outlier. Accept and re-anchor here.
                p["reanchored"] = True
                accepted.append(p)
                prev = p
                streak = 0
                continue
            q = dict(p)
            q["reject_reason"] = "impossible_speed"
            q["implied_kmh"] = round(implied, 1)
            rejected.append(q)
            continue
        streak = 0
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
