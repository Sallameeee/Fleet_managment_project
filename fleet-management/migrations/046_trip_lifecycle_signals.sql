-- 046: signals that power the trip-lifecycle log + the END-REASON inference.
--
-- alerts.meta (jsonb)       structured event params: {event, reason, gap_min,
--                           buffered, place, message_en, message_ar, ...} so every
--                           client renders the log point in its own language.
-- trips.*                   the driver phone's LAST known state, updated by every
--                           ping / heartbeat / app-state beacon:
--   last_signal_at          any signal (ping arrival, heartbeat, beacon)
--   last_heartbeat_at       "app alive" beacon (separate from GPS)
--   last_battery            0-100 (%), last_net_state online|weak|offline
--   app_closed_at           the app's graceful "closed" beacon (cleared on resume)
--   conn_lost_at            start of the OPEN connection-loss episode (null = connected)
--   end_reason              normal | cancelled | driver_deactivated | app_closed |
--                           network_lost_resumed | device_power | uncertain
--   end_detail              evidence used for the inference (jsonb)
-- location_pings.battery / net_state   phone state at the moment of the fix.
-- trip_heartbeats           append-only "app alive" beacons (batched, deduped).
--
-- Everything is optional: the code probes each column once and keeps working
-- (without that signal) when it is missing. Run after 045. Idempotent.

alter table alerts add column if not exists meta jsonb;

alter table trips add column if not exists last_signal_at    timestamptz;
alter table trips add column if not exists last_heartbeat_at timestamptz;
alter table trips add column if not exists last_battery      smallint;
alter table trips add column if not exists last_net_state    text;
alter table trips add column if not exists app_closed_at     timestamptz;
alter table trips add column if not exists conn_lost_at      timestamptz;
alter table trips add column if not exists end_reason        text;
alter table trips add column if not exists end_detail        jsonb;

do $$ begin
    alter table trips add constraint trips_end_reason_check check (end_reason is null or end_reason in
        ('normal', 'cancelled', 'driver_deactivated', 'app_closed', 'network_lost_resumed', 'device_power', 'uncertain'));
exception when duplicate_object then null; end $$;

-- The stale-trip sweeper scans ACTIVE trips by their last signal.
create index if not exists idx_trips_active_last_signal on trips (last_signal_at) where status = 'active';

alter table location_pings add column if not exists battery   smallint;
alter table location_pings add column if not exists net_state text;

create table if not exists trip_heartbeats (
    id         bigserial primary key,
    trip_id    uuid not null references trips(id) on delete cascade,
    org_id     uuid not null,
    driver_id  uuid,
    sent_at    timestamptz not null,
    battery    smallint,
    net_state  text,
    created_at timestamptz not null default now()
);
-- Idempotent batch flush (the app re-sends until it gets a 2xx).
create unique index if not exists uq_trip_heartbeats_trip_sent on trip_heartbeats (trip_id, sent_at);

comment on column trips.end_reason is
  'How the trip ended (multi-signal inference, see trip_lifecycle.py). normal = driver pressed End Trip.';
