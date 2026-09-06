-- Skipped-stop support (SHARED trip logic — BOTH modules).
--
-- When a driver/supervisor misses a stop and reaches a LATER one, the skipped
-- stop is now recorded so it shows in history / trip logs as MISSED rather than
-- silently vanishing. This is core trip behaviour and applies to university
-- drivers and school supervisors IDENTICALLY — nothing module-specific here.
--
-- `status`:
--   'visited' — the bus actually arrived (the existing behaviour; DEFAULT so
--               every existing row and every current client keeps working).
--   'skipped' — the bus passed this stop without stopping (arrival_time carries
--               the moment it was passed; departure/actual dwell stay NULL).
--
-- Additive + idempotent. No existing column changes, so current readers and the
-- current app are unaffected. Run once in the Supabase SQL editor. Safe to re-run.

alter table stop_visits
    add column if not exists status text not null default 'visited';

alter table stop_visits drop constraint if exists stop_visits_status_check;
alter table stop_visits
    add constraint stop_visits_status_check check (status in ('visited', 'skipped'));
