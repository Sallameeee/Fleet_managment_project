"""S8 strict check: every fix / heartbeat the app buffered while offline must be
on the server exactly once, with its ORIGINAL timestamp, in order, uploaded only
after reconnect; plus trip state and lifecycle log points after the outage.

Usage: python qa/s8_verify.py <trip_id> <off_utc> <on_utc>
Ground truth: qa/out/s8_buffer_pings.txt / s8_buffer_heartbeats.txt (snapshot
of the app's SQLite buffer taken just before reconnecting)."""
import json
import sys
from datetime import datetime

from qa_lib import OUT, evidence, sb


def ms(s):
    s = s.strip().replace("Z", "+00:00")
    return round(datetime.fromisoformat(s).timestamp() * 1000)


def main():
    trip, off, on = sys.argv[1], sys.argv[2], sys.argv[3]
    buf_p = [ms(x) for x in (OUT / "s8_buffer_pings.txt").read_text().splitlines() if x.strip()]
    buf_h = [ms(x) for x in (OUT / "s8_buffer_heartbeats.txt").read_text().splitlines() if x.strip()]
    rows = []
    off_set = 0
    while True:  # page past PostgREST's 1000-row cap
        page = sb("GET", f"location_pings?select=recorded_at,created_at,is_outlier,reject_reason,net_state,battery"
                         f"&trip_id=eq.{trip}&order=recorded_at&offset={off_set}&limit=1000")
        rows += page
        if len(page) < 1000:
            break
        off_set += 1000
    srv = {ms(r["recorded_at"]): r for r in rows}
    srv_ts = [ms(r["recorded_at"]) for r in rows]
    missing = [t for t in buf_p if t not in srv]
    in_window = [r for r in rows if ms(off) <= ms(r["recorded_at"]) <= ms(on)]
    uploaded_late = [r for r in in_window if ms(r["created_at"]) >= ms(on) - 5000]
    recs = sorted(ms(r["recorded_at"]) for r in in_window)
    gaps = [((b - a) // 1000) for a, b in zip(recs, recs[1:]) if b - a > 12_000]
    hb = sb("GET", f"trip_heartbeats?select=sent_at&trip_id=eq.{trip}&order=sent_at&limit=1000")
    hb_srv = {ms(h["sent_at"]) for h in hb}
    hb_missing = [t for t in buf_h if t not in hb_srv]
    trip_row = sb("GET", f"trips?select=status,end_reason,end_detail,conn_lost_at&id=eq.{trip}")[0]
    alerts = sb("GET", f"alerts?select=type,occurred_at,detail&trip_id=eq.{trip}&order=occurred_at")
    life = [(a["type"], a["occurred_at"][11:19], (a["detail"] or "")[:80]) for a in alerts
            if a["type"] in ("connection_lost", "connection_restored", "trip_ended", "offline", "trip_started")]
    summary = {
        "buffered_fixes": len(buf_p), "on_server_from_buffer": len(buf_p) - len(missing), "missing": len(missing),
        "server_fixes_in_offline_window": len(in_window), "uploaded_after_reconnect": len(uploaded_late),
        "duplicates_on_server": len(srv_ts) - len(set(srv_ts)), "ordered": recs == sorted(recs),
        "gaps_over_12s_in_window": gaps[:10], "outliers_in_window": sum(1 for r in in_window if r["is_outlier"]),
        "net_state_values": sorted({r["net_state"] for r in in_window if r["net_state"]}),
        "buffered_heartbeats": len(buf_h), "heartbeats_missing": len(hb_missing),
        "trip_status_after": trip_row["status"], "end_reason_after": trip_row["end_reason"],
        "lifecycle": life,
    }
    ok = (summary["missing"] == 0 and summary["duplicates_on_server"] == 0 and summary["ordered"]
          and summary["heartbeats_missing"] == 0 and len(buf_p) > 0)
    evidence(f"{'PASS' if ok else 'FAIL'} [S8] 1 h fully-offline trip: " + json.dumps(summary, ensure_ascii=False))
    print("S8 RESULT", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()
