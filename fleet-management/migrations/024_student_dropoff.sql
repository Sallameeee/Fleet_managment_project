-- School module: each STUDENT gets a DROP-OFF STOP — the stop on their assigned
-- route where the child gets off. Stored as the stop NAME (text), NOT a route_stops
-- id, on purpose:
--   * Route edits delete+recreate all route_stops rows with NEW ids (see
--     routers/routes.py update_route), so an id FK would be wiped on every edit;
--     the stop NAME survives.
--   * It matches change_requests.requested_stop (also the stop name), so the
--     normal drop-off and a one-day requested change are represented identically.
-- The parent live map resolves this name against the route's stops to get lat/lng
-- for distance/ETA.
--
-- Nullable → University passengers are completely unaffected (they never set it).
-- Run once in the Supabase SQL editor. Safe to re-run.

alter table passengers
    add column if not exists drop_off_stop text;
