-- Org center/hub locations (e.g. the university + branches).
-- Run once in the Supabase SQL editor. Safe to re-run.
--
-- Model choice: a dedicated `org_centers` table (not a single lat/lng column on
-- organizations) so an org can have MULTIPLE centers (branches) with exactly one
-- marked primary. "One primary" is enforced in application code (marking one
-- primary unsets the others) — simpler and more flexible than a column pair,
-- and lets distance tools pick the primary while still listing branches.

create table if not exists org_centers (
    id         uuid primary key default gen_random_uuid(),
    org_id     uuid not null references organizations(id) on delete cascade,
    name       text not null,
    lat        double precision not null,
    lng        double precision not null,
    is_primary boolean not null default false,
    created_at timestamptz not null default now()
);
create index if not exists idx_org_centers_org on org_centers(org_id);
