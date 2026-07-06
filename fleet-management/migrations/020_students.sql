-- School module: STUDENTS extend the existing `passengers` concept with parent
-- contact + class fields. In a school org a passenger IS a student; the parent
-- tracks the bus (the passenger login email = the parent's email).
--
-- All columns are nullable → University passengers are completely unaffected
-- (they never set these). Run once in the Supabase SQL editor. Safe to re-run.

alter table passengers
    add column if not exists parent_phone  text,
    add column if not exists parent_email  text,
    add column if not exists student_phone text,
    add column if not exists grade         text,   -- grade / school year
    add column if not exists class_name    text;   -- classroom / section
