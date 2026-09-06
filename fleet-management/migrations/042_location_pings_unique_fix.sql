-- 042: one row per (trip, GPS timestamp).
--
-- Real data (Sept 2026) showed EVERY fix stored twice: the tracking isolate
-- polled the GPS every 5 s while the receiver produced a new fix only every
-- ~10 s, so the same fix (same timestamp, same coordinates) was buffered and
-- uploaded again. The app now skips repeats and the backend drops duplicates
-- before insert; this index makes it airtight against concurrent flushes.
--
-- Step 1 removes the duplicates that already exist (keeps the earliest row).
-- Step 2 adds the unique index. Run after 041. Idempotent.

delete from location_pings a
using location_pings b
where a.trip_id = b.trip_id
  and a.recorded_at = b.recorded_at
  and a.id > b.id;

create unique index if not exists uq_location_pings_trip_recorded_at
  on location_pings (trip_id, recorded_at);
