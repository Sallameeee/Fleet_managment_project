-- 047: organization logo (branding).
--
-- The logo file itself lives in the public Storage bucket `org-logos`
-- (created by the API on the first upload): org-logos/<org_id>/logo-<ms>.<ext>.
-- This column stores its public URL so logins and /auth/me read it with the org.
--
-- Safe before/after: the API works WITHOUT this column (it then finds the newest
-- logo by listing the org's Storage folder). After this migration, an org whose
-- logo was uploaded before it is healed automatically: the first lookup that
-- finds logo_url NULL but a logo in Storage writes the URL here. No backfill
-- statement is needed.

alter table public.organizations
  add column if not exists logo_url text;

comment on column public.organizations.logo_url is
  'Public URL of the org logo in Storage bucket org-logos (PNG/JPG/SVG, max 1 MB). NULL = no logo (apps show the org initials).';
