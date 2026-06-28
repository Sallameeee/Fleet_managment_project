-- Platform-staff permissions: turn super_admins into a permissioned staff table.
-- Run once in the Supabase SQL editor. Safe to re-run (IF NOT EXISTS + guarded backfill).
--
-- Decision: ADD COLUMNS to super_admins rather than a new table. super_admins is
-- ALREADY the platform-staff identity table (id = Supabase Auth user id), so a
-- separate table would just duplicate it and force a join on every check.

alter table super_admins
    add column if not exists permissions jsonb not null default '{}'::jsonb;

alter table super_admins
    add column if not exists is_active boolean not null default true;

-- Backfill EXISTING admins (you/Ibrahim) to full control so nothing locks out.
-- view_all is the "owner" flag — it bypasses individual permission checks.
update super_admins
set permissions = '{
    "manage_orgs": true,
    "manage_orgs_status": true,
    "view_finance": true,
    "manage_platform_users": true,
    "view_all": true
}'::jsonb
where permissions = '{}'::jsonb or permissions is null;
