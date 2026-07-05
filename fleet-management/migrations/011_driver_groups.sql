-- Persistent, nestable driver groups for the Full View.
-- Run once in the Supabase SQL editor. Safe to re-run.
--
-- Model choice:
--   * driver_groups  — the tree. parent_group_id self-references driver_groups,
--     which is what enables NESTING (a group inside a group). A NULL parent =
--     a top-level group. Deleting a group is handled in code (children + members
--     move up one level), so parent_group_id uses ON DELETE SET NULL only as a
--     safety net (a stray delete leaves orphans as roots, never cascades away).
--   * driver_group_members — a driver's membership as its OWN table (not a
--     column on profiles): keeps the tracking feature self-contained/reversible,
--     and a PRIMARY KEY on driver_id enforces "a driver is in at most one group"
--     so moving a driver is a single upsert.

create table if not exists driver_groups (
    id              uuid primary key default gen_random_uuid(),
    org_id          uuid not null references organizations(id) on delete cascade,
    name            text not null,
    parent_group_id uuid references driver_groups(id) on delete set null,
    created_at      timestamptz not null default now()
);
create index if not exists idx_driver_groups_org on driver_groups(org_id);
create index if not exists idx_driver_groups_parent on driver_groups(parent_group_id);

create table if not exists driver_group_members (
    driver_id  uuid primary key references profiles(id) on delete cascade,
    group_id   uuid not null references driver_groups(id) on delete cascade,
    org_id     uuid not null references organizations(id) on delete cascade,
    created_at timestamptz not null default now()
);
create index if not exists idx_dgm_group on driver_group_members(group_id);
