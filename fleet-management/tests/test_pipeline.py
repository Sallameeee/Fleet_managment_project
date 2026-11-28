"""END-TO-END through the REAL FastAPI app (in-memory database, no network):
pings -> GPS filter -> storage -> stop progression -> decision log -> live
state -> Trip debug, for a University org, with a School org alongside to prove
the two never see each other. qa_ fixtures only, created and dropped in memory.

Scenarios
  1. a realistic drive (noise, a spike, a cached-fix stretch, a 4-min offline
     gap flushed in order, a highway leg, a skipped stop, a 422 payload, app
     events re-sent twice) -> accepted + SHOWN, correct stop statuses, every
     decision logged, Trip debug explains it, module separation holds
  2. the 24 Nov pattern (heartbeats every minute, no fix) -> online_no_gps,
     never OFFLINE; diagnosis says the phone never delivered a fix
  3. silence -> offline transition logged
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
os.environ["LIFECYCLE_SWEEP"] = "0"

import fake_supabase  # noqa: E402

DB = fake_supabase.install()

from fastapi import Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import auth  # noqa: E402
import gps_sim as g  # noqa: E402
import main  # noqa: E402
import trip_events  # noqa: E402
import trip_lifecycle  # noqa: E402

ORG_U, ORG_S = "11111111-0000-4000-8000-000000000001", "22222222-0000-4000-8000-000000000002"
DRV_U, ADM_U, DRV_S, ADM_S = "aaaaaaaa-0000-4000-8000-0000000000d1", "aaaaaaaa-0000-4000-8000-0000000000a1", \
    "bbbbbbbb-0000-4000-8000-0000000000d2", "bbbbbbbb-0000-4000-8000-0000000000a2"
ROUTE_U, VEH_U = "cccccccc-0000-4000-8000-00000000000r", "dddddddd-0000-4000-8000-00000000000v"
PROFILES = {
    "drv_u": {"id": DRV_U, "org_id": ORG_U, "role": "driver", "permissions": {}, "is_active": True, "name": "qa_ driver U"},
    "adm_u": {"id": ADM_U, "org_id": ORG_U, "role": "owner", "permissions": {}, "is_active": True, "name": "qa_ admin U"},
    "drv_s": {"id": DRV_S, "org_id": ORG_S, "role": "driver", "permissions": {}, "is_active": True, "name": "qa_ driver S"},
    "adm_s": {"id": ADM_S, "org_id": ORG_S, "role": "owner", "permissions": {}, "is_active": True, "name": "qa_ admin S"},
    "staff_u": {"id": "eeeeeeee-0000-4000-8000-000000000005", "org_id": ORG_U, "role": "manager",
                "permissions": {"view_tracking": True}, "is_active": True, "name": "qa_ staff U"},
}

# A 6th-of-October -> Mahalla-like corridor, scaled down (public road points).
STOPS = [(29.9780, 30.9280), (29.9930, 30.9600), (30.0150, 30.9900), (30.0280, 31.0220), (30.0620, 31.0270)]


def fake_user(request: Request) -> dict:
    who = request.headers.get("x-test-user", "")
    if who not in PROFILES:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="no test user")
    return PROFILES[who]


main.app.dependency_overrides[auth.get_current_user] = fake_user
# tracking_rejects verifies the bearer token itself (it runs before any
# dependency): in tests the token IS the test user key.
auth._verify_token = lambda creds: __import__("types").SimpleNamespace(id=PROFILES[creds.credentials]["id"])
CLIENT = TestClient(main.app)


def H(who):
    return {"x-test-user": who, "Authorization": f"Bearer {who}"}


def seed(started_at: datetime, trip_id: str) -> None:
    DB.reset()
    trip_events._probe.update(ok=None, at=0.0)
    trip_events._module_cache.clear()
    trip_lifecycle._probe.clear()
    t = DB.tables
    t["organizations"] = [
        {"id": ORG_U, "name": "qa_ Pipeline University", "slug": "qa-pipe-uni", "module": "university", "status": "active",
         "log_settings": {}, "enabled_features": None, "long_stop_minutes": 5},
        {"id": ORG_S, "name": "qa_ Pipeline School", "slug": "qa-pipe-school", "module": "school", "status": "active",
         "log_settings": {}, "enabled_features": None, "long_stop_minutes": 5},
    ]
    t["profiles"] = [dict(p) for p in PROFILES.values()]
    t["routes"] = [{"id": ROUTE_U, "org_id": ORG_U, "name": "qa_ Oct -> Mahalla (scaled)", "geometry": None}]
    t["route_stops"] = [{"id": f"stop-{i + 1}", "route_id": ROUTE_U, "stop_order": i + 1, "name": f"qa_ Stop {i + 1}",
                         "lat": la, "lng": ln, "dwell_minutes": 0} for i, (la, ln) in enumerate(STOPS)]
    t["vehicles"] = [{"id": VEH_U, "org_id": ORG_U, "bus_number": "QA-1", "share_token": "qa-share"}]
    t["trips"] = [{"id": trip_id, "org_id": ORG_U, "driver_id": DRV_U, "route_id": ROUTE_U, "vehicle_id": VEH_U,
                   "assignment_id": "qa-assignment", "status": "active", "started_at": started_at.isoformat(),
                   "ended_at": None, "progress_version": 0}]
    t["alerts"] = []
    t["stop_events"] = []
    t["stop_visits"] = []
    t["location_pings"] = []
    t["trip_heartbeats"] = []
    t["trip_events"] = []
    t["assignments"] = []
    t["alert_rules"] = []


def post_fixes(trip_id, fixes, who="drv_u"):
    r = CLIENT.post(f"/trips/{trip_id}/pings", json=fixes, headers=H(who))
    assert r.status_code == 201, r.text
    return r.json()


class Drive(unittest.TestCase):
    TRIP = "f0000000-0000-4000-8000-000000000001"

    @classmethod
    def setUpClass(cls):
        now = datetime.now(timezone.utc)
        cls.T0 = now - timedelta(minutes=45)
        seed(cls.T0, cls.TRIP)
        s = [tuple(x) for x in STOPS]
        tr = g.Track(g.offset(s[0], 0, -350), cls.T0 + timedelta(seconds=3), seed=42, sigma_m=22, clip_m=40)
        tr.drive(s[0], 25).dwell(60)                                  # stop 1: arrive + dwell
        tr.drive(g.offset(s[1], 0, -1500), 60).spike(3000)            # one impossible fix
        tr.drive(s[1], 60).drive(g.offset(s[1], 400, 1500), 60)       # stop 2: drive straight through
        tr.cached_repeat(6, then_to=g.offset(s[2], 700, -800), kmh=60)  # cached fix while moving
        tr.drive(g.offset(s[2], 700, 0), 60)                           # stop 3: 700 m away -> skipped
        cls.offline_from = len(tr.fixes)
        tr.drive(g.offset(s[3], -300, -600), 110)                      # highway leg (offline part)
        cls.offline_to = len(tr.fixes)
        tr.drive(s[3], 110).drive(g.offset(s[3], 600, 300), 100)       # stop 4: through the radius at 110
        tr.drive(s[4], 70).dwell(90)                                   # stop 5: arrive + dwell
        cls.fixes = tr.fixes
        cls.responses = []
        before = tr.fixes[:cls.offline_from]
        backlog = tr.fixes[cls.offline_from:cls.offline_to]
        after = tr.fixes[cls.offline_to:]
        live = before + after
        # Online: 30 s app flushes (6 fixes). Offline: buffered, then flushed
        # IN ORDER in one request after reconnect, before newer fixes.
        for chunk in (before, None, after):
            if chunk is None:
                cls.responses.append(post_fixes(cls.TRIP, backlog))
                continue
            for i in range(0, len(chunk), 6):
                cls.responses.append(post_fixes(cls.TRIP, chunk[i:i + 6]))
        # A re-sent batch (the app lost the 201): must be skipped as duplicates.
        cls.resend = post_fixes(cls.TRIP, live[:6])
        # Heartbeats + a payload the backend must refuse (lat 95) -> 422, logged.
        CLIENT.post(f"/trips/{cls.TRIP}/heartbeat", json=[{"sent_at": (cls.T0 + timedelta(seconds=60 * k)).isoformat(),
                                                           "battery": 80 - k, "net_state": "online"} for k in range(1, 4)],
                    headers=H("drv_u"))
        cls.bad = CLIENT.post(f"/trips/{cls.TRIP}/pings", json=[{"lat": 95, "lng": 31, "recorded_at": now.isoformat()}],
                              headers=H("drv_u"))
        # App decision log, sent twice (offline resend) -> stored once.
        app_batch = {"app_version": "2.0.0", "build": "42", "platform": "android 14", "events": [
            {"id": "e1", "type": "gps.sampled", "at": cls.T0.isoformat(), "data": {"lat": 29.978, "lng": 30.928, "accuracy": 12}},
            {"id": "e2", "type": "gate.spike_drop", "at": cls.T0.isoformat(), "message": "dropped by SpikeGate", "data": {"implied_kmh": 2100}},
            {"id": "e3", "type": "queue.sent", "at": cls.T0.isoformat(), "data": {"count": 6, "status": 201}},
        ]}
        cls.app1 = CLIENT.post(f"/trips/{cls.TRIP}/events", json=app_batch, headers=H("drv_u")).json()
        cls.app2 = CLIENT.post(f"/trips/{cls.TRIP}/events", json=app_batch, headers=H("drv_u")).json()
        cls.progress = CLIENT.get(f"/trips/{cls.TRIP}/progress", headers=H("drv_u")).json()
        cls.end = CLIENT.post(f"/trips/{cls.TRIP}/end", json={}, headers=H("drv_u"))
        cls.debug = CLIENT.get(f"/trip-debug/{cls.TRIP}", headers=H("adm_u"))

    def test_every_fix_stored_spike_tagged(self):
        pings = DB.tables["location_pings"]
        self.assertEqual(len(pings), len(self.fixes))
        tagged = [p for p in pings if p.get("is_outlier")]
        self.assertEqual([p["reject_reason"] for p in tagged], ["impossible_speed"])

    def test_resent_batch_skipped(self):
        self.assertEqual(self.resend["recorded"], 0)
        self.assertEqual(self.resend["duplicates_skipped"], 6)

    def test_offline_backlog_flushed_in_order_and_accepted(self):
        stored = sorted(p["recorded_at"] for p in DB.tables["location_pings"])
        backlog = [f["recorded_at"] for f in self.fixes[self.offline_from:self.offline_to]]
        self.assertTrue(all(b.replace("Z", "+00:00") in stored for b in backlog))

    def test_stop_statuses(self):
        v = {r["stop_id"]: r["status"] for r in DB.tables["stop_visits"]}
        self.assertEqual([v.get(f"stop-{i}") for i in range(1, 6)], ["visited", "passed", "skipped", "passed", "visited"])
        self.assertTrue(all(r.get("decided_by") == "server" for r in DB.tables["stop_visits"]))

    def test_decisions_logged(self):
        types = {e["type"] for e in DB.tables["trip_events"]}
        for t in ("ping_batch", "ping_rejected", "stop_arrived", "stop_passed", "stop_skipped", "next_stop_changed",
                  "heartbeat", "request_rejected", "gps.sampled", "lifecycle_trip_ended"):
            self.assertIn(t, types)
        self.assertNotIn("progress_fixes_ignored", types)  # in-order flush: every fix used
        rej = [e for e in DB.tables["trip_events"] if e["type"] == "ping_rejected"][0]
        self.assertEqual(rej["payload"]["reason"], "impossible_speed")
        self.assertIn("implied_kmh", rej["payload"])
        skipped = [e for e in DB.tables["trip_events"] if e["type"] == "stop_skipped"][0]
        self.assertGreater(skipped["payload"]["closest_m"], skipped["payload"]["pass_radius_m"])
        self.assertTrue(all(e["module"] == "university" and e["org_id"] == ORG_U for e in DB.tables["trip_events"]))

    def test_422_kept_and_logged(self):
        self.assertEqual(self.bad.status_code, 422)
        ev = [e for e in DB.tables["trip_events"] if e["type"] == "request_rejected"][0]
        self.assertTrue(any("lat" in err["loc"] for err in ev["payload"]["errors"]), ev["payload"]["errors"])

    def test_app_events_idempotent(self):
        self.assertEqual(self.app1["stored"], 3)
        self.assertEqual(self.app2["stored"], 0)
        app_rows = [e for e in DB.tables["trip_events"] if e["source"] == "app"]
        self.assertEqual(len(app_rows), 3)
        self.assertEqual(app_rows[0]["payload"]["_app"]["build"], "42")

    def test_progress_endpoint(self):
        self.assertEqual(self.progress["progress"]["total"], 5)
        self.assertEqual(self.progress["live"]["state"] in ("online", "online_no_gps"), True)

    def test_trip_end_ok(self):
        self.assertEqual(self.end.status_code, 200, self.end.text)

    def test_trip_debug_explains(self):
        self.assertEqual(self.debug.status_code, 200, self.debug.text)
        d = self.debug.json()
        self.assertEqual(d["counts"]["fixes"], len(self.fixes))
        self.assertEqual(d["counts"]["rejected_by_reason"], {"impossible_speed": 1})
        self.assertEqual(d["counts"]["stop_visits_by_status"], {"visited": 2, "passed": 2, "skipped": 1})
        self.assertTrue(any("rejected" in x for x in d["diagnosis"]))
        red = [f for f in d["fixes"] if not f["accepted"]]
        self.assertEqual(len(red), 1)
        self.assertEqual(red[0]["reason"], "impossible_speed")

    def test_trip_debug_export(self):
        r = CLIENT.get(f"/trip-debug/{self.TRIP}/export?format=csv", headers=H("adm_u"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("time_cairo", r.text.splitlines()[0])
        r = CLIENT.get(f"/trip-debug/{self.TRIP}/export?format=json", headers=H("adm_u"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["module"], "university")

    def test_module_and_role_separation(self):
        self.assertEqual(CLIENT.get(f"/trip-debug/{self.TRIP}", headers=H("adm_s")).status_code, 404)  # School admin
        self.assertEqual(CLIENT.get(f"/trip-debug/{self.TRIP}", headers=H("drv_u")).status_code, 403)  # driver
        self.assertEqual(CLIENT.get(f"/trip-debug/{self.TRIP}", headers=H("staff_u")).status_code, 403)  # no manage_settings
        self.assertEqual(CLIENT.post(f"/trips/{self.TRIP}/events", json={"events": []}, headers=H("drv_s")).status_code, 404)
        listed = CLIENT.get("/trip-debug/trips", headers=H("adm_s")).json()
        self.assertEqual(listed["count"], 0)
        self.assertEqual(listed["module"], "school")


class NoGpsButAlive(unittest.TestCase):
    """The 24 Nov pattern: heartbeats every minute, zero fixes."""
    TRIP = "f0000000-0000-4000-8000-000000000002"

    @classmethod
    def setUpClass(cls):
        now = datetime.now(timezone.utc)
        seed(now - timedelta(minutes=10), cls.TRIP)
        beats = [{"sent_at": (now - timedelta(minutes=10) + timedelta(seconds=60 * k)).isoformat(), "battery": 72,
                  "net_state": "online"} for k in range(10)]
        for b in beats:
            CLIENT.post(f"/trips/{cls.TRIP}/heartbeat", json=b, headers=H("drv_u"))
        cls.sweep = trip_lifecycle.sweep_once(only_trip_ids={cls.TRIP}, now=now)
        cls.positions = CLIENT.get("/live/positions", headers=H("adm_u")).json()["drivers"]
        cls.debug = CLIENT.get(f"/trip-debug/{cls.TRIP}", headers=H("adm_u")).json()

    def test_live_state_is_online_no_gps_not_offline(self):
        me = [d for d in self.positions if d["driver_id"] == DRV_U][0]
        self.assertTrue(me["online"])
        self.assertEqual(me["live_state"], "online_no_gps")

    def test_transition_logged_with_inputs(self):
        ev = [e for e in DB.tables["trip_events"] if e["type"] == "live_state"][0]
        self.assertEqual(ev["payload"]["to"], "online_no_gps")
        self.assertIn("missing", ev["message"])

    def test_diagnosis_names_the_failure(self):
        self.assertIn("NO GPS fix ever reached the server", self.debug["diagnosis"][0])
        self.assertEqual(self.debug["live"]["state"], "online_no_gps")


class Silence(unittest.TestCase):
    TRIP = "f0000000-0000-4000-8000-000000000003"

    def test_offline_after_silence(self):
        now = datetime.now(timezone.utc)
        seed(now - timedelta(minutes=20), self.TRIP)
        CLIENT.post(f"/trips/{self.TRIP}/heartbeat", json={"sent_at": now.isoformat(), "battery": 50, "net_state": "online"},
                    headers=H("drv_u"))
        trip_lifecycle.sweep_once(only_trip_ids={self.TRIP}, now=now)
        trip_lifecycle.sweep_once(only_trip_ids={self.TRIP}, now=now + timedelta(minutes=4))
        states = [e["payload"]["to"] for e in DB.tables["trip_events"] if e["type"] == "live_state"]
        self.assertEqual(states, ["online_no_gps", "offline"])


if __name__ == "__main__":
    unittest.main()
