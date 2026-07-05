-- Scheduled emailed reports. The UI + storage ship now; actual email delivery
-- is deferred to the Firebase phase (rows are saved and listed, not yet sent).
-- Run once in the Supabase SQL editor. Safe to re-run.

create table if not exists report_schedules (
    id           uuid primary key default gen_random_uuid(),
    org_id       uuid not null references organizations(id) on delete cascade,
    name         text,
    frequency    text not null,                 -- daily | weekly | monthly
    subject_kind text not null default 'all',   -- all | vehicle | driver
    subject_id   uuid,                           -- vehicle/driver id when scoped
    types        text not null,                  -- comma list: drivers,trips,kilometers,speed
    period       text not null default 'week',   -- today | week | month
    email        text not null,
    is_active    boolean not null default true,
    created_at   timestamptz not null default now()
);

create index if not exists idx_report_schedules_org on report_schedules(org_id);
