-- SUPERVISORS carry a NATIONAL ID, not a driving licence.
--
-- In a SCHOOL org the driver-role app user is a SUPERVISOR (they ride the bus and
-- take attendance) — they do not drive, so a licence number is meaningless for
-- them. The actual driver is a row in `bus_drivers`, which KEEPS its
-- license_number / license_start_date / license_end_date untouched.
--
-- University orgs are unaffected: their driver-role users really do drive, so the
-- dashboard keeps showing the licence fields for them. This column is simply
-- additive and stays NULL where unused.
--
-- Run once in the Supabase SQL editor. Safe to re-run.

alter table profiles add column if not exists national_id text;

comment on column profiles.national_id is
    'National ID for SCHOOL supervisors (driver-role users). University drivers use the license_* columns instead.';
