-- Passenger-tracking redesign (pre-trips).
-- Run once in the Supabase SQL editor (Dashboard -> SQL Editor -> New query).
-- Safe to re-run: every step is guarded (IF NOT EXISTS / existence checks).
--
-- Summary of changes:
--   1. vehicles.share_token  -> permanent, per-vehicle public tracking token.
--   2. organizations.tracking_start_time / tracking_end_time -> org-wide
--      "live tracking" window (nullable = always-on).
--   3. trips.share_token     -> removed; tracking is per-vehicle now, not per-trip.

-- gen_random_bytes() comes from pgcrypto. On Supabase it normally lives in the
-- `extensions` schema (already on the search_path). This is a no-op if present.
create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------------
-- 1. vehicles.share_token : permanent per-vehicle tracking token
-- ---------------------------------------------------------------------------
-- Add the column with a volatile default so EVERY row (existing + future) gets
-- its own random 32-hex-char token. Postgres evaluates a volatile default
-- per-row during the rewrite, so existing vehicles each get a distinct value.
alter table vehicles
    add column if not exists share_token text default encode(gen_random_bytes(16), 'hex');

-- Defensive backfill: cover any row that somehow still has a null token
-- (e.g. if the column was added previously without the default).
update vehicles
set share_token = encode(gen_random_bytes(16), 'hex')
where share_token is null;

-- Enforce going forward: never null, globally unique.
alter table vehicles
    alter column share_token set not null;

create unique index if not exists vehicles_share_token_key
    on vehicles (share_token);

-- ---------------------------------------------------------------------------
-- 2. organizations : org-wide live-tracking window
-- ---------------------------------------------------------------------------
-- Nullable on purpose. NULL = "always on" (no window configured). The admin
-- sets these later to e.g. 07:00 / 18:00. Left without a default so the
-- "always-on until configured" behaviour is explicit, not a silent 07-18.
alter table organizations
    add column if not exists tracking_start_time time;

alter table organizations
    add column if not exists tracking_end_time time;

-- ---------------------------------------------------------------------------
-- 3. trips.share_token : removed (tracking is per-vehicle now)
-- ---------------------------------------------------------------------------
-- Guarded drop: only runs if the column actually exists. Confirmed safe in
-- this database (trips has 0 rows and no endpoint reads this column yet).
-- NOTE: dropping a column is irreversible. If you'd rather keep it unused,
-- comment out this block instead of running it.
do $$
begin
    if exists (
        select 1
        from information_schema.columns
        where table_name = 'trips'
          and column_name = 'share_token'
    ) then
        alter table trips drop column share_token;
    end if;
end $$;
