-- 041: tag physically-impossible GPS fixes instead of losing them.
--
-- Ingest (routers/trips.py post_pings) runs the shared plausibility rule
-- (gps_filter.py): a fix whose implied speed from the previous ACCEPTED fix is
-- above 120 km/h is stored RAW but tagged is_outlier=true with the reason and
-- the impossible speed, so history/live/reports skip it while nothing real is
-- deleted. Readers re-apply the same rule anyway, so this works before/after.
--
-- Run in the Supabase SQL editor (after 040). Idempotent.

alter table location_pings add column if not exists is_outlier    boolean not null default false;
alter table location_pings add column if not exists reject_reason text;
alter table location_pings add column if not exists implied_kmh   numeric;

comment on column location_pings.is_outlier is
  'true = physically impossible fix (implied speed > 120 km/h from the previous accepted fix). Kept for audit, skipped by every map/report.';

-- Readers fetch a trip''s pings ordered by time; keep that fast and cheap to
-- filter on the tag.
create index if not exists idx_location_pings_trip_time_clean
  on location_pings (trip_id, recorded_at)
  where is_outlier = false;
