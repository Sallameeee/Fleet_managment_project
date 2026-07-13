-- School module: parent-requested ONE-DAY route/bus changes for a SPECIFIC child,
-- approved by an admin based on bus capacity. School-only feature; University
-- orgs never create these, so they are unaffected.
-- Run once in the Supabase SQL editor. Safe to re-run (idempotent).

-- Per-org submission cutoff. Default 20:00 (8 PM): a request for a given day must
-- be submitted before this time on the DAY BEFORE. Configurable per org.
alter table organizations
    add column if not exists change_cutoff_time time not null default '20:00:00';

create table if not exists change_requests (
    id                 uuid primary key default gen_random_uuid(),
    org_id             uuid not null references organizations(id) on delete cascade,
    student_id         uuid not null references passengers(id) on delete cascade,  -- the SPECIFIC child
    parent_id          uuid not null references profiles(id) on delete cascade,    -- the requester (parent login)
    current_route_id   uuid references routes(id) on delete set null,              -- child's normal route/bus
    requested_route_id uuid not null references routes(id) on delete cascade,      -- the one-day route/bus
    requested_stop     text,                                                       -- where the child gets off
    request_date       date not null,                                             -- the ONE day
    status             text not null default 'pending' check (status in ('pending', 'approved', 'rejected')),
    created_at         timestamptz not null default now(),
    decided_at         timestamptz,
    decided_by         uuid references profiles(id) on delete set null
);

-- A child may have only ONE active (pending/approved) request per day.
create unique index if not exists uq_change_req_active_per_day
    on change_requests (student_id, request_date)
    where status in ('pending', 'approved');
create index if not exists idx_change_requests_org_status on change_requests (org_id, status);
create index if not exists idx_change_requests_date on change_requests (org_id, request_date);
