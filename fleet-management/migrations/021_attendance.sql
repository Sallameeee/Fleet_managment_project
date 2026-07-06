-- School module: per-trip STUDENT ATTENDANCE, taken by the supervisor in the app.
-- One row per (trip, student); boarded yes/no + timestamp. trip_date is stored
-- directly so attendance reports work by date range even after a trip is removed.
--
-- School-only in practice (University orgs never create students/attendance).
-- Run once in the Supabase SQL editor. Safe to re-run.

create table if not exists attendance (
    id          uuid primary key default gen_random_uuid(),
    org_id      uuid not null references organizations(id) on delete cascade,
    trip_id     uuid references trips(id) on delete set null,
    student_id  uuid not null references passengers(id) on delete cascade,
    trip_date   date not null,
    boarded     boolean not null default false,
    recorded_at timestamptz not null default now(),
    unique (trip_id, student_id)
);
create index if not exists idx_attendance_org on attendance(org_id);
create index if not exists idx_attendance_student on attendance(student_id);
create index if not exists idx_attendance_trip on attendance(trip_id);
create index if not exists idx_attendance_date on attendance(trip_date);
