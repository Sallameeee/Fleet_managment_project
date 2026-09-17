-- 044: at most ONE active trip per driver, and per assignment.
--
-- POST /trips/start guards "one active trip per driver" with a SELECT then an
-- INSERT. Two starts racing (double tap, two phones logged in as the same
-- driver, a retried request) both pass the SELECT and BOTH insert — QA on the
-- live backend produced 4 active trips for one driver from 4 concurrent starts.
-- Every later view (live, history, reports, the app's End Trip) then has to
-- pick one of them. The rule belongs in the database. SHARED: school and
-- university alike.
--
-- Step 1 resolves any duplicates that already exist (keeps the NEWEST active
-- trip per driver, cancels the rest — identical to a manager cancel).
-- Step 2 adds the partial unique indexes; routers/trips.py start_trip catches
-- the violation and answers the same 409 the SELECT guard gives.
-- Run in the Supabase SQL editor after 043. Idempotent.

with ranked as (
    select id,
           row_number() over (partition by driver_id order by started_at desc nulls last, id desc) as rn
    from trips
    where status = 'active'
)
update trips t
set status = 'cancelled', ended_at = coalesce(t.ended_at, now())
from ranked r
where t.id = r.id and r.rn > 1;

with ranked as (
    select id,
           row_number() over (partition by assignment_id order by started_at desc nulls last, id desc) as rn
    from trips
    where status = 'active' and assignment_id is not null
)
update trips t
set status = 'cancelled', ended_at = coalesce(t.ended_at, now())
from ranked r
where t.id = r.id and r.rn > 1;

create unique index if not exists uq_trips_one_active_per_driver
    on trips (driver_id)
    where status = 'active';

create unique index if not exists uq_trips_one_active_per_assignment
    on trips (assignment_id)
    where status = 'active' and assignment_id is not null;
