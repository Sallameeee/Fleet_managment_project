-- 048: TRIP DECISION LOG + real-world tracking state.
--
-- trip_events            append-only log of every decision the system makes during
--                        a trip (server: ping accepted/rejected + why, live-state
--                        transitions, next-stop changes, stop arrived / passed /
--                        skipped, lifecycle; app: fix sampled / dropped / queued /
--                        sent / server response / permission + background state /
--                        app version). Read by the admin-only "Trip debug" view.
-- trips.last_fix_at      newest ACCEPTED fix's recorded_at (cheap live-state input)
-- trips.live_state       online | online_no_gps | offline (live_status.py)
-- trips.live_state_at    when live_state last changed
-- trips.progress_state   stop-progression state (stop_progress.py), jsonb
-- trips.progress_version optimistic-concurrency counter for progress_state
-- location_pings.accuracy  horizontal accuracy (m) reported by the phone, if any
-- stop_visits.status     + 'passed' (went by within the pass radius without stopping)
-- stop_visits.closest_m  closest approach to the stop (m), decided_by server|app
--
-- Access: the API uses the service-role key (bypasses RLS) and every endpoint
-- filters by the caller's org. RLS below is defense-in-depth for any direct
-- anon/authenticated access: SELECT only, own org only, staff only (never
-- drivers / passengers / parents). No INSERT/UPDATE/DELETE policy => only the
-- backend (service role) can write.
--
-- Retention: purge_trip_events(days) deletes rows older than `days` (default 30).
-- The backend sweeper calls it every 6 h with TRIP_EVENTS_RETENTION_DAYS; if
-- pg_cron is installed a nightly job is scheduled as well.
--
-- Idempotent. Run after 047.

create table if not exists trip_events (
    id          bigserial primary key,
    trip_id     uuid not null references trips(id) on delete cascade,
    org_id      uuid not null references organizations(id) on delete cascade,
    module      text not null check (module in ('university', 'school')),
    source      text not null check (source in ('app', 'server')),
    type        text not null check (char_length(type) between 1 and 48),
    occurred_at timestamptz not null,
    message     text not null default '' check (char_length(message) <= 500),
    payload     jsonb not null default '{}'::jsonb,
    -- App events carry their own id so a re-sent batch is stored once.
    client_event_id text check (client_event_id is null or char_length(client_event_id) <= 64),
    created_at  timestamptz not null default now()
);

create index if not exists idx_trip_events_trip_time on trip_events (trip_id, occurred_at);
create index if not exists idx_trip_events_org_created on trip_events (org_id, created_at);
create index if not exists idx_trip_events_created on trip_events (created_at);
create unique index if not exists uq_trip_events_client
    on trip_events (trip_id, client_event_id) where client_event_id is not null;

alter table trip_events enable row level security;

do $$ begin
    create policy trip_events_staff_read on trip_events for select
        using (
            org_id = (select p.org_id from profiles p where p.id = auth.uid())
            and (select p.role from profiles p where p.id = auth.uid())
                not in ('driver', 'passenger', 'parent', 'supervisor', 'student')
        );
exception when duplicate_object then null; end $$;

create or replace function purge_trip_events(days integer default 30)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    keep_days integer := coalesce(nullif(greatest(days, 0), 0), 30);
    n integer;
begin
    delete from trip_events where created_at < now() - keep_days * interval '1 day';
    get diagnostics n = row_count;
    return n;
end $$;

-- Only the backend (service role) may purge; Supabase grants new functions to
-- anon/authenticated by default, so revoke those explicitly.
revoke all on function purge_trip_events(integer) from public, anon, authenticated;
grant execute on function purge_trip_events(integer) to service_role;

do $$ begin
    if exists (select 1 from pg_extension where extname = 'pg_cron') then
        perform cron.schedule('purge-trip-events', '17 3 * * *', 'select purge_trip_events(30)');
    end if;
exception when others then null; end $$;

alter table trips add column if not exists last_fix_at      timestamptz;
alter table trips add column if not exists live_state       text;
alter table trips add column if not exists live_state_at    timestamptz;
alter table trips add column if not exists progress_state   jsonb;
alter table trips add column if not exists progress_version integer not null default 0;

do $$ begin
    alter table trips add constraint trips_live_state_check
        check (live_state is null or live_state in ('online', 'online_no_gps', 'offline'));
exception when duplicate_object then null; end $$;

alter table location_pings add column if not exists accuracy real;

alter table stop_visits add column if not exists closest_m  real;
alter table stop_visits add column if not exists decided_by text;
alter table stop_visits drop constraint if exists stop_visits_status_check;
alter table stop_visits
    add constraint stop_visits_status_check check (status in ('visited', 'passed', 'skipped'));

comment on table trip_events is
  'Trip decision log (server + app). Admin-only Trip debug view. Retention: purge_trip_events(days).';
