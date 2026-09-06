-- 038: passengers.route_id is OPTIONAL.
--
-- A student / university passenger may be created before their route exists.
-- The column was declared nullable in 013_passengers.sql already, so on most
-- databases this is a no-op safety net; it is idempotent either way. The code
-- (routers/passengers.py, dashboard, app) is written to work with NULL route_id
-- whether or not this file has been run.
--
-- Run in the Supabase SQL editor (after 037).

alter table passengers alter column route_id drop not null;

comment on column passengers.route_id is
  'Optional. NULL = route not defined yet ("Not defined" in the dashboard, "No route assigned yet" in the app). Assign later via PATCH /passengers/{id}.';

-- Speeds up "who has no route yet" filters in the manager lists.
create index if not exists idx_passengers_org_null_route
  on passengers (org_id)
  where route_id is null;
