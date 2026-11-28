"""The golden vectors the Dart port is checked against must match the CURRENT
Python rule — a change to stop_progress.py / gps_filter.py without
regenerating them (python tests/make_vectors.py) fails here."""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import gps_filter as gf  # noqa: E402
import stop_progress as sp  # noqa: E402

VEC = os.path.join(os.path.dirname(os.path.dirname(HERE)), "shared", "dart", "test", "vectors.json")


@unittest.skipUnless(os.path.exists(VEC), "shared/dart not in this build context")
class VectorsFresh(unittest.TestCase):
    def test_progression_vectors_match_python(self):
        v = json.load(open(VEC, encoding="utf-8"))
        for vec in v["progression"]:
            cfg = sp.config(vec["cfg"])
            st = sp.new_state()
            got = []
            for f in vec["fixes"]:
                st, ds = sp.step(st, vec["stops"], f, cfg)
                got += [[d["type"], d.get("stop_id"), d.get("status")] for d in ds]
            st, ds = sp.finalize(st, vec["stops"], vec["fixes"][-1]["recorded_at"], cfg)
            got += [[d["type"], d.get("stop_id"), d.get("status")] for d in ds]
            self.assertEqual(got, vec["expect"]["decisions"], vec["name"])

    def test_gate_vectors_match_python(self):
        v = json.load(open(VEC, encoding="utf-8"))
        for vec in v["gate"]:
            acc, rej = gf.filter_fixes(vec["fixes"], trip_started_at=vec["trip_started_at"])
            self.assertEqual(len(rej), sum(1 for x in vec["expect"] if x != "ok"), vec["name"])


if __name__ == "__main__":
    unittest.main()
