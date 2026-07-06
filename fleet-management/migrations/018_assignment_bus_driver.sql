-- School module: the REAL (physical) bus driver stored as plain data on the
-- assignment. In a school org the app user (assignment.driver_id → profile) is
-- the SUPERVISOR who runs the trip; the actual driver is just name + phone here.
--
-- Named `bus_driver_*` (not `driver_*`) on purpose: the assignment API already
-- exposes `driver_name` = the app user's / supervisor's profile name, so these
-- distinct columns avoid any collision.
--
-- Additive + nullable → University orgs (which never set them) are unaffected.
-- Run once in the Supabase SQL editor. Safe to re-run.

alter table assignments
    add column if not exists bus_driver_name  text,
    add column if not exists bus_driver_phone text;
