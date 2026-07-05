-- Passengers (students) + forced-first-login flag.
-- Run once in the Supabase SQL editor. Safe to re-run.
--
-- Auth model: a passenger is a `profiles` row with role='passenger' (so the
-- existing login/JWT/org-scoping machinery works unchanged), PLUS a `passengers`
-- detail table for the student-specific fields (university_id + their route).
-- `must_change_password` (on profiles) is set true when a passenger account is
-- auto-created with the shared default password, forcing a reset on first login.

alter table profiles add column if not exists must_change_password boolean not null default false;

create table if not exists passengers (
    id            uuid primary key references profiles(id) on delete cascade,
    org_id        uuid not null references organizations(id) on delete cascade,
    university_id text,
    route_id      uuid references routes(id) on delete set null,
    created_at    timestamptz not null default now()
);
create index if not exists idx_passengers_org on passengers(org_id);
create index if not exists idx_passengers_route on passengers(route_id);
