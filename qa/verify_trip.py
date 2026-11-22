"""Server-side verdict for one window of a trip driven by the real app.

Usage: python qa/verify_trip.py <trip_id> <from_utc> <to_utc> <label> [--interval 5]
Prints: fixes vs expected, gaps > 2x interval, ordering, duplicates, upload delay
(created_at - recorded_at; buffered fixes show large delays with ORIGINAL
recorded_at), outliers, and lifecycle/log alerts inside the window.
"""
import argparse
import json
from datetime import datetime

from qa_lib import cairo, evidence, sb


def dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("trip")
    ap.add_argument("frm")
    ap.add_argument("to")
    ap.add_argument("label")
    ap.add_argument("--interval", type=float, default=5)
    a = ap.parse_args()
    rows = sb("GET", f"location_pings?select=recorded_at,created_at,is_outlier,reject_reason,net_state,battery"
                     f"&trip_id=eq.{a.trip}&recorded_at=gte.{a.frm}&recorded_at=lte.{a.to}&order=recorded_at")
    t0, t1 = dt(a.frm), dt(a.to)
    span = (t1 - t0).total_seconds()
    expected = span / a.interval
    rec = [dt(r["recorded_at"]) for r in rows]
    gaps = [(x.strftime("%H:%M:%S"), round((y - x).total_seconds())) for x, y in zip(rec, rec[1:])
            if (y - x).total_seconds() > 2 * a.interval]
    dups = len(rec) - len(set(rec))
    ordered = all(x < y for x, y in zip(rec, rec[1:]))
    delays = sorted((dt(r["created_at"]) - dt(r["recorded_at"])).total_seconds() for r in rows)
    buffered = [d for d in delays if d > 20]
    out = [r["reject_reason"] for r in rows if r["is_outlier"]]
    nets = sorted({r["net_state"] for r in rows if r["net_state"]})
    alerts = sb("GET", f"alerts?select=type,occurred_at,detail&trip_id=eq.{a.trip}&occurred_at=gte.{a.frm}"
                       f"&occurred_at=lte.{a.to}&order=occurred_at")
    summary = {
        "label": a.label, "window": f"{cairo(a.frm)} -> {cairo(a.to)} ({span/60:.1f} min)",
        "fixes": len(rows), "expected": round(expected), "ratio": round(len(rows) / expected, 2) if expected else None,
        "gaps_over_2x": gaps, "duplicates": dups, "strictly_ordered": ordered,
        "buffered_fixes(delay>20s)": len(buffered), "max_upload_delay_s": round(delays[-1]) if delays else None,
        "outliers": out, "net_states": nets,
        "alerts": [(x["type"], x["occurred_at"][11:19] + "Z", (x["detail"] or "")[:70]) for x in alerts],
    }
    evidence(f"[verify] {a.label}: " + json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
