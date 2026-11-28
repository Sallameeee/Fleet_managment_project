"""Online / offline: heartbeat alive + recent accepted fix = online; alive
without a fix = online_no_gps (the 24 Nov case); nothing = offline."""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import live_status as ls  # noqa: E402

NOW = datetime(2026, 11, 24, 17, 0, tzinfo=timezone.utc)


def ago(s):
    return (NOW - timedelta(seconds=s)).isoformat()


class LiveState(unittest.TestCase):
    def test_online(self):
        st = ls.compute(now=NOW, last_heartbeat_at=ago(40), last_fix_at=ago(6))
        self.assertEqual(st["state"], "online")
        self.assertTrue(st["online"] and st["gps_fresh"])

    def test_24_nov_heartbeats_but_no_fix_is_online_no_gps(self):
        st = ls.compute(now=NOW, last_signal_at=ago(30), last_heartbeat_at=ago(30), last_fix_at=None)
        self.assertEqual(st["state"], "online_no_gps")
        self.assertTrue(st["online"])
        self.assertFalse(st["gps_fresh"])
        self.assertIn("missing", st["reason"])

    def test_stale_fix_but_alive(self):
        st = ls.compute(now=NOW, last_heartbeat_at=ago(20), last_fix_at=ago(600))
        self.assertEqual(st["state"], "online_no_gps")

    def test_offline_when_nothing_recent(self):
        st = ls.compute(now=NOW, last_heartbeat_at=ago(400), last_fix_at=ago(400))
        self.assertEqual(st["state"], "offline")
        self.assertFalse(st["online"])

    def test_one_late_heartbeat_is_tolerated(self):
        st = ls.compute(now=NOW, last_heartbeat_at=ago(130), last_fix_at=None)
        self.assertEqual(st["state"], "online_no_gps")

    def test_fresh_fix_alone_counts_as_alive(self):
        # Pre-046 databases have no heartbeat: a fresh fix proves the app is alive.
        st = ls.compute(now=NOW, last_fix_at=ago(10))
        self.assertEqual(st["state"], "online")

    def test_buffered_backlog_old_fixes_fresh_signal(self):
        # Reconnected after 20 min: the flush arrives now, fixes are old.
        st = ls.compute(now=NOW, last_signal_at=ago(2), last_fix_at=ago(1200))
        self.assertEqual(st["state"], "online_no_gps")

    def test_inactive_trip_is_offline(self):
        st = ls.for_trip({"status": "completed", "last_heartbeat_at": ago(5), "last_fix_at": ago(5)}, now=NOW)
        self.assertEqual(st["state"], "offline")

    def test_state_from(self):
        self.assertEqual(ls.state_from(True, {"recorded_at": ago(5)}, now=NOW), (True, True, "online"))
        self.assertEqual(ls.state_from(True, None, now=NOW), (True, False, "online_no_gps"))
        self.assertEqual(ls.state_from(False, {"recorded_at": ago(5)}, now=NOW), (False, False, "offline"))


if __name__ == "__main__":
    unittest.main()
