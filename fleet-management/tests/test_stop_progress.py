"""Shared stop progression under realistic GPS: noise +/-20-40 m, ~5 s cadence,
highway segments, stops close together, passed twice, doubling back, starting
away from stop 1, drift while parked."""

import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gps_sim as g  # noqa: E402
import stop_progress as sp  # noqa: E402

T0 = datetime(2026, 11, 24, 16, 0, tzinfo=timezone.utc)
A = (30.0000, 31.0000)


def run(stops, fixes, cfg=None, end=True):
    st = sp.new_state()
    log = []
    for f in fixes:
        st, ds = sp.step(st, stops, f, cfg)
        log += ds
    if end:
        st, ds = sp.finalize(st, stops, fixes[-1]["recorded_at"], cfg)
        log += ds
    return st, log


def statuses(st, stops):
    return [st["stops"].get(s["id"], {}).get("status") for s in stops]


def events(log, type_):
    return [d for d in log if d["type"] == type_]


def line(n, spacing_m, start=A):
    return [g.offset(start, 0, i * spacing_m) for i in range(n)]


class Basics(unittest.TestCase):
    def test_arrive_and_dwell_is_visited(self):
        pts = line(3, 1500)
        stops = g.stops_from(pts)
        tr = g.Track(g.offset(A, 0, -500), T0, seed=1)
        for p in pts:
            tr.drive(p, 35).dwell(45)
        st, log = run(stops, tr.fixes)
        self.assertEqual(statuses(st, stops), ["visited"] * 3)
        self.assertEqual(len(events(log, "stop_arrived")), 3)

    def test_drive_through_radius_without_stopping_is_passed(self):
        pts = line(3, 1500)
        stops = g.stops_from(pts)
        tr = g.Track(g.offset(A, 0, -500), T0, seed=2, sigma_m=15)
        tr.drive(pts[0], 35).dwell(40).drive(pts[2], 50).dwell(40)
        st, log = run(stops, tr.fixes)
        self.assertEqual(statuses(st, stops), ["visited", "passed", "visited"])
        msg = [d["message"] for d in events(log, "stop_passed")][0]
        self.assertIn("without stopping", msg)

    def test_go_by_at_120m_is_passed_not_skipped(self):
        stops = g.stops_from([A, g.offset(A, 120, 2000), g.offset(A, 0, 4000)])
        tr = g.Track(A, T0, seed=3, sigma_m=12)
        tr.dwell(30).drive(g.offset(A, 0, 2000), 50).drive(stops_pt(stops, 2), 50).dwell(30)
        st, log = run(stops, tr.fixes)
        self.assertEqual(statuses(st, stops), ["visited", "passed", "visited"])
        self.assertLess(events(log, "stop_passed")[0]["payload"]["closest_m"], 150)

    def test_far_away_stop_is_skipped_with_reason(self):
        stops = g.stops_from([A, g.offset(A, 900, 2000), g.offset(A, 0, 4000)])
        tr = g.Track(A, T0, seed=4)
        tr.dwell(30).drive(stops_pt(stops, 2), 60).dwell(30)
        st, log = run(stops, tr.fixes)
        self.assertEqual(statuses(st, stops), ["visited", "skipped", "visited"])
        sk = events(log, "stop_skipped")[0]
        self.assertGreater(sk["payload"]["closest_m"], 800)
        self.assertIn("moved on", sk["message"])


class RealWorld(unittest.TestCase):
    def test_highway_100kmh_5s_cannot_jump_over_a_stop(self):
        # 139 m between fixes > 2 x 60 m radius: segment closest-approach catches it.
        pts = line(4, 6000)
        stops = g.stops_from(pts)
        tr = g.Track(g.offset(A, 0, -300), T0, seed=5, sigma_m=20, cadence_s=5)
        tr.drive(pts[0], 40).dwell(40).drive(pts[3], 100).dwell(40)
        st, _ = run(stops, tr.fixes)
        self.assertEqual(statuses(st, stops)[1:3], ["passed", "passed"])
        self.assertEqual(statuses(st, stops)[3], "visited")

    def test_stops_close_together(self):
        stops = g.stops_from([A, g.offset(A, 0, 45), g.offset(A, 0, 1500)])
        tr = g.Track(g.offset(A, 0, -400), T0, seed=6, sigma_m=10)
        tr.drive(A, 30).dwell(30).drive(g.offset(A, 0, 45), 10).dwell(30).drive(stops_pt(stops, 2), 40).dwell(30)
        st, log = run(stops, tr.fixes)
        self.assertEqual(statuses(st, stops), ["visited", "visited", "visited"])

    def test_gps_drift_while_parked_never_flips_the_next_stop(self):
        stops = g.stops_from(line(2, 2000))
        tr = g.Track(g.offset(A, 0, -300), T0, seed=7, sigma_m=25, clip_m=40)
        tr.drive(A, 30).dwell(300).drive(stops_pt(stops, 1), 40).dwell(20)
        st, log = run(stops, tr.fixes)
        changes = [d for d in events(log, "next_stop_changed") if d.get("stop_id") == "s2"]
        self.assertEqual(len(changes), 1)
        self.assertEqual(statuses(st, stops), ["visited", "visited"])

    def test_start_away_from_stop_1_then_drive_to_it(self):
        pts = line(3, 1500)
        stops = g.stops_from(pts)
        # depot 2 km BEYOND stop 1 toward stop 2 — then the bus drives back to stop 1
        tr = g.Track(g.offset(A, 300, 2000), T0, seed=8)
        tr.dwell(20).drive(A, 40).dwell(40).drive(pts[1], 40).dwell(30).drive(pts[2], 40).dwell(30)
        st, _ = run(stops, tr.fixes)
        self.assertEqual(statuses(st, stops), ["visited", "visited", "visited"])

    def test_start_beyond_stop_1_driving_away_skips_it(self):
        pts = line(3, 1500)
        stops = g.stops_from(pts)
        tr = g.Track(g.offset(A, 400, 700), T0, seed=9)
        tr.drive(pts[1], 40).dwell(30).drive(pts[2], 40).dwell(30)
        st, _ = run(stops, tr.fixes)
        self.assertEqual(statuses(st, stops), ["skipped", "visited", "visited"])

    def test_skipped_then_reached_by_doubling_back_is_upgraded(self):
        stops = g.stops_from([A, g.offset(A, 700, 1500), g.offset(A, 0, 3000)])
        tr = g.Track(A, T0, seed=10)
        tr.dwell(30).drive(g.offset(A, 0, 2600), 50)  # far past stop 2 (700 m off)
        tr.drive(stops_pt(stops, 1), 40).dwell(40)   # doubles back and really stops there
        tr.drive(stops_pt(stops, 2), 40).dwell(30)
        st, log = run(stops, tr.fixes)
        self.assertIn(statuses(st, stops)[1], ("passed", "visited"))
        self.assertTrue(events(log, "stop_upgraded"))
        self.assertEqual(statuses(st, stops)[2], "visited")

    def test_later_stop_on_the_road_before_current_stop_does_not_steal(self):
        # stop 3 lies between stop 1 and stop 2 on the outbound road.
        s1, s2, s3 = A, g.offset(A, 0, 3000), g.offset(A, 0, 1500)
        stops = g.stops_from([s1, s2, s3])
        tr = g.Track(A, T0, seed=11, sigma_m=10)
        tr.dwell(30).drive(s2, 40).dwell(40).drive(s3, 40).dwell(40)
        st, log = run(stops, tr.fixes)
        self.assertEqual(statuses(st, stops), ["visited", "visited", "visited"])

    def test_stop_passed_twice_logs_reentry_once(self):
        # loop route: last stop == first stop location
        stops = g.stops_from([A, g.offset(A, 0, 1500), g.offset(A, 1500, 1500), g.offset(A, 0, 0)])
        tr = g.Track(A, T0, seed=12)
        tr.dwell(30).drive(stops_pt(stops, 1), 40).dwell(30).drive(stops_pt(stops, 2), 40).dwell(30).drive(A, 40).dwell(40)
        st, log = run(stops, tr.fixes)
        self.assertEqual(statuses(st, stops), ["visited"] * 4)

    def test_trip_ended_early_skips_the_rest(self):
        stops = g.stops_from(line(4, 1500))
        tr = g.Track(A, T0, seed=13)
        tr.dwell(30).drive(stops_pt(stops, 1), 40).dwell(30)
        st, log = run(stops, tr.fixes)
        self.assertEqual(statuses(st, stops), ["visited", "visited", "skipped", "skipped"])
        self.assertTrue(all("trip ended" in d["message"] for d in events(log, "stop_skipped")))

    def test_out_of_order_fix_is_ignored(self):
        stops = g.stops_from(line(2, 1000))
        tr = g.Track(A, T0, seed=14)
        tr.dwell(20)
        st, _ = run(stops, tr.fixes, end=False)
        st, ds = sp.step(st, stops, dict(tr.fixes[0]))
        self.assertEqual(ds[0]["type"], "fix_out_of_order")

    def test_configurable_radius(self):
        cfg = sp.config({"arrive_radius_m": 30})
        self.assertEqual(cfg["arrive_radius_m"], 30)
        self.assertEqual(cfg["pass_radius_m"], 150)
        self.assertEqual(sp.config({"arrive_radius_m": 5000})["arrive_radius_m"], 300)
        self.assertEqual(sp.config({"arrive_radius_m": 100})["pass_radius_m"], 250)

    def test_summary_next_stop_and_eta(self):
        stops = g.stops_from(line(3, 2000))
        tr = g.Track(A, T0, seed=15)
        tr.dwell(30).drive(g.offset(A, 0, 1000), 40)
        st, _ = run(stops, tr.fixes, end=False)
        s = sp.summary(st, stops)
        self.assertEqual(s["next_stop"]["stop_id"], "s2")
        self.assertGreater(s["next_stop"]["eta_s"], 30)
        self.assertEqual(s["done"], 1)


def stops_pt(stops, i):
    return (stops[i]["lat"], stops[i]["lng"])


if __name__ == "__main__":
    unittest.main()
