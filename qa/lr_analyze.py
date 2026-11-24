"""Numbers for the long-offline run (qa/nox_longrun.sh): capture cadence, what the
server holds, History gaps, CPU / memory / storage, and the trip's close/reopen
timeline. Reads qa/out/lr_log.txt + lr_buffer_*.txt + the live database.

Usage: python qa/lr_analyze.py <trip_id>
"""
import json
import re
import statistics
import sys
from datetime import datetime

from qa_lib import OUT, api, evidence, sb

trip = sys.argv[1]
log = (OUT / "lr_log.txt").read_text(encoding="utf-8").splitlines()


def ts(s):
    return datetime.fromisoformat(s.strip().replace("Z", "+00:00"))


off = ts(next(l.split()[1] for l in log if l.startswith("OFF ")))
on = ts(next(l.split()[1] for l in log if l.startswith("ON ")))
buf = sorted(ts(x) for x in (OUT / "lr_buffer_pings.txt").read_text().split() if x.strip())
gaps = [(b - a).total_seconds() for a, b in zip(buf, buf[1:])]
offline_s = (on - off).total_seconds()

# --- samples: CPU ticks (1/100 s), PSS, DB size ---
samples = []
for l in log:
    m = re.match(r"(\S+) (\S+Z) buf_pings=(\d+) buf_hb=(\d+) pid=(\S*) fg=(\S*) pss_kb=(\S*) cpu_ticks=(\S*) screen=(\S*) battery=(\S*) db_bytes=(\S*)", l)
    if m:
        samples.append({"label": m[1], "t": ts(m[2]), "pings": int(m[3]), "hb": int(m[4]), "pid": m[5], "fg": m[6],
                        "pss_kb": int(m[7]) if m[7].isdigit() else None, "ticks": int(m[8]) if m[8].isdigit() else None,
                        "screen": m[9].split("=")[-1], "db": int(m[11]) if m[11].isdigit() else None})
cpu = []
for a, b in zip(samples, samples[1:]):
    if a["ticks"] is not None and b["ticks"] is not None and a["pid"] == b["pid"]:
        dt = (b["t"] - a["t"]).total_seconds()
        cpu.append({"from": a["label"], "to": b["label"], "cpu_pct_one_core": round((b["ticks"] - a["ticks"]) / dt, 2),
                    "screen": b["screen"]})
peak = max((s for s in samples if s["db"]), key=lambda s: s["pings"])
empty_db = min(s["db"] for s in samples if s["db"])
bytes_per_row = round((peak["db"] - empty_db) / max(1, peak["pings"] + peak["hb"]), 1)

# --- server ---
rows, off_set = [], 0
while True:
    page = sb("GET", f"location_pings?select=recorded_at,created_at&trip_id=eq.{trip}&order=recorded_at&offset={off_set}&limit=1000")
    rows += page
    if len(page) < 1000:
        break
    off_set += 1000
srv_times = [ts(r["recorded_at"]) for r in rows]
t = sb("GET", f"trips?select=driver_id,status,end_reason,started_at,ended_at&id=eq.{trip}")[0]
others = sb("GET", f"trips?select=id&driver_id=eq.{t['driver_id']}&id=neq.{trip}")
stray = 0
for o in others:
    stray += len(sb("GET", f"location_pings?select=id&trip_id=eq.{o['id']}&recorded_at=gte.{off.isoformat()}&recorded_at=lte.{on.isoformat()}"))
alerts = sb("GET", f"alerts?select=type,occurred_at,detail&trip_id=eq.{trip}&order=occurred_at")
led = json.loads((OUT / "longrun_ledger.json").read_text(encoding="utf-8"))
code, d, _ = api("POST", "/auth/login", body={"login": f"{led['manager']['username']}@{led['slug']}", "password": led["manager"]["password"]})
hist = api("GET", "/history", d["access_token"], params={"kind": "drivers", "subject_id": led["driver"]["id"]})[1]
htrip = next((x for x in hist.get("trips", []) if x.get("trip_id") == trip), None) or next(iter(hist.get("trips", [])), {})
hp = sorted(ts(p["recorded_at"]) for p in htrip.get("pings", []))
hgaps = [(b - a).total_seconds() for a, b in zip(hp, hp[1:])]

out = {
    "offline_minutes": round(offline_s / 60, 1),
    "buffered_fixes": len(buf), "expected_at_5s": int(offline_s // 5),
    "capture_interval_s": {"median": statistics.median(gaps) if gaps else None, "p95": sorted(gaps)[int(len(gaps) * .95)] if gaps else None,
                           "max": max(gaps) if gaps else None, "over_12s": sum(g > 12 for g in gaps), "over_120s": sum(g > 120 for g in gaps)},
    "server_fixes_total": len(rows), "server_duplicates": len(srv_times) - len(set(srv_times)),
    "server_fixes_in_outage": sum(off <= x <= on for x in srv_times), "stray_fixes_on_other_trips": stray,
    "history_pings": len(hp), "history_max_gap_s": max(hgaps) if hgaps else None,
    "history_breaks_over_120s": sum(g > 120 for g in hgaps),
    "history_events": [{k: e.get(k) for k in ("type", "occurred_at", "gap_min", "buffered")} for e in htrip.get("events", [])],
    "trip_now": t, "lifecycle": [(a["type"], a["occurred_at"][11:19], (a["detail"] or "")[:70]) for a in alerts],
    "cpu": cpu, "pss_mb": [(s["label"], round(s["pss_kb"] / 1024)) for s in samples if s["pss_kb"]],
    "db_bytes_peak": peak["db"], "rows_at_peak": peak["pings"] + peak["hb"], "db_bytes_per_row": bytes_per_row,
}
print(json.dumps(out, indent=1, default=str))
(OUT / "lr_analysis.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
evidence("[LR ANALYSIS] " + json.dumps({k: out[k] for k in ("offline_minutes", "buffered_fixes", "expected_at_5s", "capture_interval_s",
                                                             "server_fixes_in_outage", "server_duplicates", "stray_fixes_on_other_trips",
                                                             "history_max_gap_s", "db_bytes_per_row")}, default=str))
