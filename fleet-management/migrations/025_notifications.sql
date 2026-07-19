-- School module: in-app NOTIFICATIONS for parents and managers.
-- Additive only — University is unaffected (no rows are ever created for a
-- non-school org; the generation layer is school-gated).
-- Run once in the Supabase SQL editor. Safe to re-run.
--
-- Recipient model:
--   audience='parent'  → recipient_id = the parent's profile id (only they see it)
--   audience='manager' → recipient_id NULL = ORG-WIDE (any manager of the org sees
--                        it; read state is shared across the org's managers)
-- dedup_key makes event notifications idempotent: e.g. "bus started"/"arrived"
-- fire from repeated pings, but the unique(org_id, dedup_key) index means only the
-- first insert wins and later ones are silently ignored.

create table if not exists notifications (
    id           uuid primary key default gen_random_uuid(),
    org_id       uuid not null references organizations(id) on delete cascade,
    audience     text not null check (audience in ('parent', 'manager')),
    recipient_id uuid references profiles(id) on delete cascade,  -- parent; NULL = org-wide manager
    type         text not null,                 -- change_request_result, trip_started, child_arrived, ...
    title        text not null,
    body         text,
    related_id   text,                           -- change_request / trip / profile_request id
    dedup_key    text,                           -- event idempotency (see below)
    is_read      boolean not null default false,
    created_at   timestamptz not null default now()
);

create index if not exists idx_notifications_parent  on notifications (org_id, recipient_id, is_read, created_at desc);
create index if not exists idx_notifications_manager on notifications (org_id, audience, is_read, created_at desc);
-- Idempotency for generated event notifications (only when a dedup_key is set).
create unique index if not exists uq_notifications_dedup on notifications (org_id, dedup_key) where dedup_key is not null;
