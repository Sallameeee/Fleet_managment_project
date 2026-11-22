"""Server load test: N buses reconnect at the same moment after a 1-hour outage and
flush their whole backlog exactly like the app (pings in batches of 200, then
heartbeats in batches of 100), all in parallel. Measures per-request latency,
errors and drain time; then verifies EVERY trip has exactly its fixes (original
timestamps, no duplicates). Fixtures: qa_load_NN drivers/buses in acme-bus-co
(ledger -> deleted by cleanup).

Usage: python qa/load_flush.py --buses 10 --minutes 60
"""
import argparse
import concurrent.futures as cf
import math
import statistics
import time
from datetime import datetime, timedelta

from qa_lib import CAIRO, api, evidence, iso, load_ledger, mint_impersonation, password, save_ledger, sb, utc_now

SLUG = "acme-bus-co"


def hav(a, b):
    R = 6371000.0
    la1, lo1, la2, lo2 = map(math.radians, (a[1], a[0], b[1], b[0]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def make_fixtures(led, n):
    o = led["orgs"][SLUG]
    tok = mint_impersonation(o["owner_id"], o["org_id"])
    lo = led.setdefault("load", {})
    today = datetime.now(CAIRO)
    for i in range(1, n + 1):
        k = f"{i:02d}"
        if k in lo:
            continue
        pw = password()
        c, d, _ = api("POST", "/drivers", tok, {"name": f"qa_ Load Driver {k}", "username": f"qa_load_{k}", "password": pw})
        assert c in (200, 201), (c, d)
        did = d.get("id") or d.get("driver", {}).get("id")
        c, v, _ = api("POST", "/vehicles", tok, {"bus_number": f"qa_LBUS{k}", "plate_number": f"QA L{k}", "capacity": 40})
        assert c in (200, 201), (c, v)
        vid = v.get("id") or v.get("vehicle", {}).get("id")
        c, a, _ = api("POST", "/assignments", tok, {"driver_id": did, "route_id": o["route"]["id"], "vehicle_id": vid,
                                                    "trip_date": today.date().isoformat(),
                                                    "start_time": (today - timedelta(minutes=5)).strftime("%H:%M"),
                                                    "end_time": "23:59", "shift_label": "qa_load"})
        assert c in (200, 201), (c, a)
        lo[k] = {"driver_id": did, "username": f"qa_load_{k}", "password": pw, "vehicle_id": vid,
                 "assignment_id": a.get("id") or a.get("assignment", {}).get("id")}
        save_ledger(led)
    return lo


def backlog(coords, minutes, t_end):
    cum = [0.0]
    for p, q in zip(coords, coords[1:]):
        cum.append(cum[-1] + hav(p, q))
    total, v = cum[-1], 30 / 3.6
    n = int(minutes * 60 / 5)
    t0 = t_end - timedelta(seconds=5 * n)
    pos, direction, pings = 0.0, 1, []
    for i in range(n):
        k = next((j for j in range(1, len(cum)) if cum[j] >= pos), len(cum) - 1)
        p, q = coords[k - 1], coords[k]
        f = (pos - cum[k - 1]) / ((cum[k] - cum[k - 1]) or 1.0)
        pings.append({"lat": round(p[1] + (q[1] - p[1]) * f, 7), "lng": round(p[0] + (q[0] - p[0]) * f, 7),
                      "speed": v, "heading": 0.0, "recorded_at": iso(t0 + timedelta(seconds=5 * i)),
                      "battery": max(20, 95 - i // 40), "net_state": "offline"})
        pos += direction * v * 5
        if pos >= total or pos <= 0:
            direction *= -1
            pos = max(0.0, min(total, pos))
    hbs = [{"sent_at": iso(t0 + timedelta(seconds=60 * j)), "battery": max(20, 95 - j), "net_state": "offline"}
           for j in range(int(minutes))]
    return pings, hbs


def run_bus(k, fx, coords, minutes):
    c, d, _ = api("POST", "/auth/login", body={"login": f"{fx['username']}@{SLUG}", "password": fx["password"]})
    tok = d["access_token"]
    c, d, _ = api("POST", "/trips/start", tok, {"assignment_id": fx["assignment_id"]})
    trip = (d.get("trip") or d).get("id")
    pings, hbs = backlog(coords, minutes, utc_now() - timedelta(seconds=30))
    lat, codes = [], []
    t0 = time.perf_counter()
    errs = []
    for i in range(0, len(pings), 200):
        c, r, dt = api("POST", f"/trips/{trip}/pings", tok, pings[i:i + 200])
        lat.append(dt)
        codes.append(c)
        if c not in (200, 201):
            errs.append(("pings", i, c, str(r)[:300]))
    for i in range(0, len(hbs), 100):
        c, r, dt = api("POST", f"/trips/{trip}/heartbeat", tok, hbs[i:i + 100])
        lat.append(dt)
        codes.append(c)
        if c not in (200, 201):
            errs.append(("heartbeat", i, c, str(r)[:300]))
    for e in errs:
        evidence(f"[load] bus {k} trip {trip[:8]} ERROR {e}")
    drain = time.perf_counter() - t0
    return {"bus": k, "trip": trip, "tok": tok, "sent_pings": len(pings), "sent_hb": len(hbs),
            "lat": lat, "codes": codes, "drain_s": drain, "first_ts": pings[0]["recorded_at"], "last_ts": pings[-1]["recorded_at"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--buses", type=int, default=10)
    ap.add_argument("--minutes", type=int, default=60)
    a = ap.parse_args()
    led = load_ledger()
    fx = make_fixtures(led, a.buses)
    coords = sb("GET", f"routes?select=geometry&id=eq.{led['orgs'][SLUG]['route']['id']}")[0]["geometry"]["coordinates"]
    evidence(f"[load] {a.buses} buses x {a.minutes} min backlog ({a.minutes * 12} fixes + {a.minutes} heartbeats each) flushing in parallel")
    t0 = time.perf_counter()
    with cf.ThreadPoolExecutor(a.buses) as ex:
        res = list(ex.map(lambda kv: run_bus(kv[0], kv[1], coords, a.minutes), list(fx.items())[:a.buses]))
    wall = time.perf_counter() - t0
    led["load_trips"] = [r["trip"] for r in res]
    save_ledger(led)
    all_lat = sorted(x for r in res for x in r["lat"])
    bad = [(r["bus"], c) for r in res for c in r["codes"] if c not in (200, 201)]
    p95 = all_lat[max(0, int(len(all_lat) * 0.95) - 1)]
    evidence(f"[load] wall={wall:.1f}s requests={len(all_lat)} errors={bad} latency p50={statistics.median(all_lat)*1000:.0f}ms "
             f"p95={p95*1000:.0f}ms max={all_lat[-1]*1000:.0f}ms drain/bus max={max(r['drain_s'] for r in res):.1f}s")
    ok_all = True
    for r in res:
        rows = []
        off = 0
        while True:
            page = sb("GET", f"location_pings?select=recorded_at&trip_id=eq.{r['trip']}&order=recorded_at&offset={off}&limit=1000")
            rows += page
            if len(page) < 1000:
                break
            off += 1000
        ts = [x["recorded_at"] for x in rows]
        hb = sb("GET", f"trip_heartbeats?select=id&trip_id=eq.{r['trip']}&limit=1000")
        ok = len(ts) == r["sent_pings"] and len(set(ts)) == len(ts) and len(hb) == r["sent_hb"]
        ok_all &= ok
        evidence(f"[load] bus {r['bus']} trip {r['trip'][:8]}: server pings {len(ts)}/{r['sent_pings']} unique={len(set(ts))} "
                 f"heartbeats {len(hb)}/{r['sent_hb']} drain={r['drain_s']:.1f}s {'PASS' if ok else 'FAIL'}")
        api("POST", f"/trips/{r['trip']}/end", r["tok"])
    evidence(f"{'PASS' if ok_all and not bad else 'FAIL'} [load] all {a.buses} buses: every fix stored exactly once")


if __name__ == "__main__":
    main()
