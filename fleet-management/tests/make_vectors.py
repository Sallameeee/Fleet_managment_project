"""Generate the GOLDEN VECTORS shared by the Python rule and the driver app's
Dart port (shared/dart). Re-run after changing stop_progress.py or
gps_filter.py:  python tests/make_vectors.py  — then run the Dart check:
  cd shared/dart && dart run test/vectors_test.dart
Both implementations must produce the same decisions for every vector."""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import gps_filter as gf  # noqa: E402
import gps_sim as g  # noqa: E402
import stop_progress as sp  # noqa: E402

T0 = datetime(2026, 11, 24, 16, 0, tzinfo=timezone.utc)
A = (30.0, 31.0)
OUT = os.path.join(os.path.dirname(os.path.dirname(HERE)), "shared", "dart", "test", "vectors.json")


def progression(name, stops_pts, build, cfg=None, seed=1, sigma=15.0):
    stops = g.stops_from(stops_pts)
    tr = g.Track(build[0], T0, seed=seed, sigma_m=sigma)
    for op in build[1:]:
        getattr(tr, op[0])(*op[1:])
    c = sp.config(cfg)
    st = sp.new_state()
    decisions = []
    for f in tr.fixes:
        st, ds = sp.step(st, stops, f, c)
        decisions += ds
    st, ds = sp.finalize(st, stops, tr.fixes[-1]["recorded_at"], c)
    decisions += ds
    summ = sp.summary(st, stops, c)
    return {"name": name, "cfg": cfg or {}, "stops": stops, "fixes": tr.fixes,
            "expect": {"decisions": [[d["type"], d.get("stop_id"), d.get("status")] for d in decisions],
                       "statuses": [st["stops"].get(s["id"], {}).get("status") for s in stops],
                       "closest_m": [s["closest_m"] for s in summ["stops"]]}}


def gate(name, build, seed=1, sigma=15.0, started_at=None):
    tr = g.Track(build[0], T0, seed=seed, sigma_m=sigma)
    for op in build[1:]:
        getattr(tr, op[0])(*op[1:])
    acc, rej = gf.filter_fixes(tr.fixes, trip_started_at=started_at)
    idx = {f["recorded_at"] + str(i): i for i, f in enumerate(tr.fixes)}
    verdict = []
    acc_ids = {id(a) for a in acc}
    # Recompute the verdict per input index (filter returns copies for rejects).
    acc_keys = [(a["recorded_at"], a["lat"], a["lng"]) for a in acc]
    rej_map = {}
    for r in rej:
        rej_map.setdefault((r["recorded_at"], r["lat"], r["lng"]), []).append(r["reject_reason"])
    for f in tr.fixes:
        k = (f["recorded_at"], f["lat"], f["lng"])
        if k in rej_map and rej_map[k]:
            verdict.append(rej_map[k].pop(0))
        else:
            verdict.append("ok")
    del idx, acc_ids, acc_keys
    return {"name": name, "fixes": tr.fixes, "trip_started_at": started_at, "expect": verdict}


def main():
    s3 = [A, g.offset(A, 0, 1500), g.offset(A, 0, 3000)]
    vec = {"generated_at": datetime.now(timezone.utc).isoformat(), "progression": [], "gate": []}
    P = vec["progression"]
    P.append(progression("arrive_all", s3, [g.offset(A, 0, -500), ("drive", s3[0], 35), ("dwell", 45),
                                             ("drive", s3[1], 35), ("dwell", 45), ("drive", s3[2], 35), ("dwell", 45)]))
    P.append(progression("drive_through_middle", s3, [g.offset(A, 0, -500), ("drive", s3[0], 35), ("dwell", 40),
                                                       ("drive", s3[2], 50), ("dwell", 40)], seed=2))
    far = [A, g.offset(A, 900, 2000), g.offset(A, 0, 4000)]
    P.append(progression("skip_far_stop", far, [A, ("dwell", 30), ("drive", far[2], 60), ("dwell", 30)], seed=4))
    hw = [g.offset(A, 0, i * 6000) for i in range(4)]
    P.append(progression("highway_100", hw, [g.offset(A, 0, -300), ("drive", hw[0], 40), ("dwell", 40),
                                              ("drive", hw[3], 100), ("dwell", 40)], seed=5, sigma=20))
    close = [A, g.offset(A, 0, 45), g.offset(A, 0, 1500)]
    P.append(progression("stops_close_together", close, [g.offset(A, 0, -400), ("drive", A, 30), ("dwell", 30),
                                                          ("drive", close[1], 10), ("dwell", 30), ("drive", close[2], 40), ("dwell", 30)],
                         seed=6, sigma=10))
    two = [A, g.offset(A, 0, 2000)]
    P.append(progression("drift_parked", two, [g.offset(A, 0, -300), ("drive", A, 30), ("dwell", 300),
                                                ("drive", two[1], 40), ("dwell", 20)], seed=7, sigma=25))
    P.append(progression("start_beyond_stop1", s3, [g.offset(A, 400, 700), ("drive", s3[1], 40), ("dwell", 30),
                                                     ("drive", s3[2], 40), ("dwell", 30)], seed=9))
    back = [A, g.offset(A, 700, 1500), g.offset(A, 0, 3000)]
    P.append(progression("double_back", back, [A, ("dwell", 30), ("drive", g.offset(A, 0, 2600), 50),
                                                ("drive", back[1], 40), ("dwell", 40), ("drive", back[2], 40), ("dwell", 30)], seed=10))
    camp = [A, g.offset(A, 0, 3000)]
    P.append(progression("near_stop_wait", camp, [A, ("dwell", 30), ("drive", g.offset(A, 170, 3000), 50), ("dwell", 270)], seed=21))
    P.append(progression("radius_30", s3, [g.offset(A, 0, -500), ("drive", s3[0], 35), ("dwell", 40),
                                            ("drive", g.offset(s3[1], 45, 0), 40), ("drive", s3[2], 40), ("dwell", 40)],
                         cfg={"arrive_radius_m": 30}, seed=12))
    G = vec["gate"]
    G.append(gate("highway_130", [A, ("drive", g.offset(A, 0, 8000), 130)], seed=1, sigma=18))
    G.append(gate("spike", [A, ("drive", g.offset(A, 0, 400), 40), ("spike", 3000), ("drive", g.offset(A, 0, 800), 40)], seed=5, sigma=10))
    G.append(gate("cached_then_catch_up", [A, ("drive", g.offset(A, 0, 500), 60), ("cached_repeat", 8, g.offset(A, 0, 1170), 60),
                                           ("drive", g.offset(A, 0, 1600), 60)], seed=4, sigma=8))
    G.append(gate("teleport", [A, ("drive", g.offset(A, 0, 200), 30), ("gap", 0, g.offset(A, 8000, 0)),
                               ("drive", g.offset(A, 8000, 300), 30)], seed=6, sigma=5))
    G.append(gate("low_accuracy_spike", [A, ("drive", g.offset(A, 0, 300), 30), ("spike", 400, 900.0), ("drive", g.offset(A, 0, 600), 30)], seed=8))
    G.append(gate("before_start", [g.offset(A, 0, 0), ("drive", g.offset(A, 0, 300), 30)], seed=9,
                  started_at=(T0 + timedelta(minutes=10)).isoformat()))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(vec, f, ensure_ascii=False)
    print(f"wrote {OUT}: {len(P)} progression + {len(G)} gate vectors")


if __name__ == "__main__":
    main()
