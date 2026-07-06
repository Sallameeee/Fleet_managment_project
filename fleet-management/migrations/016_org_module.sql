-- Add a `module` to organizations: which FEATURE SET an org sees.
--   'university' (default) → every existing org keeps working EXACTLY as today.
--   'school'              → the new School module (features built in later steps).
-- Data stays org-scoped/isolated as before; `module` only decides which features
-- an org sees, not where its data lives.
--
-- Run once in the Supabase SQL editor. Safe to re-run (idempotent).

alter table organizations
    add column if not exists module text not null default 'university';

-- Constrain to known modules. Drop+recreate so re-running stays clean.
alter table organizations drop constraint if exists organizations_module_check;
alter table organizations
    add constraint organizations_module_check check (module in ('university', 'school'));
