"""STOP PROGRESSION — "which stop is the bus heading to?" — ONE rule, shared.

Used by the backend (routers/trips.py: stop visits + trip decision log + the
driver's GET /trips/{id}/progress) and mirrored 1:1 by the driver app
(shared/dart/stop_progress.dart; both are checked against the SAME golden
vectors in tests/stop_progress_vectors.json). Module-agnostic: no School /
University branching anywhere; each org's stops and settings stay its own.

Pure functions over a JSON-serialisable state, fed with ACCEPTED fixes in time
order (gps_filter already removed impossible / duplicate fixes):

    state = new_state()
    state, decisions = step(state, stops, fix, cfg)      # per accepted fix
    state, decisions = finalize(state, stops, at, cfg)   # trip ended
    view = summary(state, stops, cfg)                    # next stop / ETA / progress

THE RULE
  * DONE when the bus comes within ARRIVE radius (default 60 m) of the stop —
    measured as the closest approach along the segment between consecutive fixes,
    so a bus at 100 km/h sampled every 5 s (139 m apart) cannot jump over it.
      - stayed >= stop_dwell_s inside, or slowed to <= stopped_mps -> VISITED
        ("arrived at X")
      - drove through without stopping                           -> PASSED
        ("passed ~25 m from X without stopping")
  * DONE when the bus clearly went BY the stop without entering the radius:
    it is moving away from stop N (distance growing, `away_m` beyond its closest
    approach, for `away_fixes` consecutive fixes) AND it is beyond N toward N+1
    (closer to N+1 than N itself is). Closest approach <= pass radius -> PASSED
    ("passed ~120 m from X"), otherwise SKIPPED ("skipped X: closest 820 m, bus
    moved on toward Y").
  * Entering the radius of one of the next `lookahead` stops first closes the
    stops in between (passed / skipped by their closest approach) — but only
    when the bus is not still closing in on stop N (a later stop that lies on the
    road BEFORE N never steals the pointer).
  * Hysteresis: leaving a stop needs `exit_fixes` consecutive fixes beyond
    radius + exit_hysteresis_m, and a decided stop is never undone, so GPS drift
    at a stop cannot flip the pointer back and forth. The only change to a
    decided stop is an UPGRADE when the bus later really reaches it
    (skipped -> passed -> visited: doubling back, a stop passed twice).
  * The last stop is never skipped by movement (only by reaching it, or at the
    trip end), and stops left at the end are SKIPPED ("trip ended before X").
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

VERSION = 1

DEFAULTS = {
    "arrive_radius_m": 60.0,
    "exit_hysteresis_m": 40.0,
    "exit_fixes": 2,
    "pass_radius_m": 150.0,
    "away_m": 100.0,
    "away_fixes": 2,
    "stop_dwell_s": 20.0,
    "stopped_mps": 1.5,
    "lookahead": 2,
    "max_interp_gap_s": 60.0,
    "road_factor": 1.25,
    "min_eta_speed_mps": 5.0,
}


def config(overrides: Optional[dict] = None) -> dict:
    """Effective config. Only arrive_radius_m is org-configurable (25..300 m);
    the pass radius follows it (>= 2.5 x radius, never below 150 m)."""
    cfg = dict(DEFAULTS)
    r = (overrides or {}).get("arrive_radius_m")
    if isinstance(r, (int, float)) and not isinstance(r, bool):
        cfg["arrive_radius_m"] = float(min(300.0, max(25.0, r)))
    cfg["pass_radius_m"] = max(DEFAULTS["pass_radius_m"], 2.5 * cfg["arrive_radius_m"])
    return cfg


# ── geometry ─────────────────────────────────────────────────────────────────

def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def closest_on_segment(a: dict, b: dict, s: dict) -> tuple:
    """(distance_m, fraction 0..1) of the closest point to stop `s` on segment
    a->b, local equirectangular projection (sub-metre error at these scales)."""
    k = 111_320.0
    c = math.cos(math.radians(s["lat"]))
    ax, ay = (a["lng"] - s["lng"]) * k * c, (a["lat"] - s["lat"]) * k
    bx, by = (b["lng"] - s["lng"]) * k * c, (b["lat"] - s["lat"]) * k
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / l2))
    return math.hypot(ax + t * dx, ay + t * dy), t


def _parse(t) -> datetime:
    if isinstance(t, datetime):
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    d = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _r1(x):
    return None if x is None else round(float(x), 1)


# ── state ────────────────────────────────────────────────────────────────────

def new_state() -> dict:
    return {"v": VERSION, "k": 0, "stops": {}, "inside": None, "exit_count": 0,
            "away": {"count": 0, "last_d": None}, "revisit": None, "last_fix": None,
            "speed_ema": None, "fixes": 0}


def _rec(state: dict, stop: dict) -> dict:
    return state["stops"].setdefault(stop["id"], {
        "status": None, "closest_m": None, "closest_at": None, "entered_at": None,
        "last_inside_at": None, "min_speed": None, "arrived": False, "decided_at": None})


def _name(stop: dict) -> str:
    return stop.get("name") or f"stop {stop.get('stop_order')}"


def _decision(type_: str, stop: Optional[dict], at: datetime, message: str, **payload) -> dict:
    d = {"type": type_, "at": _iso(at), "message": message, "payload": payload}
    if stop is not None:
        d["stop_id"] = stop["id"]
        d["stop_order"] = stop.get("stop_order")
        d["stop_name"] = stop.get("name")
    return d


def _visit(stop: dict, status: str, rec: dict, arrival: Optional[str], departure: Optional[str] = None) -> dict:
    return {"type": "visit", "stop_id": stop["id"], "stop_order": stop.get("stop_order"), "status": status,
            "arrival_time": arrival, "departure_time": departure, "closest_m": _r1(rec.get("closest_m"))}


def _next_changed(stops: list, state: dict, at: datetime, why: str) -> dict:
    k = state["k"]
    nxt = stops[k] if k < len(stops) else None
    msg = (f"next stop is now {_name(nxt)} (#{nxt.get('stop_order')})" if nxt else "all stops done") + f" — {why}"
    return _decision("next_stop_changed", nxt, at, msg, next_index=k, reason=why)


def _close_stop(state: dict, stops: list, i: int, at: datetime, cfg: dict, why: str) -> list:
    """Decide stop i (not yet decided) from what we observed, advance pointer."""
    s = stops[i]
    rec = _rec(state, s)
    out = []
    closest = rec.get("closest_m")
    if rec.get("arrived"):
        rec["status"] = "visited"
        out.append(_decision("stop_departed", s, at, f"left {_name(s)}",
                             arrival=rec.get("entered_at"), departure=rec.get("last_inside_at"),
                             dwell_s=_dwell(rec)))
        out.append(_visit(s, "visited", rec, rec.get("entered_at"), rec.get("last_inside_at")))
    elif closest is not None and closest <= cfg["pass_radius_m"]:
        rec["status"] = "passed"
        out.append(_decision("stop_passed", s, at,
                             f"passed ~{round(closest)} m from {_name(s)} without stopping",
                             closest_m=_r1(closest), closest_at=rec.get("closest_at"), reason=why,
                             min_speed_mps=_r1(rec.get("min_speed"))))
        out.append(_visit(s, "passed", rec, rec.get("closest_at") or _iso(at)))
    else:
        rec["status"] = "skipped"
        far = f"closest approach {round(closest)} m" if closest is not None else "never approached"
        out.append(_decision("stop_skipped", s, at, f"skipped {_name(s)}: {far}, {why}",
                             closest_m=_r1(closest), closest_at=rec.get("closest_at"), reason=why))
        out.append(_visit(s, "skipped", rec, rec.get("closest_at") or _iso(at)))
    rec["decided_at"] = _iso(at)
    if state["inside"] == s["id"]:
        state["inside"] = None
    state["exit_count"] = 0
    state["away"] = {"count": 0, "last_d": None}
    if state["k"] == i:
        state["k"] = i + 1
    return out


def _dwell(rec: dict) -> Optional[float]:
    if rec.get("entered_at") and rec.get("last_inside_at"):
        return _r1((_parse(rec["last_inside_at"]) - _parse(rec["entered_at"])).total_seconds())
    return None


def _stopped(rec: dict, cfg: dict) -> bool:
    d = _dwell(rec)
    return (d is not None and d >= cfg["stop_dwell_s"]) or (
        rec.get("min_speed") is not None and rec["min_speed"] <= cfg["stopped_mps"])


# ── the step ─────────────────────────────────────────────────────────────────

def step(state: dict, stops: list, fix: dict, cfg: Optional[dict] = None) -> tuple:
    """Feed one ACCEPTED fix {lat, lng, recorded_at, speed?}. `stops` ordered by
    stop_order, each {id, stop_order, name, lat, lng}. Returns (state, decisions);
    decisions of type 'visit' are stop_visits upserts, the rest are log events."""
    cfg = cfg or config()
    out: list = []
    if not stops:
        return state, out
    t = _parse(fix["recorded_at"])
    p = {"lat": float(fix["lat"]), "lng": float(fix["lng"])}
    prev = state.get("last_fix")
    if prev is not None and t <= _parse(prev["t"]):
        return state, [_decision("fix_out_of_order", None, t, "fix older than the last one used — ignored for stop progress",
                                 fix_at=_iso(t), last_at=prev["t"])]
    dt = (t - _parse(prev["t"])).total_seconds() if prev else None
    seg_a = {"lat": prev["lat"], "lng": prev["lng"]} if prev and dt is not None and dt <= cfg["max_interp_gap_s"] else p
    speed = fix.get("speed")
    if speed is None or (isinstance(speed, float) and speed != speed) or speed < 0:
        speed = haversine_m(prev["lat"], prev["lng"], p["lat"], p["lng"]) / dt if prev and dt else None
    if speed is not None:
        ema = state.get("speed_ema")
        state["speed_ema"] = speed if ema is None else 0.7 * ema + 0.3 * speed
    state["last_fix"] = {"lat": p["lat"], "lng": p["lng"], "t": _iso(t), "speed": _r1(speed)}
    state["fixes"] = state.get("fixes", 0) + 1

    n = len(stops)
    k = state["k"]
    R = cfg["arrive_radius_m"]
    t_prev = _parse(prev["t"]) if prev else t

    def seg_closest(i):
        d, frac = closest_on_segment(seg_a, p, stops[i])
        at = t_prev + (t - t_prev) * frac if seg_a is not p else t
        return d, at

    # Track closest approach for the current + lookahead stops.
    seg_d, end_d = {}, {}
    for i in range(k, min(n, k + 1 + int(cfg["lookahead"]))):
        d, at = seg_closest(i)
        seg_d[i] = (d, at)
        end_d[i] = haversine_m(p["lat"], p["lng"], stops[i]["lat"], stops[i]["lng"])
        rec = _rec(state, stops[i])
        if rec["closest_m"] is None or d < rec["closest_m"]:
            rec["closest_m"], rec["closest_at"] = d, _iso(at)

    if k >= n:
        out += _revisits(state, stops, p, t, speed, cfg)
        return state, out

    inside = state.get("inside")
    if inside is not None and inside == stops[k]["id"]:
        rec = _rec(state, stops[k])
        # Stops close together: already inside the NEXT stop's radius and nearer to it.
        if k + 1 < n and end_d.get(k + 1, 1e18) <= R and end_d[k + 1] < end_d[k]:
            out += _close_stop(state, stops, k, t, cfg, f"reached the next stop {_name(stops[k + 1])} right after it")
            out.append(_next_changed(stops, state, t, "stops close together"))
            out += _enter(state, stops, k + 1, t, end_d[k + 1], speed, cfg)
            return state, out
        if end_d[k] <= R + cfg["exit_hysteresis_m"]:
            state["exit_count"] = 0
            if end_d[k] <= R:
                rec["last_inside_at"] = _iso(t)
                if speed is not None:
                    rec["min_speed"] = speed if rec["min_speed"] is None else min(rec["min_speed"], speed)
            if not rec["arrived"] and _stopped(rec, cfg):
                rec["arrived"] = True
                rec["status"] = "visited"
                out.append(_decision("stop_arrived", stops[k], t, f"arrived at {_name(stops[k])}",
                                     arrival=rec["entered_at"], distance_m=_r1(end_d[k]),
                                     dwell_s=_dwell(rec), min_speed_mps=_r1(rec.get("min_speed"))))
                out.append(_visit(stops[k], "visited", rec, rec["entered_at"]))
            return state, out
        state["exit_count"] = state.get("exit_count", 0) + 1
        if state["exit_count"] < int(cfg["exit_fixes"]):
            return state, out
        out += _close_stop(state, stops, k, t, cfg, "left the stop radius")
        out.append(_next_changed(stops, state, t, f"left {_name(stops[k])}"))
        return state, out

    # Not inside the current stop. Entering it, or one of the next stops? A later
    # stop only takes over when the bus already went by stop k or is moving away
    # from it — never while it is still closing in on k.
    target = None
    if seg_d[k][0] <= R:
        target = k
    else:
        moving_away = state["away"]["last_d"] is not None and end_d[k] > state["away"]["last_d"] + 5.0
        ck = _rec(state, stops[k])["closest_m"]
        went_by = ck is not None and ck <= cfg["pass_radius_m"]
        for j in range(k + 1, min(n, k + 1 + int(cfg["lookahead"]))):
            if seg_d[j][0] <= R and (went_by or moving_away) and end_d[j] < end_d[k]:
                target = j
                break
    if target is not None:
        for i in range(k, target):
            out += _close_stop(state, stops, i, t, cfg, f"bus reached {_name(stops[target])} first")
        if target > k:
            out.append(_next_changed(stops, state, t, f"reached {_name(stops[target])}"))
        state["k"] = target
        d_at = seg_d[target][1]
        out += _enter(state, stops, target, d_at, end_d[target], speed, cfg)
        # Jumped through the whole radius within one segment (highway).
        if state.get("inside") == stops[target]["id"] and end_d[target] > R + cfg["exit_hysteresis_m"]:
            out += _close_stop(state, stops, target, t, cfg, "drove through the stop radius between two fixes")
            out.append(_next_changed(stops, state, t, f"passed {_name(stops[target])}"))
        return state, out

    # Movement-based advance: moving away from stop k AND beyond it toward k+1.
    away = state["away"]
    if k + 1 < n:
        rec = _rec(state, stops[k])
        beyond = end_d.get(k + 1, haversine_m(p["lat"], p["lng"], stops[k + 1]["lat"], stops[k + 1]["lng"])) < \
            haversine_m(stops[k]["lat"], stops[k]["lng"], stops[k + 1]["lat"], stops[k + 1]["lng"])
        farther = away["last_d"] is None or end_d[k] >= away["last_d"] - 10.0
        if beyond and farther and rec["closest_m"] is not None and end_d[k] >= rec["closest_m"] + cfg["away_m"]:
            away["count"] += 1
        else:
            away["count"] = 0
        away["last_d"] = end_d[k]
        if away["count"] >= int(cfg["away_fixes"]):
            out += _close_stop(state, stops, k, t, cfg, f"bus moved on toward {_name(stops[k + 1])}")
            out.append(_next_changed(stops, state, t, f"moved past {_name(stops[k])}"))
            return state, out
    else:
        away["last_d"] = end_d[k]
    out += _revisits(state, stops, p, t, speed, cfg)
    return state, out


def _enter(state: dict, stops: list, i: int, at: datetime, d: float, speed, cfg: dict) -> list:
    s = stops[i]
    rec = _rec(state, s)
    state["inside"] = s["id"]
    state["exit_count"] = 0
    rec["entered_at"] = _iso(at)
    rec["last_inside_at"] = _iso(at)
    rec["min_speed"] = speed
    out = [_decision("stop_entered", s, at, f"entered {_name(s)} radius ({round(d)} m)",
                     distance_m=_r1(d), radius_m=cfg["arrive_radius_m"], speed_mps=_r1(speed))]
    if speed is not None and speed <= cfg["stopped_mps"]:
        rec["arrived"] = True
        rec["status"] = "visited"
        out.append(_decision("stop_arrived", s, at, f"arrived at {_name(s)}", arrival=rec["entered_at"],
                             distance_m=_r1(d), min_speed_mps=_r1(speed)))
        out.append(_visit(s, "visited", rec, rec["entered_at"]))
    return out


def _revisits(state: dict, stops: list, p: dict, t: datetime, speed, cfg: dict) -> list:
    """Earlier (decided) stops the bus is at again: upgrade skipped -> passed ->
    visited, log a re-entry once per visit. Never moves the pointer back."""
    out = []
    R = cfg["arrive_radius_m"]
    by_id = {s["id"]: s for s in stops}
    rv = state.get("revisit")
    if rv and rv["stop_id"] in by_id:
        # Still at the stop we came back to? (exit hysteresis, like a normal stop)
        s = by_id[rv["stop_id"]]
        rec = _rec(state, s)
        d = haversine_m(p["lat"], p["lng"], s["lat"], s["lng"])
        if d <= R + cfg["exit_hysteresis_m"]:
            if speed is not None:
                rv["min_speed"] = speed if rv.get("min_speed") is None else min(rv["min_speed"], speed)
            dwell = (t - _parse(rv["entered_at"])).total_seconds()
            if rec["status"] == "passed" and (dwell >= cfg["stop_dwell_s"] or
                                              (rv.get("min_speed") is not None and rv["min_speed"] <= cfg["stopped_mps"])):
                rec["status"] = "visited"
                out.append(_decision("stop_upgraded", s, t, f"stopped at {_name(s)} on a later pass — now visited",
                                     distance_m=_r1(d), was="passed", now="visited", dwell_s=_r1(dwell)))
                out.append(_visit(s, "visited", rec, rv["entered_at"], _iso(t)))
            return out
        state["revisit"] = None
    for i in range(min(state["k"], len(stops))):
        s = stops[i]
        rec = _rec(state, s)
        if rec["status"] is None:
            continue
        d = haversine_m(p["lat"], p["lng"], s["lat"], s["lng"])
        if d > R:
            continue
        state["revisit"] = {"stop_id": s["id"], "entered_at": _iso(t), "min_speed": speed}
        if rec["status"] == "skipped":
            rec["status"] = "passed"
            rec["closest_m"] = d if rec["closest_m"] is None else min(rec["closest_m"], d)
            out.append(_decision("stop_upgraded", s, t, f"reached {_name(s)} after it was skipped — now passed",
                                 distance_m=_r1(d), was="skipped", now="passed"))
            out.append(_visit(s, "passed", rec, _iso(t)))
        else:
            out.append(_decision("stop_reentered", s, t, f"back at {_name(s)} (already {rec['status']})",
                                 distance_m=_r1(d)))
        return out
    return out


def finalize(state: dict, stops: list, at, cfg: Optional[dict] = None) -> tuple:
    """Trip ended: close the stop the bus is at; every stop never reached is
    SKIPPED with its closest approach. Idempotent."""
    cfg = cfg or config()
    at = _parse(at)
    out = []
    while state["k"] < len(stops):
        i = state["k"]
        at_stop = state.get("inside") == stops[i]["id"]
        out += _close_stop(state, stops, i, at, cfg, "trip ended at this stop" if at_stop else "trip ended before reaching it")
    state["inside"] = None
    return state, out


def summary(state: dict, stops: list, cfg: Optional[dict] = None) -> dict:
    """Next stop, ETA and progress for the app / dashboard."""
    cfg = cfg or config()
    n = len(stops)
    k = min(state.get("k", 0), n)
    rows = []
    done = skipped = 0
    for s in stops:
        rec = state.get("stops", {}).get(s["id"], {})
        st = rec.get("status")
        if st in ("visited", "passed"):
            done += 1
        elif st == "skipped":
            skipped += 1
        rows.append({"stop_id": s["id"], "stop_order": s.get("stop_order"), "name": s.get("name"),
                     "status": st, "closest_m": _r1(rec.get("closest_m"))})
    nxt = None
    lf = state.get("last_fix")
    if k < n:
        s = stops[k]
        dist = haversine_m(lf["lat"], lf["lng"], s["lat"], s["lng"]) if lf else None
        sp = state.get("speed_ema")
        eta = None
        if dist is not None:
            eta = round(dist * cfg["road_factor"] / max(sp or 0.0, cfg["min_eta_speed_mps"]))
        nxt = {"stop_id": s["id"], "stop_order": s.get("stop_order"), "name": s.get("name"),
               "distance_m": _r1(dist), "eta_s": eta, "at_stop": state.get("inside") == s["id"]}
    return {"next_stop": nxt, "next_index": k, "total": n, "done": done, "skipped": skipped,
            "progress_pct": round(100.0 * (done + skipped) / n, 1) if n else 0.0,
            "stops": rows, "last_fix_at": lf["t"] if lf else None,
            "arrive_radius_m": cfg["arrive_radius_m"]}
