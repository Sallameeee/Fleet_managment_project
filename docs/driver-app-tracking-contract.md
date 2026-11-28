# Driver app ↔ backend tracking contract (real-world tracking fix)

The driver app (`com.fleet.fleet_driver_app`, Flutter) is **not in this repository**.
This document is the exact contract the backend on `fix/real-world-tracking`
expects, and the changes the app needs. Everything here is additive: an old APK
keeps working against the new backend (it just sends no app events).

## Why (evidence, 24 Nov real trips `32dfac81`, `e491bc37`)

- 0 fixes stored, 38 + 59 heartbeats stored (all `online`), on a real phone with
  good internet. The backend stores every validated fix (rejects are stored,
  tagged), so **no `/pings` request ever succeeded** — the app never sent one, or
  every one failed before validation.
- The same account on a real phone in September (older APK, no heartbeats) sent
  686 / 142 fixes per trip. The regression is in the newer APK's
  GPS → gate → buffer → send path.
- The old Dart `_SpikeGate` (120 km/h, no accuracy allowance) rejected 21 real
  fixes on one real Mahalla → Cairo trip (device ≤ 72 km/h).

## 1. GPS → buffer → upload (MUST)

1. **Every raw fix goes into the offline buffer.** No gate (spike, duplicate,
   accuracy, "same as last") may decide what is buffered or uploaded. The server
   applies the rule and stores rejects tagged with the reason. Gates only drive
   the live marker and the on-phone stop progression.
2. `recorded_at` = the fix's own time (`Position.timestamp`), **UTC, ISO-8601
   with `Z`** (`position.timestamp.toUtc().toIso8601String()`). Never the poll
   time, never local time without an offset. (The server corrects naive Cairo
   time when unambiguous and logs it — don't rely on it.)
3. Send `accuracy` (metres, `Position.accuracy`) with every fix — it feeds the
   accuracy allowance and the `low_accuracy` tag.
4. Use a **position stream** (`getPositionStream`, distanceFilter 0–10 m,
   interval ~5 s) from a **foreground service with type `location`**
   (Android 14 requires `foregroundServiceType="location"` and the
   `FOREGROUND_SERVICE_LOCATION` permission). A `getLastKnownPosition` fallback
   may feed the UI, but must not be the only source, and a cached fix with an
   old timestamp is still sent (the server tags it `before_trip_start` / dedupes it).
5. Flush the buffer **oldest first**, ≤ 200 fixes per request, and delete rows
   only after a 2xx. On 422, log the response body as an app event (below) and
   move the rejected rows to a dead-letter table rather than retrying forever.
6. Screen locked / background / swiped away: the foreground service keeps
   sampling and buffering; on process death the buffer survives (SQLite) and is
   flushed on the next start. Ask for "Allow all the time" location and a
   battery-optimisation exemption; log the result (below).

## 2. Endpoints (backend `fix/real-world-tracking`)

| Endpoint | Body | Notes |
|---|---|---|
| `POST /trips/{id}/pings` | one fix or a list: `lat, lng, speed?, heading?, recorded_at, accuracy?, battery?, net_state?` | Response adds `progress` (next stop / ETA) and `rejected[]` with reasons. |
| `POST /trips/{id}/heartbeat` | `sent_at, battery?, net_state?` (or list) | unchanged; ~every 60 s, separate timer. |
| `POST /trips/{id}/app-state` | `state: detached\|paused\|resumed, at?, battery?, net_state?` | unchanged; now logged. |
| `POST /trips/{id}/events` | `{app_version, build, platform, events: [{id, type, at, message?, data?}]}` ≤ 200 events | **new** — the app's decision log. `id` = client UUID (idempotent). `type` matches `^[a-z][a-z0-9_.]{0,47}$`. |
| `GET /trips/{id}/progress` | — | **new** — shared next stop, ETA, progress + live state. |

Suggested app event types (buffer them like pings, size-cap the buffer, send
with each ping flush):

`app.start` (version/build/os), `perm.location` (whileInUse/always/denied),
`perm.battery_opt` (exempt?), `svc.started` / `svc.stopped` (foreground service),
`gps.sampled` (lat,lng,accuracy,provider,fix_time — at most 1 per 30 s, plus
every rejected one), `gate.spike_drop` / `gate.duplicate` / `gate.low_accuracy`
(why + values), `queue.enqueued` (count, buffer size), `queue.sent`
(count, http status, recorded, duplicates_skipped, ms), `queue.error`
(status, body excerpt), `net.change` (online/offline), `lifecycle.paused` /
`lifecycle.resumed` / `lifecycle.detached`, `stop.*` (decisions from the shared
progression on the phone).

## 3. Shared rules (use, don't re-implement)

`shared/dart` (package `routemind_tracking_core`, no dependencies):

```yaml
# driver app pubspec.yaml
dependencies:
  routemind_tracking_core:
    path: ../Fleet_managment_project/shared/dart   # or copy the two files
```

- `GpsGate` — replaces `_SpikeGate` (live marker only).
- `newProgressState / stepProgress / finalizeProgress / progressSummary` — next
  stop, ETA, progress bar, Stops timeline; persist the state Map with the trip.
  The server runs the identical rule and writes stop visits itself, so the app
  no longer needs to POST `/stop-visits` (still accepted; never downgraded).
- Parity: `dart run test/vectors_test.dart` (CI: `shared-tracking-rules.yml`).

## 4. Release

Bump `version:` in the app's `pubspec.yaml` (e.g. `x.y.z+N` → `x.y.(z+1)+(N+1)`),
send `app_version`/`build` in the events batch, build
`flutter build apk --release`, copy to `G:\RouteMind_DEMO\` (needs an
Administrator-created folder — `G:\` root is read-only for normal users).
