-- School module: PARENT-requested edits to their own personal info (name / phone
-- / email), approved by a manager — a parent's edit never applies directly; it
-- becomes a request the manager approves or rejects (mirrors change_requests).
-- Additive only; University is unaffected. Run once in Supabase. Safe to re-run.

create table if not exists profile_change_requests (
    id             uuid primary key default gen_random_uuid(),
    org_id         uuid not null references organizations(id) on delete cascade,
    parent_id      uuid not null references profiles(id) on delete cascade,  -- the parent (login)
    proposed_name  text,
    proposed_phone text,
    proposed_email text,   -- on approve: updates the Auth login email + profile + children's parent_email
    status         text not null default 'pending' check (status in ('pending', 'approved', 'rejected')),
    created_at     timestamptz not null default now(),
    decided_at     timestamptz,
    decided_by     uuid references profiles(id) on delete set null
);

-- One pending profile-edit request per parent at a time.
create unique index if not exists uq_profile_req_active on profile_change_requests (parent_id) where status = 'pending';
create index if not exists idx_profile_req_org_status on profile_change_requests (org_id, status);
