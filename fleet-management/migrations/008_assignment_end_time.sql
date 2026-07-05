-- Shift end time for assignments, so each assignment has a [start_time, end_time]
-- window used for driver/vehicle double-booking conflict detection.
-- Run once in the Supabase SQL editor. Safe to re-run. Nullable, so existing
-- assignments are unaffected (they simply have no window and are skipped by the
-- overlap check until edited).

alter table assignments
    add column if not exists end_time time;
