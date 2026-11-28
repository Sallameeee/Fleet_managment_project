"""Deterministic, realistic GPS track generator for tests and replays.

Not "perfect lines": every fix carries Gaussian position noise (default
sigma 15 m, clipped to +/-40 m), a reported accuracy, device speed with noise,
and the cadence jitters around 5 s. Segments can be driven at highway speed,
crawl in traffic, dwell at a stop, and the generator can inject the real-phone
artefacts seen in the September forensics: a cached fix repeated with fresh
timestamps, then a catch-up jump.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

EARTH = 6_371_000.0


def dist_m(a, b) -> float:
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH * math.asin(min(1.0, math.sqrt(h)))


def offset(p, north_m: float, east_m: float):
    lat = p[0] + math.degrees(north_m / EARTH)
    lng = p[1] + math.degrees(east_m / (EARTH * math.cos(math.radians(p[0]))))
    return (lat, lng)


def lerp(a, b, f):
    return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)


class Track:
    """Build a drive leg by leg; `fixes` holds dicts shaped like PingIn."""

    def __init__(self, start, t0: datetime, *, seed: int = 7, sigma_m: float = 15.0, clip_m: float = 40.0,
                 cadence_s: float = 5.0, jitter_s: float = 0.6):
        self.pos = start
        self.t = t0 if t0.tzinfo else t0.replace(tzinfo=timezone.utc)
        self.rng = random.Random(seed)
        self.sigma, self.clip = sigma_m, clip_m
        self.cadence, self.jitter = cadence_s, jitter_s
        self.fixes: list = []
        self.truth: list = []

    def _noise(self):
        n = max(-self.clip, min(self.clip, self.rng.gauss(0, self.sigma)))
        e = max(-self.clip, min(self.clip, self.rng.gauss(0, self.sigma)))
        return n, e

    def _emit(self, true_pos, speed_mps):
        n, e = self._noise()
        lat, lng = offset(true_pos, n, e)
        self.fixes.append({
            "lat": round(lat, 7), "lng": round(lng, 7),
            "speed": round(max(0.0, speed_mps + self.rng.gauss(0, 0.4)), 2),
            "accuracy": round(abs(self.rng.gauss(self.sigma, 4)) + 3, 1),
            "recorded_at": self.t.isoformat().replace("+00:00", "Z"),
        })
        self.truth.append(true_pos)

    def _tick(self):
        dt = self.cadence + self.rng.uniform(-self.jitter, self.jitter)
        self.t += timedelta(seconds=dt)
        return dt

    def drive(self, to, kmh: float):
        """Drive in a straight line to `to` at ~kmh (+/-8 % variation)."""
        total = dist_m(self.pos, to)
        done = 0.0
        start = self.pos
        while done < total:
            v = kmh / 3.6 * (1 + self.rng.uniform(-0.08, 0.08))
            dt = self._tick()
            done = min(total, done + v * dt)
            self.pos = lerp(start, to, done / total if total else 1.0)
            self._emit(self.pos, v)
        return self

    def dwell(self, seconds: float):
        end = self.t + timedelta(seconds=seconds)
        while self.t < end:
            self._tick()
            self._emit(self.pos, 0.0)
        return self

    def cached_repeat(self, count: int, then_to=None, kmh: float = 60):
        """Real-phone artefact: the SAME cached fix re-stamped `count` times while
        the bus keeps moving, then the real position catches up."""
        if not self.fixes:
            self._emit(self.pos, 0.0)
        cached = dict(self.fixes[-1])
        start = self.pos
        for i in range(count):
            dt = self._tick()
            if then_to is not None:
                f = min(1.0, (i + 1) * (kmh / 3.6) * dt / max(1.0, dist_m(start, then_to)))
                self.pos = lerp(start, then_to, f)
            row = dict(cached)
            row["recorded_at"] = self.t.isoformat().replace("+00:00", "Z")
            row["speed"] = 0.0
            self.fixes.append(row)
            self.truth.append(self.pos)
        return self

    def gap(self, seconds: float, to=None):
        """No fixes for `seconds` (tunnel / GPS off); the bus may move meanwhile."""
        self.t += timedelta(seconds=seconds)
        if to is not None:
            self.pos = to
        return self

    def spike(self, north_m: float = 3000.0):
        """One genuinely impossible fix (multipath / bad cell fix)."""
        self._tick()
        bad = offset(self.pos, north_m, 0)
        self.fixes.append({"lat": bad[0], "lng": bad[1], "speed": 0.0, "accuracy": 900.0,
                           "recorded_at": self.t.isoformat().replace("+00:00", "Z")})
        self.truth.append(self.pos)
        return self


def stops_from(points, names=None):
    return [{"id": f"s{i + 1}", "stop_order": i + 1, "name": (names[i] if names else f"Stop {i + 1}"),
             "lat": p[0], "lng": p[1]} for i, p in enumerate(points)]
