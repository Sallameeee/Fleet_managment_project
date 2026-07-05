-- Per-stop arrival time + route start time for the map-based route editor.
-- Run once in the Supabase SQL editor. Safe to re-run (IF NOT EXISTS).
-- Both columns are nullable, so existing routes/stops are unaffected.

-- Departure time of the first stop (when the bus leaves stop #1).
alter table routes
    add column if not exists start_time time;

-- Manager-entered arrival time at each stop.
alter table route_stops
    add column if not exists arrival_time time;
