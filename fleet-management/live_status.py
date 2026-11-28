"""LIVE STATE of an active trip — the SINGLE rule for "is this bus online?".

SHARED by both modules and by every reader (manager Full View, Drivers list,
dashboard counters, super-admin drivers, school all-buses map, parent/passenger
tracking) and by the lifecycle sweeper, which logs every transition to the trip
decision log with the inputs behind it.

    online          the app is alive (any signal: heartbeat, ping request, app-state
                    beacon within SIGNAL_FRESH) AND a recent ACCEPTED GPS fix exists
                    (recorded within FIX_FRESH)
    online_no_gps   the app is alive but has no recent accepted fix (GPS off / no
                    permission / indoors / every fix rejected). The 24 Nov trips
                    sat here for their whole run and were shown as OFFLINE.
    offline         no signal of any kind within SIGNAL_FRESH (network gone, phone
                    off, app killed)

Before this module "online" meant only "a ping recorded in the last 2 min", so a
phone that was online but whose fixes never reached the server was OFFLINE.

`online` (boolean, kept for every existing client) now means "the app is
connected" (online or online_no_gps); `gps_fresh` says whether the position is
current.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

SIGNAL_FRESH = timedelta(seconds=150)   # heartbeats are ~60 s apart: one may be late/lost
FIX_FRESH = timedelta(seconds=120)      # fixes are every few seconds while moving

ONLINE, ONLINE_NO_GPS, OFFLINE = "online", "online_no_gps", "offline"
STATES = (ONLINE, ONLINE_NO_GPS, OFFLINE)


def _dt(v) -> Optional[datetime]:
    if not v:
        return None
    if isinstance(v, datetime):
        d = v
    else:
        try:
            d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def compute(*, now: datetime, last_signal_at=None, last_heartbeat_at=None, last_fix_at=None,
            last_ping_arrival_at=None) -> dict:
    """Pure decision. All inputs are UTC instants (or ISO strings / None).
    `last_fix_at`  newest ACCEPTED fix's recorded_at (device time of the fix).
    `last_ping_arrival_at`  when the server last received a ping request (a
    signal even if its fixes were rejected or old)."""
    sig_candidates = [d for d in (_dt(last_signal_at), _dt(last_heartbeat_at), _dt(last_ping_arrival_at)) if d]
    last_sig = max(sig_candidates) if sig_candidates else None
    fix = _dt(last_fix_at)
    # A fresh fix is itself proof the app was alive when it was taken.
    if fix and (last_sig is None or fix > last_sig):
        last_sig = fix
    sig_age = (now - last_sig).total_seconds() if last_sig else None
    fix_age = (now - fix).total_seconds() if fix else None
    alive = sig_age is not None and sig_age <= SIGNAL_FRESH.total_seconds()
    gps_fresh = fix_age is not None and fix_age <= FIX_FRESH.total_seconds()
    if not alive:
        state = OFFLINE
        why = "no signal of any kind" + (f" for {int(sig_age)} s" if sig_age is not None else " since start")
    elif gps_fresh:
        state = ONLINE
        why = f"app alive ({int(sig_age)} s ago) and fresh fix ({int(fix_age)} s ago)"
    else:
        state = ONLINE_NO_GPS
        why = (f"app alive ({int(sig_age)} s ago) but newest accepted fix is "
               + (f"{int(fix_age)} s old" if fix_age is not None else "missing (none accepted yet)"))
    return {
        "state": state,
        "online": state != OFFLINE,
        "gps_fresh": gps_fresh,
        "reason": why,
        "last_signal_at": last_sig.isoformat() if last_sig else None,
        "last_fix_at": fix.isoformat() if fix else None,
        "signal_age_s": round(sig_age, 1) if sig_age is not None else None,
        "fix_age_s": round(fix_age, 1) if fix_age is not None else None,
    }


def for_trip(trip: dict, *, now: Optional[datetime] = None, last_fix_at=None) -> dict:
    """Live state of one trip row. Inactive trips are always offline.
    `last_fix_at` overrides trips.last_fix_at (readers that already loaded the
    newest accepted fix pass its recorded_at; pre-048 databases have no column)."""
    now = now or datetime.now(timezone.utc)
    if trip.get("status") not in (None, "active"):
        return compute(now=now)  # -> offline
    return compute(
        now=now,
        last_signal_at=trip.get("last_signal_at"),
        last_heartbeat_at=trip.get("last_heartbeat_at"),
        last_fix_at=last_fix_at if last_fix_at is not None else trip.get("last_fix_at"),
    )


def trip_cols(has_lifecycle: bool, has_fix_col: bool) -> str:
    """Extra trips columns a reader should select for `for_trip`."""
    cols = []
    if has_lifecycle:
        cols += ["last_signal_at", "last_heartbeat_at"]
    if has_fix_col:
        cols += ["last_fix_at"]
    return ", ".join(cols)


def states_for_trips(supabase, trips: list, *, now: Optional[datetime] = None) -> dict:
    """{trip_id: compute(...)} for a list of ACTIVE trip rows. Uses
    trips.last_fix_at when present; otherwise loads each trip's newest accepted
    fix (shared gps_filter rule). Bounded: one query per trip lacking the column."""
    import gps_filter
    now = now or datetime.now(timezone.utc)
    out = {}
    for t in trips:
        fix = t.get("last_fix_at")
        if fix is None and "last_fix_at" not in t:
            pos = gps_filter.latest_position(supabase, t["id"])
            fix = pos["recorded_at"] if pos else None
        out[t["id"]] = for_trip({**t, "status": t.get("status", "active")}, now=now, last_fix_at=fix)
    return out
