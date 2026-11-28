// GPS GATE — the driver app's copy of fleet-management/gps_filter.py
// (filter_fixes). Replaces the app's old `_SpikeGate`, whose 120 km/h ceiling
// with no accuracy allowance dropped real highway fixes and catch-up fixes on
// the real phone (see docs/tracking-fix-progress.md).
//
// IMPORTANT for the app: the gate decides what the LIVE MARKER and the stop
// progression use. It must NEVER decide what is buffered or uploaded — every
// raw fix is buffered and sent; the server applies the same rule and stores
// rejects tagged with their reason. A gate that keeps a fix out of the upload
// queue makes failures silent (the 24 Nov trips stored zero fixes).
//
// Same constants, same decisions as the Python rule; checked against the shared
// golden vectors (test/vectors.json): `dart run test/vectors_test.dart`.

import 'dart:math' as math;

import 'stop_progress.dart' show haversineM;

const double spikeCeilingKmh = 160.0;
const double minDtS = 1.0;
const int reanchorAfter = 3;
const double defaultAccuracyM = 20.0;
const double maxAllowanceM = 100.0;
const double lowAccuracyM = 150.0;
const double beforeStartToleranceS = 120.0;
const double _samePointM = 0.5;
const double _repeatEpsDeg = 1e-7;

class GateVerdict {
  final bool accepted;
  final String? reason; // duplicate | same_timestamp | impossible_speed | low_accuracy | before_trip_start
  final bool staleRepeat;
  final bool reanchored;
  final Map<String, Object?> why;
  const GateVerdict(this.accepted, {this.reason, this.staleRepeat = false, this.reanchored = false, this.why = const {}});
  @override
  String toString() => accepted ? 'ok' : reason!;
}

class _Fix {
  final double lat, lng;
  final DateTime t;
  final double? accuracy;
  DateTime seen;
  _Fix(this.lat, this.lng, this.t, this.accuracy) : seen = t;
}

double _acc(double? a) => (a == null || a.isNaN || a < 0) ? defaultAccuracyM : math.min(a, maxAllowanceM);
double _secs(DateTime a, DateTime b) => a.difference(b).inMicroseconds / 1e6;

/// Stateful gate: feed fixes in time order with [check]. Keeps the last
/// accepted fix (anchor) like the server keeps the stored predecessor.
class GpsGate {
  _Fix? _prev; // last ACCEPTED fix
  _Fix? _lastSeen; // last fix processed (accepted or rejected)
  int _streak = 0;
  final DateTime? tripStartedAt;
  final double ceilingKmh;

  GpsGate({this.tripStartedAt, this.ceilingKmh = spikeCeilingKmh});

  GateVerdict check({required double lat, required double lng, required DateTime recordedAt, double? accuracy}) {
    final p = _Fix(lat, lng, recordedAt.toUtc(), accuracy);
    final ls = _lastSeen;
    if (ls != null && _secs(p.t, ls.t) <= 0 && haversineM(ls.lat, ls.lng, p.lat, p.lng) <= _samePointM) {
      return const GateVerdict(false, reason: 'duplicate');
    }
    _lastSeen = p;
    final start = tripStartedAt;
    if (start != null && _secs(start.toUtc(), p.t) > beforeStartToleranceS) {
      return GateVerdict(false, reason: 'before_trip_start', why: {'age_before_start_s': _secs(start.toUtc(), p.t)});
    }
    if (accuracy != null && !accuracy.isNaN && accuracy > lowAccuracyM) {
      return GateVerdict(false, reason: 'low_accuracy', why: {'accuracy_m': accuracy, 'limit_m': lowAccuracyM});
    }
    final prev = _prev;
    if (prev == null) {
      _prev = p;
      return const GateVerdict(true);
    }
    final dt = _secs(p.t, prev.t);
    final dist = haversineM(prev.lat, prev.lng, p.lat, p.lng);
    if (dt <= 0) {
      return GateVerdict(false, reason: dist <= _samePointM ? 'duplicate' : 'same_timestamp', why: {'dist_m': dist});
    }
    if ((prev.lat - p.lat).abs() <= _repeatEpsDeg && (prev.lng - p.lng).abs() <= _repeatEpsDeg) {
      p.seen = prev.seen;
      _streak = 0;
      _prev = p;
      return const GateVerdict(true, staleRepeat: true);
    }
    final refDt = _secs(p.t, prev.seen);
    final allowance = _acc(prev.accuracy) + _acc(p.accuracy);
    final eff = math.max(0.0, dist - allowance);
    final implied = eff / math.max(refDt, minDtS) * 3.6;
    if (implied > ceilingKmh) {
      _streak++;
      if (_streak >= reanchorAfter) {
        _prev = p;
        _streak = 0;
        return const GateVerdict(true, reanchored: true);
      }
      return GateVerdict(false, reason: 'impossible_speed', why: {
        'implied_kmh': (implied * 10).round() / 10, 'dist_m': dist, 'allowance_m': allowance, 'elapsed_s': refDt,
        'ceiling_kmh': ceilingKmh,
      });
    }
    _streak = 0;
    _prev = p;
    return const GateVerdict(true);
  }
}
