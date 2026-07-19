-- Parent-reported issues/complaints (School module). A parent submits a subject +
-- message (optionally about a specific child); the manager sees them and resolves.
-- Named parent_reports to avoid any clash with the existing reporting/export
-- feature. Additive; University unaffected. Run once in Supabase. Safe to re-run.

create table if not exists parent_reports (
    id          uuid primary key default gen_random_uuid(),
    org_id      uuid not null references organizations(id) on delete cascade,
    parent_id   uuid not null references profiles(id) on delete cascade,
    student_id  uuid references passengers(id) on delete set null,  -- optional: about one child
    subject     text not null,
    message     text not null,
    status      text not null default 'open' check (status in ('open', 'resolved')),
    created_at  timestamptz not null default now(),
    resolved_at timestamptz,
    resolved_by uuid references profiles(id) on delete set null
);
create index if not exists idx_parent_reports_org_status on parent_reports (org_id, status, created_at desc);
create index if not exists idx_parent_reports_parent on parent_reports (parent_id);
