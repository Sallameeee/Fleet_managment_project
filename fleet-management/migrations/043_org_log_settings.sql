-- 043: "Edit Logs" — per-organization event logging settings.
--
-- organizations.log_settings (jsonb): {type: {enabled, threshold, duration_s}}
-- for speeding (km/h), off_route (metres + seconds), long_stop (minutes),
-- short_stop (on/off), offline (minutes). See log_settings_logic.py for the
-- catalog and defaults. Missing column / missing type => catalog defaults, so
-- detection keeps working before this runs; the Edit Logs page reports
-- "configurable: false" until then. Supersedes organizations.long_stop_minutes
-- (040) when a long_stop entry is present.
--
-- Run in the Supabase SQL editor after 042. Idempotent.

alter table organizations
    add column if not exists log_settings jsonb not null default '{}'::jsonb;

comment on column organizations.log_settings is
  'Edit Logs: {event_type: {enabled, threshold, duration_s}}. Empty = catalog defaults.';
