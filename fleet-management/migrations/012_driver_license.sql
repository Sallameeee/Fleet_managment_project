-- Driver licence fields (nullable — existing drivers unaffected).
-- Stored on profiles (drivers are profiles with role='driver'), same as name/phone.
-- Run once in the Supabase SQL editor. Safe to re-run.

alter table profiles add column if not exists license_number text;
alter table profiles add column if not exists license_start_date date;
alter table profiles add column if not exists license_expiry_date date;
