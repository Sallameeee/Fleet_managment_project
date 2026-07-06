-- Per-trip per-stop VISIT records: the app-confirmed, ordered arrival/departure
-- for reporting planned-vs-actual dwell. Written by the driver app (see
-- POST /trips/{id}/stop-visits). Distinct from `stop_events` (which the ping
-- processor writes automatically for geofence short-stop ALERTS): stop_visits
-- is the authoritative record the driver's arrival timer produces.
--
-- Run once in the Supabase SQL editor. Safe to re-run.

create table if not exists stop_visits (
    id                    uuid primary key default gen_random_uuid(),
    trip_id               uuid not null references trips(id) on delete cascade,
    org_id                uuid not null references organizations(id) on delete cascade,
    stop_id               uuid not null references route_stops(id) on delete cascade,
    stop_order            int,
    arrival_time          timestamptz not null,
    departure_time        timestamptz,
    planned_dwell_seconds int,          -- route_stops.dwell_minutes * 60 at arrival
    actual_dwell_seconds  int,          -- departure_time - arrival_time
    created_at            timestamptz not null default now(),
    -- one visit row per stop per trip → the app upserts (arrival, then departure)
    unique (trip_id, stop_id)
);
create index if not exists idx_stop_visits_trip on stop_visits(trip_id);
