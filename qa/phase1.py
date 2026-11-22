"""Phase 1 live checks against the deployed backend (QA_BASE). Uses the qa_
fixtures from setup_fixtures.py. Writes PASS/FAIL lines to qa/out/EVIDENCE.md
and a JSON summary to qa/out/phase1_results.json.

Sections: auth (every role), trip pipeline (both orgs), detection + lifecycle,
production sweeper (real 150 s silence -> connection_lost), concurrency/cancel,
Edit Logs honoured, Cairo times, latency.
"""
import concurrent.futures as cf
import json
import math
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone

from qa_lib import (CAIRO, OUT, api, auth_admin, evidence, iso, load_ledger, mint_impersonation, password,
                    save_ledger, sb, utc_now)

led = load_ledger()
ORGS = led["orgs"]
RESULTS = []
LAT = {}  # endpoint -> [seconds]


def rec(section, item, ok, ev):
    RESULTS.append({"section": section, "item": item, "result": "PASS" if ok else "FAIL", "evidence": ev})
    evidence(f"{'PASS' if ok else 'FAIL'} [{section}] {item}: {ev}")


def call(method, path, token=None, body=None, params=None, key=None):
    code, data, dt = api(method, path, token, body, params)
    LAT.setdefault(key or (method + " " + path.split("?")[0]), []).append(dt)
    return code, data


def staff_login(slug, who):
    o = ORGS[slug][who]
    return call("POST", "/auth/login", body={"login": f"{o['username']}@{slug}", "password": o["password"]},
                key="POST /auth/login")


# ── geometry along the qa route (A -> D, New Cairo) ─────────────────────────
A = (30.0300, 31.4700)
D_ = (30.0405, 31.4835)
M_LAT = 111_000.0
M_LNG = 111_000.0 * math.cos(math.radians(30.035))
dN, dE = (D_[0] - A[0]) * M_LAT, (D_[1] - A[1]) * M_LNG
L = math.hypot(dN, dE)
uN, uE = dN / L, dE / L          # along-route unit
pN, pE = -uE, uN                 # perpendicular unit


def point(dist_m, off_m=0.0):
    n = uN * dist_m + pN * off_m
    e = uE * dist_m + pE * off_m
    return A[0] + n / M_LAT, A[1] + e / M_LNG


def ping(t0, t, dist, off=0.0, speed=None, battery=None, net=None):
    lat, lng = point(dist, off)
    return {"lat": round(lat, 7), "lng": round(lng, 7), "speed": speed, "heading": 42.0,
            "recorded_at": iso(t0 + timedelta(seconds=t)), "battery": battery, "net_state": net}


def timeline(t0):
    seg1 = [ping(t0, t, 6 * t, speed=6, battery=80, net="wifi") for t in range(0, 60, 10)]
    seg2 = [ping(t0, t, 300 + 25 * (t - 50), speed=25, battery=80, net="wifi") for t in (60, 70, 80, 90)]
    seg3 = [ping(t0, t, 1320 + (3 if (t // 10) % 2 else -3), speed=0, battery=78, net="none") for t in range(100, 470, 10)]
    offs = {470: 150, 480: 300, 490: 450, 500: 450, 510: 450, 520: 450, 530: 450, 540: 450, 550: 450, 560: 450,
            570: 300, 580: 150, 590: 0}
    seg4 = [ping(t0, t, 1320, off, speed=5, battery=77, net="mobile") for t, off in offs.items()]
    seg6 = [ping(t0, t, 1320 + 4.5 * (t - 1010), speed=4.5, battery=75, net="mobile") for t in range(1010, 1110, 10)]
    spike = ping(t0, 1105, 1725, 5000, speed=5, battery=75, net="mobile")
    final = ping(t0, 1110, 1740, 0, speed=2, battery=75, net="mobile")
    return seg1, seg2, seg3, seg4, seg6, spike, final


# ── sections ─────────────────────────────────────────────────────────────────
def section_auth():
    toks = {}
    for slug, o in ORGS.items():
        for who, role in (("manager", "manager"), ("drv1", "driver"), ("drv2", "driver")):
            code, d = staff_login(slug, who)
            got = (d.get("user") or {}).get("role") if isinstance(d, dict) else None
            rec("auth", f"{slug} {who} login", code == 200 and got == role, f"{code} role={got}")
            if code == 200:
                toks[(slug, who)] = d["access_token"]
                toks[(slug, who, "refresh")] = d.get("refresh_token")
        # passenger / student (email login, shared default password + forced change)
        p = o["passenger"]
        code, d = call("POST", "/auth/passenger/login", body={"email": p["email"], "password": p["default_password"]})
        rec("auth", f"{slug} passenger ({o['module']}) login", code == 200,
            f"{code} must_change_password={d.get('must_change_password') if isinstance(d, dict) else d}")
        if code == 200:
            toks[(slug, "pax")] = d.get("access_token")
        # wrong password vs unknown user -> same 401 text (no enumeration)
        c1, d1 = call("POST", "/auth/login", body={"login": f"{o['manager']['username']}@{slug}", "password": "wrong-Pass-1"})
        c2, d2 = call("POST", "/auth/login", body={"login": f"qa_nobody_xyz@{slug}", "password": "wrong-Pass-1"})
        rec("auth", f"{slug} wrong password vs unknown user", c1 == 401 and c2 == 401 and d1 == d2,
            f"{c1} {d1} | {c2} {d2}")
        # refresh
        rt = toks.get((slug, "manager", "refresh"))
        code, d = call("POST", "/auth/refresh", body={"refresh_token": rt})
        newtok = d.get("access_token") if isinstance(d, dict) else None
        rec("auth", f"{slug} /auth/refresh", code == 200 and bool(newtok) and newtok != toks.get((slug, "manager")),
            f"{code} new_token={'yes' if newtok else 'no'}")
        # deactivated user -> 403
        own = mint_impersonation(o["owner_id"], o["org_id"])
        cpatch, _ = call("PATCH", f"/drivers/{o['drvoff']['id']}", own, {"is_active": False})
        code, d = staff_login(slug, "drvoff")
        rec("auth", f"{slug} deactivated driver login", cpatch == 200 and code == 403, f"patch={cpatch} login={code} {d}")
    s, u = ORGS["school-test"], ORGS["acme-bus-co"]
    c3, d3 = call("GET", "/vehicles", toks.get(("school-test", "manager")))
    items = d3.get("vehicles", []) if isinstance(d3, dict) else (d3 if isinstance(d3, list) else [])
    leak = any(v.get("id") in (u["vehicle"]["id"], u["vehicle2"]["id"]) for v in items)
    rec("auth", "tenant isolation (school /vehicles has no uni bus)", c3 == 200 and not leak,
        f"{c3} items={len(items)} uni_bus_leaked={leak}")
    # super admin: a qa_ super-admin account (auth user + super_admins row), deleted at cleanup
    sa = led.setdefault("super_admin", {})
    if "id" not in sa:
        pw = password()
        u_ = auth_admin("POST", "users", {"email": "qa_superadmin@example.com", "password": pw, "email_confirm": True})
        sb("POST", "super_admins", {"id": u_["id"], "email": "qa_superadmin@example.com", "name": "qa_ Super Admin",
                                    "is_active": True, "permissions": {}}, prefer="return=minimal")
        sa.update(id=u_["id"], email="qa_superadmin@example.com", password=pw)
        save_ledger(led)
    code, d = call("POST", "/auth/super-admin/login", body={"email": sa["email"], "password": sa["password"]})
    rec("auth", "super-admin login", code == 200, f"{code} keys={sorted(d.keys())[:6] if isinstance(d, dict) else d}")
    return toks


def count_pings(trip_id):
    rows = sb("GET", f"location_pings?select=recorded_at,is_outlier,reject_reason,battery,net_state&trip_id=eq.{trip_id}&order=recorded_at")
    return rows


def section_pipeline(toks):
    trips = {}
    for slug, o in ORGS.items():
        tok = toks[(slug, "drv1")]
        code, d = call("POST", "/trips/start", tok, {"assignment_id": o["asg_drv1"]["id"]})
        tid = (d.get("trip") or d).get("id") if isinstance(d, dict) else None
        rec("pipeline", f"{slug} start trip", code in (200, 201) and bool(tid), f"{code} trip={str(tid)[:8]}")
        if not tid:
            continue
        o["trip1"] = {"id": tid}
        save_ledger(led)
        t0 = utc_now() - timedelta(minutes=20)
        seg1, seg2, seg3, seg4, seg6, spike, final = timeline(t0)
        for p in seg1:  # "live" one by one
            call("POST", f"/trips/{tid}/pings", tok, p, key="POST /trips/{id}/pings")
        ch, _ = call("POST", f"/trips/{tid}/heartbeat", tok, {"sent_at": iso(utc_now()), "battery": 79, "net_state": "wifi"},
                     key="POST /trips/{id}/heartbeat")
        ca1, _ = call("POST", f"/trips/{tid}/app-state", tok, {"state": "paused", "battery": 79, "net_state": "wifi"},
                      key="POST /trips/{id}/app-state")
        ca2, _ = call("POST", f"/trips/{tid}/app-state", tok, {"state": "resumed", "battery": 79, "net_state": "wifi"},
                      key="POST /trips/{id}/app-state")
        rec("pipeline", f"{slug} heartbeat + app-state beacons", ch in (200, 201) and ca1 in (200, 201) and ca2 in (200, 201),
            f"heartbeat={ch} paused={ca1} resumed={ca2}")
        c2, _ = call("POST", f"/trips/{tid}/pings", tok, seg2, key="POST /trips/{id}/pings")
        c3, d3 = call("POST", f"/trips/{tid}/pings", tok, seg3, key="POST /trips/{id}/pings")   # buffered OLD batch
        before = len(count_pings(tid))
        c3b, d3b = call("POST", f"/trips/{tid}/pings", tok, seg3, key="POST /trips/{id}/pings")  # exact re-send
        after = len(count_pings(tid))
        rec("pipeline", f"{slug} duplicate re-send deduped", c3b in (200, 201) and after == before,
            f"rows before={before} after={after} resp={str(d3b)[:120]}")
        c4, _ = call("POST", f"/trips/{tid}/pings", tok, seg4, key="POST /trips/{id}/pings")
        c6, _ = call("POST", f"/trips/{tid}/pings", tok, seg6, key="POST /trips/{id}/pings")
        cs, ds = call("POST", f"/trips/{tid}/pings", tok, spike, key="POST /trips/{id}/pings")
        cf_, _ = call("POST", f"/trips/{tid}/pings", tok, final, key="POST /trips/{id}/pings")
        fut = dict(final, recorded_at=iso(utc_now() + timedelta(minutes=30)))
        cfu, dfu = call("POST", f"/trips/{tid}/pings", tok, fut, key="POST /trips/{id}/pings")
        rows = count_pings(tid)
        sent = seg1 + seg2 + seg3 + seg4 + seg6 + [spike, final, fut]
        exp_ts = sorted({p["recorded_at"] for p in sent})
        got_ts = [r["recorded_at"] for r in rows]
        same_ts = len(got_ts) == len(set(got_ts))
        orig = all(datetime.fromisoformat(r["recorded_at"]).timestamp() in
                   {datetime.fromisoformat(x).timestamp() for x in exp_ts} for r in rows)
        rec("pipeline", f"{slug} offline batch with OLD timestamps stored as sent", c3 in (200, 201) and orig and same_ts,
            f"stored={len(rows)} sent_unique={len(exp_ts)} all_original_ts={orig} no_dup_ts={same_ts}")
        spike_row = [r for r in rows if r["is_outlier"] and r["reject_reason"] not in (None, "future_timestamp")]
        fut_row = [r for r in rows if r["reject_reason"] == "future_timestamp"]
        rec("pipeline", f"{slug} impossible spike tagged outlier", len(spike_row) >= 1,
            f"outliers={[(r['recorded_at'][11:19], r['reject_reason']) for r in spike_row]}")
        rec("pipeline", f"{slug} future timestamp tagged", len(fut_row) == 1, f"rows={len(fut_row)} resp={str(dfu)[:100]}")
        bn = [r for r in rows if r["battery"] is not None and r["net_state"]]
        rec("pipeline", f"{slug} battery/net_state stored on pings", len(bn) >= len(rows) - 1, f"{len(bn)}/{len(rows)}")
        cp, dp = call("GET", f"/trips/{tid}/pings", toks[(slug, "manager")], key="GET /trips/{id}/pings")
        clean = dp if isinstance(dp, list) else (dp.get("pings") if isinstance(dp, dict) else [])
        hidden = all(not p.get("is_outlier") for p in (clean or []))
        rec("pipeline", f"{slug} track hides outliers", cp == 200 and hidden and len(clean or []) <= len(rows) - 2,
            f"{cp} track_points={len(clean or [])} stored={len(rows)}")
        trips[slug] = tid
    return trips


def section_isolation(toks, trips):
    s, u = trips.get("school-test"), trips.get("acme-bus-co")
    if not (s and u):
        return
    c1, _ = call("GET", f"/trips/{u}/pings", toks[("school-test", "manager")])
    c2, _ = call("GET", f"/trips/{s}/pings", toks[("acme-bus-co", "manager")])
    c3, _ = call("GET", f"/trips/{u}/status", toks[("school-test", "drv1")])
    rec("auth", "tenant isolation (cross-org trip reads)", all(c in (403, 404) for c in (c1, c2, c3)),
        f"school mgr->uni pings {c1}, uni mgr->school pings {c2}, school driver->uni status {c3}")


def section_sweeper_and_end(toks, trips):
    # Real silence: no signal for > the 90 s grace + one 30 s sweep -> connection_lost by the sweeper.
    need = [t for t in trips.values() if not sb("GET", f"alerts?select=id&trip_id=eq.{t}&type=eq.connection_lost")]
    if need:
        evidence("sweeper: waiting 160 s of real silence on the qa trips ...")
        time.sleep(160)
    for slug, tid in trips.items():
        lost = sb("GET", f"alerts?select=type,occurred_at,created_at,detail,meta&trip_id=eq.{tid}&type=eq.connection_lost")
        rec("lifecycle", f"{slug} production sweeper wrote connection_lost", len(lost) >= 1,
            f"{[(a['created_at'][11:19] + 'Z', (a.get('meta') or {}).get('message_ar', '')[:60]) for a in lost]}")
        tok = toks[(slug, "drv1")]
        p = ping(utc_now(), 0, 1745, 0, speed=1, battery=74, net="wifi")
        call("POST", f"/trips/{tid}/pings", tok, p, key="POST /trips/{id}/pings")
        time.sleep(3)
        rest = sb("GET", f"alerts?select=type,created_at,detail&trip_id=eq.{tid}&type=eq.connection_restored")
        rec("lifecycle", f"{slug} connection_restored on next fix", len(rest) >= 1, f"{[(a['created_at'][11:19] + 'Z', a['detail'][:70]) for a in rest]}")
        code, d = call("POST", f"/trips/{tid}/end", tok, key="POST /trips/{id}/end")
        t = sb("GET", f"trips?select=status,end_reason,ended_at,last_battery,last_net_state,last_heartbeat_at&id=eq.{tid}")[0]
        rec("pipeline", f"{slug} end trip", code == 200 and t["status"] == "completed",
            f"{code} status={t['status']} end_reason={t['end_reason']} last_battery={t['last_battery']} last_net={t['last_net_state']}")
        hb = sb("GET", f"trip_heartbeats?select=sent_at,battery,net_state&trip_id=eq.{tid}")
        rec("pipeline", f"{slug} heartbeat row stored", len(hb) >= 1, f"{len(hb)} rows")


def section_detection(toks, trips):
    for slug, tid in trips.items():
        rows = sb("GET", f"alerts?select=type,occurred_at,created_at,detail,meta&trip_id=eq.{tid}&order=occurred_at")
        types = {}
        for a in rows:
            types.setdefault(a["type"], []).append(a)
        for t in ("trip_started", "speeding", "long_stop", "off_route", "offline", "connection_lost", "connection_restored", "trip_ended"):
            got = types.get(t, [])
            ex = got[0] if got else None
            msg_ar = ((ex or {}).get("meta") or {}).get("message_ar") if ex else None
            rec("detection", f"{slug} {t} written once", len(got) == 1,
                f"count={len(got)} occurred={ex['occurred_at'][11:19] + 'Z' if ex else '-'} "
                f"en='{(ex or {}).get('detail', '')[:70]}' ar='{(msg_ar or '')[:50]}'")
        other = sorted(set(types) - {"trip_started", "speeding", "long_stop", "off_route", "offline", "connection_lost", "connection_restored", "trip_ended"})
        evidence(f"[detection] {slug} other alert types on the trip: {[(k, len(types[k])) for k in other]}")
        # Cairo time inside lifecycle messages. Messages may use 24 h or 12 h and
        # Arabic-Indic digits, so normalise and accept the UTC+2 wall time in
        # either clock, and FAIL if the UTC+3 (old summer) wall time appears.
        trans = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
        for a in rows:
            if a["type"] in ("trip_started", "trip_ended", "connection_lost", "connection_restored"):
                dt = datetime.fromisoformat(a["occurred_at"])
                c2 = dt.astimezone(CAIRO)
                c3 = dt.astimezone(timezone.utc) + timedelta(hours=3)
                forms2 = {c2.strftime("%H:%M"), f"{c2.hour % 12 or 12}:{c2.minute:02d}"}
                forms3 = {c3.strftime("%H:%M"), f"{c3.hour % 12 or 12}:{c3.minute:02d}"}
                meta = a.get("meta") or {}
                for lang, txt in (("en", a.get("detail") or meta.get("message_en") or ""), ("ar", meta.get("message_ar") or "")):
                    norm = txt.translate(trans)
                    if not any(ch.isdigit() for ch in norm):
                        continue
                    ok = any(f in norm for f in forms2) and not any(f in norm for f in forms3 - forms2)
                    rec("timezone", f"{slug} {a['type']} {lang} time is Cairo (UTC+2)", ok,
                        f"occurred={a['occurred_at'][11:16]}Z cairo={sorted(forms2)} utc+3={sorted(forms3)} text='{txt[:90]}'")


def section_concurrency(toks):
    for slug, o in ORGS.items():
        tok = toks[(slug, "drv2")]
        with cf.ThreadPoolExecutor(10) as ex:
            futs = [ex.submit(api, "POST", "/trips/start", tok, {"assignment_id": o["asg_drv2"]["id"]}) for _ in range(10)]
            res = [f.result() for f in futs]
        codes = sorted(r[0] for r in res)
        active = sb("GET", f"trips?select=id,status&driver_id=eq.{o['drv2']['id']}&status=eq.active")
        rec("pipeline", f"{slug} 10 concurrent starts -> one active trip", len(active) == 1 and codes.count(409) >= 1,
            f"codes={codes} active_in_db={len(active)}")
        if not active:
            continue
        tid = active[0]["id"]
        o.setdefault("trips_extra", []).append(tid)
        # cancel is a MANAGER action (require_permission("manage_trips"))
        code, d = call("POST", f"/trips/{tid}/cancel", toks[(slug, "manager")], key="POST /trips/{id}/cancel")
        st = sb("GET", f"trips?select=status,end_reason&id=eq.{tid}")[0]
        code2, d2 = call("POST", "/trips/start", tok, {"assignment_id": o["asg_drv2"]["id"]})
        tid2 = (d2.get("trip") or d2).get("id") if isinstance(d2, dict) else None
        rec("pipeline", f"{slug} cancel resets (can start again)", code == 200 and st["status"] == "cancelled" and code2 in (200, 201),
            f"cancel={code} status={st['status']} reason={st['end_reason']} restart={code2}")
        if tid2:
            o["trips_extra"].append(tid2)
            o["trip2"] = {"id": tid2}
        save_ledger(led)


def section_edit_logs(toks):
    """Edit Logs honoured: disable speeding for school-test, send a 90 km/h fix on
    the restarted drv2 trip -> no speeding alert; then restore the org's EXACT
    previous log_settings via the service role."""
    slug = "school-test"
    o = ORGS[slug]
    tid = (o.get("trip2") or {}).get("id")
    if not tid:
        rec("edit-logs", "speeding disabled is honoured", False, "no trip2")
        return
    org = sb("GET", f"organizations?select=log_settings&id=eq.{o['org_id']}")[0]
    original = org["log_settings"]
    own = mint_impersonation(o["owner_id"], o["org_id"])
    cp, dp = call("PATCH", "/log-settings", own, {"speeding": {"enabled": False}})
    try:
        t0 = utc_now() - timedelta(minutes=3)
        pts = [ping(t0, t, 25 * t, speed=25, battery=70, net="wifi") for t in (0, 10, 20)]
        call("POST", f"/trips/{tid}/pings", toks[(slug, "drv2")], pts, key="POST /trips/{id}/pings")
        sp = sb("GET", f"alerts?select=id&trip_id=eq.{tid}&type=eq.speeding")
        rec("edit-logs", "speeding disabled -> no speeding alert", cp == 200 and len(sp) == 0, f"patch={cp} speeding_alerts={len(sp)}")
    finally:
        sb("PATCH", f"organizations?id=eq.{o['org_id']}", {"log_settings": original}, prefer="return=minimal")
        back = sb("GET", f"organizations?select=log_settings&id=eq.{o['org_id']}")[0]["log_settings"]
        rec("edit-logs", "org log_settings restored exactly", back == original, f"restored={json.dumps(back)}")
    call("POST", f"/trips/{tid}/end", toks[(slug, "drv2")], key="POST /trips/{id}/end")
    # the same trip in the university org, ended normally
    u = ORGS["acme-bus-co"]
    if (u.get("trip2") or {}).get("id"):
        call("POST", f"/trips/{u['trip2']['id']}/end", toks[("acme-bus-co", "drv2")], key="POST /trips/{id}/end")


def section_reads(toks):
    for slug, o in ORGS.items():
        mt = toks[(slug, "manager")]
        today = datetime.now(CAIRO).date().isoformat()
        ch, dh = call("GET", "/history", mt, params={"date_from": today, "date_to": today})
        txt = json.dumps(dh)[:200000]
        rec("reads", f"{slug} History lists the qa trip", ch == 200 and o["trip1"]["id"] in txt, f"{ch} contains_trip={o['trip1']['id'] in txt}")
        cr, dr = call("GET", "/reports", mt, params={"types": "trips", "date_from": today, "date_to": today})
        rec("reads", f"{slug} Reports respond", cr == 200, f"{cr} {str(dr)[:120]}")
        for path in ("/dashboard/summary", "/alerts", "/live/positions", "/notifications/unread-count", "/logs/today"):
            c, d = call("GET", path, mt)
            rec("reads", f"{slug} GET {path}", c == 200, f"{c}")


def main():
    toks = section_auth()
    # Resume: reuse trip1 if it already exists and is still active.
    trips = {}
    for slug, o in ORGS.items():
        t = (o.get("trip1") or {}).get("id")
        if t and sb("GET", f"trips?select=status&id=eq.{t}")[0]["status"] == "active":
            trips[slug] = t
    if len(trips) != len(ORGS):
        trips = section_pipeline(toks)
    else:
        evidence(f"resume: reusing active qa trips {[(k, v[:8]) for k, v in trips.items()]}")
    section_isolation(toks, trips)
    # Pre-clean: cancel (as manager) any drv2 trip left active by an earlier run.
    for slug, o in ORGS.items():
        for t in sb("GET", f"trips?select=id&driver_id=eq.{o['drv2']['id']}&status=eq.active"):
            call("POST", f"/trips/{t['id']}/cancel", toks[(slug, "manager")])
    section_concurrency(toks)
    section_edit_logs(toks)
    section_sweeper_and_end(toks, trips)
    section_detection(toks, trips)
    section_reads(toks)
    lat = {k: {"n": len(v), "p50_ms": round(statistics.median(v) * 1000), "p95_ms": round(sorted(v)[max(0, int(len(v) * .95) - 1)] * 1000)}
           for k, v in sorted(LAT.items())}
    for k, v in lat.items():
        evidence(f"[latency] {k}: n={v['n']} p50={v['p50_ms']}ms p95={v['p95_ms']}ms")
    (OUT / "phase1_results.json").write_text(json.dumps({"results": RESULTS, "latency": lat}, indent=2, ensure_ascii=False), encoding="utf-8")
    fails = [r for r in RESULTS if r["result"] == "FAIL"]
    print(f"\nTOTAL {len(RESULTS)} checks, {len(fails)} FAIL")
    for f in fails:
        print("FAIL", f["section"], f["item"], "|", f["evidence"][:200])


if __name__ == "__main__":
    main()
