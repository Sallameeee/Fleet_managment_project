-- Creates the assignments table: links a driver, a route, and a vehicle for a
-- trip on a given date. Run once in the Supabase SQL editor
-- (Dashboard -> SQL Editor -> New query). Safe to re-run (IF NOT EXISTS).

create table if not exists assignments (
    id          uuid primary key default gen_random_uuid(),
    org_id      uuid not null references organizations (id) on delete cascade,
    driver_id   uuid not null references profiles (id)      on delete cascade,
    route_id    uuid not null references routes (id)         on delete cascade,
    vehicle_id  uuid not null references vehicles (id)       on delete cascade,
    trip_date   date not null,
    shift_label text,
    start_time  time,
    created_at  timestamptz not null default now()
);

-- Fast lookups for "the caller's org's schedule", optionally filtered by date.
create index if not exists assignments_org_date_idx
    on assignments (org_id, trip_date);
