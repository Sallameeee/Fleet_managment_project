"""S8b strict check (fixed app): End Trip pressed OFFLINE, delivered later.

Every fix the app still held when End was pressed must reach the server exactly
once with its original timestamp, and the trip must end NORMALLY at the moment
End was pressed (the app's queued pending end), with exactly one trip_ended log
point carrying the "offline; delivered" note.

Usage: python qa/s8b_verify.py <tag> <trip_id> <ended_at_ms_from_app>
Ground truth: qa/out/<tag>_buffer_pings.txt (app SQLite snapshot before reconnect)."""
import json
import sys
from datetime import datetime, timezone

from qa_lib import OUT, evidence, sb


def ms(s):
    return round(datetime.fromisoformat(str(s).strip().replace("Z", "+00:00")).timestamp() * 1000)


def main():
    tag, trip, end_ms = sys.argv[1], sys.argv[2], int(sys.argv[3])
    buf = [ms(x) for x in (OUT / f"{tag}_buffer_pings.txt").read_text().splitlines() if x.strip()]
    rows = sb("GET", f"location_pings?select=recorded_at,created_at&trip_id=eq.{trip}&order=recorded_at&limit=5000")
    srv = [ms(r["recorded_at"]) for r in rows]
    srv_set = set(srv)
    missing = [t for t in buf if t not in srv_set]
    t = sb("GET", f"trips?select=status,ended_at,end_reason,end_detail&id=eq.{trip}")[0]
    ended = sb("GET", f"alerts?select=occurred_at,detail,meta&trip_id=eq.{trip}&type=eq.trip_ended")
    life = [(a["type"], a["occurred_at"][11:19], (a["detail"] or "")[:110]) for a in
            sb("GET", f"alerts?select=type,occurred_at,detail&trip_id=eq.{trip}&order=occurred_at")
            if a["type"] in ("trip_started", "connection_lost", "connection_restored", "trip_ended")]
    end_err_s = abs(ms(t["ended_at"]) - end_ms) / 1000 if t.get("ended_at") else None
    after_end = [x for x in srv if x > end_ms + 1000]
    summary = {
        "buffered_at_end": len(buf), "on_server_from_buffer": len(buf) - len(missing), "missing": len(missing),
        "duplicates_on_server": len(srv) - len(srv_set), "fixes_after_pressed_end": len(after_end),
        "status": t["status"], "end_reason": t["end_reason"],
        "ended_at": t["ended_at"], "pressed_end_at": datetime.fromtimestamp(end_ms / 1000, timezone.utc).isoformat(),
        "ended_at_error_s": end_err_s, "trip_ended_points": len(ended),
        "trip_ended_text": (ended[0]["detail"] if ended else None),
        "end_detail": t.get("end_detail"), "lifecycle": life,
    }
    ok = (len(buf) > 0 and not missing and summary["duplicates_on_server"] == 0 and t["status"] == "completed"
          and t["end_reason"] == "normal" and end_err_s is not None and end_err_s <= 1.5 and len(ended) == 1
          and not after_end)
    evidence(f"{'PASS' if ok else 'FAIL'} [{tag}] End Trip pressed offline, delivered later: " + json.dumps(summary, ensure_ascii=False))
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(tag, "RESULT", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
