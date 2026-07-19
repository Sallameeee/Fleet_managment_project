-- Expanded attendance (School module): distinguish the trip SESSION (morning
-- pickup vs afternoon drop-off) and record WHICH STOP a student got off at in the
-- afternoon. Both nullable → existing rows and University are unaffected.
-- Session is derived server-side from the trip's start time (before ~12:00 local =
-- morning, else afternoon). Run once in Supabase. Safe to re-run.

alter table attendance
    add column if not exists session       text,   -- 'morning' | 'afternoon'
    add column if not exists drop_off_stop  text;   -- stop the student got off at (afternoon)
