"""Server side of the buffer-cap test (qa/nox_bufcap.sh): what reached the
server, what was dropped on the phone, and what the dashboard shows for it.

Usage: python qa/bufcap_verify.py <trip_id> <off_iso> <on_iso>
"""
import json
import sys
from datetime import datetime

from qa_lib import OUT, api, evidence, sb

trip, off, on = sys.argv[1], sys.argv[2], sys.argv[3]


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


rows = []
offset = 0
while True:
    chunk = sb("GET", f"location_pings?select=recorded_at,speed,heading&trip_id=eq.{trip}&order=recorded_at&offset={offset}&limit=1000")
    rows += chunk
    if len(chunk) < 1000:
        break
    offset += 1000
injected = [r for r in rows if r["speed"] == 12.34 and r["heading"] == 359.9]
real = [r for r in rows if not (r["speed"] == 12.34 and r["heading"] == 359.9)]
real_off = [r for r in real if ts(off) <= ts(r["recorded_at"]) <= ts(on)]

before = [l.split("|", 1) for l in (OUT / "bufcap_real_before.txt").read_text().split() if "|" in l]
after = [l.split("|", 1) for l in (OUT / "bufcap_real_after.txt").read_text().split() if "|" in l]
kept = {t for _, t in after}
dropped = [t for _, t in before if t not in kept]
server_ts = {ts(r["recorded_at"]) for r in real}
dropped_on_server = [t for t in dropped if ts(t) in server_ts]

# The hole the dashboard shows: the biggest gap between consecutive fixes of the trip.
times = sorted(ts(r["recorded_at"]) for r in rows)
gaps = sorted(((b - a).total_seconds(), a, b) for a, b in zip(times, times[1:]))[-1:] if len(times) > 1 else []
t = sb("GET", f"trips?select=status,end_reason,started_at,ended_at&id=eq.{trip}")[0]
alerts = sb("GET", f"alerts?select=type,occurred_at,detail&trip_id=eq.{trip}&order=occurred_at")

led = json.loads((OUT / "longrun_ledger.json").read_text(encoding="utf-8"))
code, d, _ = api("POST", "/auth/login", body={"login": f"{led['manager']['username']}@{led['slug']}", "password": led["manager"]["password"]})
hist = api("GET", "/history", d["access_token"], params={"kind": "drivers", "subject_id": led["driver"]["id"]})[1]
htrip = next((x for x in hist.get("trips", []) if x.get("trip_id") == trip), {})
hist_keys = sorted(k for k in htrip.keys() if k != "pings")
loss_words = [k for k in hist_keys if any(w in k for w in ("drop", "lost", "missing", "gap"))]

out = {
    "server_rows": len(rows), "server_injected": len(injected), "server_real_during_outage": len(real_off),
    "phone_real_offline_before_trim": len(before), "phone_real_dropped_by_cap": len(dropped),
    "dropped_but_on_server": len(dropped_on_server),
    "dropped_window": [dropped[0], dropped[-1]] if dropped else None,
    "biggest_gap_s": round(gaps[0][0]) if gaps else None,
    "biggest_gap": [gaps[0][1].isoformat(), gaps[0][2].isoformat()] if gaps else None,
    "history_pings": len(htrip.get("pings", [])), "history_trip_fields": hist_keys,
    "history_mentions_loss": loss_words,
    "lifecycle": [(a["type"], a["occurred_at"][11:19]) for a in alerts],
    "history_events": [{k: e.get(k) for k in ("type", "occurred_at", "gap_min", "buffered", "detail")} for e in htrip.get("events", [])
                       if e.get("type") in ("connection_lost", "connection_restored", "offline", "trip_ended")],
    "trip": t,
}
print(json.dumps(out, indent=1, default=str))
(OUT / "bufcap_verify.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
evidence(f"[BUFCAP] trip {trip[:8]}: server rows={len(rows)} injected={len(injected)} real dropped on phone={len(dropped)} "
         f"(window {out['dropped_window']}), dropped-but-on-server={len(dropped_on_server)}, biggest gap={out['biggest_gap_s']} s, "
         f"history mentions loss: {loss_words or 'no'}")
