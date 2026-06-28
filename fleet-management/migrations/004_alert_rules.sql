-- Manager-defined, org-scoped rules that drive configurable alerting.
-- Run once in the Supabase SQL editor. Safe to re-run (guarded throughout).

-- Enum types (guarded so the migration is idempotent).
do $$ begin
    create type alert_rule_type as enum ('speeding', 'off_route', 'short_stop', 'offline');
exception when duplicate_object then null; end $$;

do $$ begin
    create type alert_target_kind as enum ('all', 'vehicles', 'drivers');
exception when duplicate_object then null; end $$;

create table if not exists alert_rules (
    id            uuid primary key default gen_random_uuid(),
    org_id        uuid not null references organizations (id) on delete cascade,
    name          text not null,
    type          alert_rule_type not null,
    -- Meaning depends on type: speeding=km/h, off_route=meters, offline=minutes.
    -- short_stop ignores this (uses route_stops.dwell_minutes).
    threshold     numeric,
    target_kind   alert_target_kind not null default 'all',
    -- Specific vehicle/driver ids when target_kind != 'all'. NULL/empty = all.
    target_ids    uuid[],
    notify_panel  boolean not null default true,
    notify_email  boolean not null default false,  -- recorded only (Firebase later)
    notify_push   boolean not null default false,  -- recorded only (Firebase later)
    is_active     boolean not null default true,
    created_at    timestamptz not null default now()
);

create index if not exists alert_rules_org_idx on alert_rules (org_id);
create index if not exists alert_rules_org_active_type_idx
    on alert_rules (org_id, is_active, type);

-- RLS: org-scoped, mirroring the multi-tenant pattern. NOTE: the API uses the
-- Supabase service-role key, which BYPASSES RLS — so app-level org filtering
-- (every endpoint filters by the caller's org_id) remains the primary guard.
-- This policy is defense-in-depth for any direct anon/authenticated access.
alter table alert_rules enable row level security;

do $$ begin
    create policy alert_rules_org_isolation on alert_rules
        using (org_id = (select org_id from profiles where id = auth.uid()))
        with check (org_id = (select org_id from profiles where id = auth.uid()));
exception when duplicate_object then null; end $$;
