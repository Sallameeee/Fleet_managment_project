-- 045: trip-lifecycle LOG POINTS reuse the alerts table / alert_type enum.
--
-- Four new event types (shared by school + university; see trip_lifecycle.py):
--   trip_started         — the trip began (marker = first accepted GPS fix)
--   trip_ended           — the trip ended + HOW (meta.reason, see 046)
--   connection_lost      — pings stopped ARRIVING for > the org's grace (default 90 s)
--   connection_restored  — pings resumed; meta carries the gap + buffered count
--
-- Until this runs the backend simply skips these four log points (never fails
-- a trip start/end/ping). ALTER TYPE ... ADD VALUE cannot share a transaction
-- with statements that USE the value, so this file holds ONLY the enum change.
-- Run it on its own in the Supabase SQL editor (after 044), then run 046.

alter type alert_type add value if not exists 'trip_started';
alter type alert_type add value if not exists 'trip_ended';
alter type alert_type add value if not exists 'connection_lost';
alter type alert_type add value if not exists 'connection_restored';
