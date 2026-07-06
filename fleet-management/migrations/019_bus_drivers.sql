-- School module: a proper BUS DRIVERS entity — data-only (NO login/auth; a bus
-- driver is not an app user). Org-scoped. Linked to an assignment via
-- assignments.bus_driver_id.
--
-- This SUPERSEDES the interim assignments.bus_driver_name/bus_driver_phone text
-- columns from migration 018 (which were brand-new and unpopulated). We drop them
-- and reference a real bus_drivers row instead.
--
-- University orgs simply never create bus_drivers or set bus_driver_id, so they
-- are completely unaffected (all additive + nullable).
--
-- Run once in the Supabase SQL editor. Safe to re-run (idempotent).

create table if not exists bus_drivers (
    id                 uuid primary key default gen_random_uuid(),
    org_id             uuid not null references organizations(id) on delete cascade,
    name               text not null,
    phone              text,
    license_number     text,
    license_start_date date,
    license_end_date   date,
    created_at         timestamptz not null default now()
);
create index if not exists idx_bus_drivers_org on bus_drivers(org_id);

-- Link an assignment to its bus driver. ON DELETE SET NULL so deleting a bus
-- driver never destroys assignment/trip history — the link just clears.
alter table assignments
    add column if not exists bus_driver_id uuid references bus_drivers(id) on delete set null;

-- Remove the interim free-text columns from 018 (superseded by bus_driver_id).
alter table assignments
    drop column if exists bus_driver_name,
    drop column if exists bus_driver_phone;
