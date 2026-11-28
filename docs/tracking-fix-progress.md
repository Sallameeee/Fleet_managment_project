# Real-world tracking fix — progress log

Branch: `fix/real-world-tracking` (from `main` @ 38dd99c, 2026-11-28).
Rules: University/School separation; real trips read-only; qa_ accounts only; UTC stored,
`ZoneInfo("Africa/Cairo")` for display; no deploy / merge / prod migration without the owner's OK.

## Status

| Part | State |
|---|---|
| 0 Preflight | done (findings below) |
| A Forensics | done (server-side evidence); app-side cause BLOCKED on the driver-app source |
| B Trip decision log | in progress |
| C Fix tracking | server/dashboard: pending · app: BLOCKED (no source on this machine) |
| D Stop progression | pending |
| E Route search key | pending |
| F Tests | pending |
| G Report | pending |

## Part 0 — preflight (2026-11-28)

- **Driver app source is NOT on this machine.** Package id `com.fleet.fleet_driver_app`
  (from `qa/nox_*.sh`). The QA scripts reference `ROOT=/d/Fleet_managment_project` —
  another machine. Neither GitHub account visible here (`ibrahim212-wq`, `Sallameeee`)
  has a driver-app repo. `D:\cairoWay` / `G:\cairoWay` / `G:\grad_all\maps-mobile-application`
  are a *different* app (RouteMind maps, `name: routemind`).
- Tools: Flutter 3.41.7 / Dart 3.11.5 OK; adb 1.0.41 at
  `%LOCALAPPDATA%\Android\Sdk\platform-tools` (not on PATH); Android emulator with AVD
  `Pixel_4` OK; **NoxPlayer not installed**; **Railway CLI not installed**.
- Supabase read access via `fleet-management/.env` (service key) OK — used with GET only.
- `G:\` root is ACL'd RX for users (only Administrators can create folders), so
  `G:\RouteMind_DEMO` could not be created. Forensics went to
  `C:\Users\ibrah\RouteMind_DEMO\trip_forensics\` instead.

## Part A — forensic findings

Trips identified (route `test22`, org **Engy** (university), driver 6de843a3):
stop 1 at 29.978,30.928 (6th of October) → stop 5 at 30.952,31.152 (El Mahalla).

| trip | Cairo time | pings | heartbeats | alerts | stop visits |
|---|---|---|---|---|---|
| 32dfac81 | 24 Nov 18:10–18:47 | **0** | 38 (all `online`) | started, connection_lost@start, ended(normal) | 0 |
| e491bc37 | 24 Nov 18:48–19:46 | **0** | 59 (all `online`) | started, connection_lost@start, ended(uncertain) | 0 |

There is no trip after 26 Nov 02:37 Cairo (QA only), so the "most recent real trip
6 Oct → El Mahalla" and the "24 Nov trip" are these two trips (the driver ended and
restarted once at 18:47/18:48).

- Heartbeat clock offset (created_at − sent_at): median 0.7 s → phone clock and UTC
  conversion are correct for heartbeats.
- `conn_lost_at == started_at` on both trips: the sweeper logged "connection lost — app
  still alive, GPS not reporting" 90 s after start, because no fix ever arrived.
- Same driver account, real phone, **September** (older APK, no heartbeats): 277 / 686 /
  142 / 73 / 5 pings per trip → GPS used to work on this phone.
- Same day (24 Nov 10:52–15:40 UTC) a Nox QA trip (qa_ LongRun) stored 2,261 pings via
  the same backend → server ingest worked that day.
- `post_pings` stores EVERY validated fix (outliers incl. `future_timestamp` are stored
  tagged; only exact duplicates are dropped; the first fix of a trip is always
  accepted). **Zero rows ⇒ no `/pings` request ever succeeded.** Either the app never
  sent one, or every request failed validation/auth before insert.
- Live backend commit `0609b8a` == `main` for `fleet-management/`. Railway deploys from
  GitHub source (`RAILWAY_GIT_COMMIT_SHA`); the Actions deploy job last ran 18 Sep.
  Backend was not stale.
- Dashboard OFFLINE: `live_logic.driver_live_positions` sets `online = on_trip and
  newest accepted ping < 2 min old`. Heartbeats are ignored, so a trip with heartbeats
  but no fixes is always OFFLINE.

September trips of the same driver account, replayed read-only:
- `5d2229f2` / `67e9bb31` / `787efaa5` start at 33.9858,-118.2541 (Los Angeles = the
  Android EMULATOR default location) → emulator trips, not real-phone evidence.
- REAL phone: `ea835a73` (El Mahalla → Cairo corridor, 686 fixes) and `fd8cca3f`
  (6th of October, 142 fixes). Exact coordinate repeats happen only while standing
  (0 repeats at > 3 m/s), so "cached fix while moving" is NOT shown on this phone.
- On `ea835a73` the old gps_filter / Dart SpikeGate rejected 21 real fixes as
  impossible_speed (implied 122–396 km/h) while the device reported ≤ 20 m/s; on
  `fd8cca3f` 3 (device max 59 km/h). Pattern: positions LAG under ~10 s poll-time
  stamps, then a catch-up jump is judged over one interval; plus ±20–40 m noise with
  no accuracy allowance under a 120 km/h ceiling.
  New rule (accuracy allowance, 160 km/h, first-seen time for repeats): 21 → 6 and
  3 → 0; the 6 left are inconsistent with their own timestamps and stay stored, tagged.
- This explains MISSING segments on the phone's live marker / arrival detection
  (SpikeGate) and on maps, but NOT zero pings: the server stores tagged rejects too.

Root cause (server-side view): the 24 Nov APK on a real phone never delivered a single
fix while its heartbeat channel worked. That is an app-side regression between the
September build and the 22–24 Nov build in the GPS → gate → buffer → send path. It
cannot be pinned to a line without the app source (the 22 Nov Android fallback/dup
gate and a non-encodable/poison row at the buffer head are the leading candidates).
The server and dashboard turned it into a silent failure: no record of what the app
did, and "OFFLINE" instead of "online, no GPS".

## Next

1. Part B migration 048 `trip_events` + server logging + app-event ingest endpoint.
2. Online/offline states in live_logic (online / online_no_gps / offline).
3. Shared stop-progression module + replay harness.
