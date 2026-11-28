"""GPS plausibility rule under REALISTIC GPS (noise, highway, slow traffic,
cached repeats) — the protections that work must keep working."""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gps_filter as gf  # noqa: E402
import gps_sim as g  # noqa: E402

T0 = datetime(2026, 11, 24, 16, 10, tzinfo=timezone.utc)
A = (30.0300, 31.0000)


def reasons(rej):
    out = {}
    for r in rej:
        out[r["reject_reason"]] = out.get(r["reject_reason"], 0) + 1
    return out


class RealWorldAccepts(unittest.TestCase):
    def test_highway_130kmh_noise_30m_no_false_rejections(self):
        tr = g.Track(A, T0, seed=1, sigma_m=18, clip_m=40)
        tr.drive(g.offset(A, 0, 25_000), 130)
        acc, rej = gf.filter_fixes(tr.fixes)
        self.assertEqual(reasons(rej), {}, rej[:3])
        self.assertEqual(len(acc), len(tr.fixes))

    def test_highway_120kmh_without_reported_accuracy(self):
        tr = g.Track(A, T0, seed=2, sigma_m=15, clip_m=35)
        tr.drive(g.offset(A, 0, 15_000), 120)
        fixes = [{k: v for k, v in f.items() if k != "accuracy"} for f in tr.fixes]
        _, rej = gf.filter_fixes(fixes)
        self.assertLessEqual(len(rej), 1, reasons(rej))

    def test_slow_traffic_with_jitter(self):
        tr = g.Track(A, T0, seed=3, sigma_m=20, clip_m=40)
        tr.drive(g.offset(A, 600, 0), 6).dwell(120).drive(g.offset(A, 900, 0), 12)
        _, rej = gf.filter_fixes(tr.fixes)
        self.assertEqual(reasons(rej), {})

    def test_cached_repeats_then_catch_up_jump_is_accepted(self):
        # The phone re-sends one cached fix for 40 s while the bus moves ~670 m,
        # then the real position catches up in one 5 s interval.
        tr = g.Track(A, T0, seed=4, sigma_m=8, clip_m=20)
        tr.drive(g.offset(A, 0, 500), 60)
        tr.cached_repeat(8, then_to=g.offset(A, 0, 1170), kmh=60)
        tr.drive(g.offset(A, 0, 1600), 60)
        acc, rej = gf.filter_fixes(tr.fixes)
        self.assertEqual(reasons(rej), {}, rej[:2])
        self.assertGreaterEqual(sum(1 for a in acc if a.get("stale_repeat")), 7)


class ProtectionsStillWork(unittest.TestCase):
    def test_exact_duplicate_dropped(self):
        f = {"lat": 30.0, "lng": 31.0, "recorded_at": T0.isoformat()}
        acc, rej = gf.filter_fixes([f, dict(f)])
        self.assertEqual(len(acc), 1)
        self.assertEqual(reasons(rej), {"duplicate": 1})

    def test_single_spike_loses_exactly_one_point(self):
        tr = g.Track(A, T0, seed=5, sigma_m=10, clip_m=25)
        tr.drive(g.offset(A, 0, 400), 40).spike(3000).drive(g.offset(A, 0, 800), 40)
        acc, rej = gf.filter_fixes(tr.fixes)
        self.assertEqual(reasons(rej), {"impossible_speed": 1})
        self.assertEqual(len(acc), len(tr.fixes) - 1)

    def test_teleport_reanchors_after_three(self):
        tr = g.Track(A, T0, seed=6, sigma_m=5, clip_m=10)
        tr.drive(g.offset(A, 0, 200), 30)
        tr.gap(0, to=g.offset(A, 8000, 0))  # GPS restarts 8 km away, no time passes
        tr.drive(g.offset(A, 8000, 300), 30)
        acc, rej = gf.filter_fixes(tr.fixes)
        self.assertEqual(reasons(rej), {"impossible_speed": 2})
        self.assertTrue(any(a.get("reanchored") for a in acc))

    def test_same_timestamp_two_places(self):
        a = {"lat": 30.0, "lng": 31.0, "recorded_at": T0.isoformat()}
        b = {"lat": 30.01, "lng": 31.0, "recorded_at": T0.isoformat()}
        _, rej = gf.filter_fixes([a, b])
        self.assertEqual(reasons(rej), {"same_timestamp": 1})

    def test_low_accuracy_tagged(self):
        a = {"lat": 30.0, "lng": 31.0, "recorded_at": T0.isoformat(), "accuracy": 12}
        b = {"lat": 30.0005, "lng": 31.0, "recorded_at": (T0 + timedelta(seconds=5)).isoformat(), "accuracy": 1500}
        acc, rej = gf.filter_fixes([a, b])
        self.assertEqual(reasons(rej), {"low_accuracy": 1})
        self.assertEqual(rej[0]["why"]["accuracy_m"], 1500)

    def test_before_trip_start_tagged(self):
        old = {"lat": 30.0, "lng": 31.0, "recorded_at": (T0 - timedelta(hours=3)).isoformat()}
        new = {"lat": 30.0, "lng": 31.0001, "recorded_at": (T0 + timedelta(seconds=5)).isoformat()}
        acc, rej = gf.filter_fixes([old, new], trip_started_at=T0.isoformat())
        self.assertEqual(reasons(rej), {"before_trip_start": 1})
        self.assertEqual(len(acc), 1)

    def test_rejection_carries_inputs(self):
        tr = g.Track(A, T0, seed=7, sigma_m=5, clip_m=10)
        tr.drive(g.offset(A, 0, 200), 30).spike(5000)
        _, rej = gf.filter_fixes(tr.fixes)
        why = rej[0]["why"]
        for k in ("dist_m", "allowance_m", "elapsed_s", "ceiling_kmh"):
            self.assertIn(k, why)


class NaiveTimestamps(unittest.TestCase):
    CAIRO = ZoneInfo("Africa/Cairo")

    def test_naive_local_cairo_time_is_resolved(self):
        now = datetime(2026, 11, 24, 16, 10, 5, tzinfo=timezone.utc)  # 18:10:05 Cairo (UTC+2, no DST)
        naive = datetime(2026, 11, 24, 18, 10, 0)
        dt, used_local = gf.resolve_naive_timestamp(naive, now, self.CAIRO, timedelta(minutes=10))
        self.assertEqual(used_local, 1)
        self.assertEqual(dt, datetime(2026, 11, 24, 16, 10, 0, tzinfo=timezone.utc))

    def test_naive_utc_stays_utc(self):
        now = datetime(2026, 11, 24, 16, 10, 5, tzinfo=timezone.utc)
        dt, used_local = gf.resolve_naive_timestamp(datetime(2026, 11, 24, 16, 9, 58), now, self.CAIRO, timedelta(minutes=10))
        self.assertEqual(used_local, 0)
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_summer_dst_offset_comes_from_tzdb(self):
        # Egypt summer time (UTC+3) — the offset is never hardcoded.
        now = datetime(2026, 7, 1, 9, 0, 5, tzinfo=timezone.utc)
        dt, used_local = gf.resolve_naive_timestamp(datetime(2026, 7, 1, 12, 0, 0), now, self.CAIRO, timedelta(minutes=10))
        self.assertEqual(used_local, 1)
        self.assertEqual(dt, datetime(2026, 7, 1, 9, 0, 0, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
