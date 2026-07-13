-- Parent → MULTIPLE students (school module). Fixes bulk student upload failing
-- on sibling / duplicate parent emails.
--
-- Model: a STUDENT is a `passengers` row; the PARENT is a `profiles` row
-- (role='passenger') that owns the ONE login (parent_email). Siblings share the
-- same parent account via passengers.parent_id → profiles.id. A student no longer
-- needs its own login, so passengers.id is decoupled from profiles.
--
-- University is unaffected: their passenger keeps its own login, and its student
-- row simply points parent_id at its OWN profile (self-parent).
--
-- Run once in the Supabase SQL editor. Safe to re-run (idempotent).

-- Student name now lives on the student row (school students have no profile).
alter table passengers add column if not exists name text;

-- Link each student to its parent account.
alter table passengers add column if not exists parent_id uuid references profiles(id) on delete cascade;

-- Backfill existing rows: each old passenger WAS its own login (passengers.id =
-- profiles.id), so that profile becomes the parent, and its name the student name.
update passengers p set name = pr.name from profiles pr where pr.id = p.id and p.name is null;
update passengers set parent_id = id where parent_id is null;

-- New students get an independent id and no longer need to BE a profile, so drop
-- the id -> profiles foreign key. (attendance.student_id still references
-- passengers(id) — unchanged; the primary key stays.)
alter table passengers alter column id set default gen_random_uuid();
alter table passengers drop constraint if exists passengers_id_fkey;

create index if not exists idx_passengers_parent on passengers(parent_id);
