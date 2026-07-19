-- Per-trip PERFORMANCE metrics (School module), computed + persisted when a trip
-- ends. Reuses existing signals: speeding/off_route come from the `alerts` table
-- (already detected from pings during the trip); schedule adherence is computed
-- from stop_events.arrived_at vs route_stops.arrival_time.
-- Additive; University trips simply never get a row (compute is school-gated).
-- Run once in Supabase. Safe to re-run.

create table if not exists trip_performance (
    trip_id         uuid primary key references trips(id) on delete cascade,
    org_id          uuid not null references organizations(id) on delete cascade,
    driver_id       uuid references profiles(id) on delete set null,   -- the supervisor
    route_id        uuid references routes(id) on delete set null,
    trip_date       date,
    speeding_count  int not null default 0,   -- # speeding alerts on the trip
    off_route_count int not null default 0,   -- # off_route alerts on the trip
    stops_total     int not null default 0,   -- scheduled stops actually reached
    stops_on_time   int not null default 0,   -- reached within the grace window
    stops_late      int not null default 0,   -- reached later than grace
    avg_delay_min   numeric,                  -- mean arrival delay (min; negative = early)
    max_delay_min   numeric,                  -- worst arrival delay (min)
    computed_at     timestamptz not null default now()
);
create index if not exists idx_trip_perf_org on trip_performance (org_id, trip_date desc);
create index if not exists idx_trip_perf_driver on trip_performance (driver_id);
