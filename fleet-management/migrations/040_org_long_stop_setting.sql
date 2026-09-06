-- 040: org-wide long-stop threshold (Settings → Tracking).
--
-- organizations.long_stop_minutes: how long a bus may stand still AWAY from any
-- scheduled route stop before a `long_stop` alert is raised. NULL/missing =>
-- the code default (5 min); 0 => detection off for that org. Read by
-- routers/trips.py _long_stop_threshold_min, edited via
-- GET/PATCH /organizations/tracking-hours (manage_settings).
--
-- Run in the Supabase SQL editor after 039. Idempotent.

alter table organizations
    add column if not exists long_stop_minutes integer not null default 5;

do $$ begin
    alter table organizations
        add constraint organizations_long_stop_minutes_check
        check (long_stop_minutes >= 0 and long_stop_minutes <= 180);
exception when duplicate_object then null; end $$;

comment on column organizations.long_stop_minutes is
  'Long-stop alert threshold in minutes (bus stationary away from any route stop). 0 = off.';
