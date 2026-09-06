-- 039: add the `long_stop` alert type.
--
-- The detection engine (routers/trips.py _detect_long_stop) raises an alert when
-- a bus stands still, away from any scheduled route stop, for longer than the
-- org's threshold. `alerts.type` is the enum `alert_type`, so the new value needs
-- DDL. Until this runs, the code stores those events as type 'short_stop' with a
-- "Long stop:" detail prefix and the Logs page labels them correctly anyway.
--
-- NOTE: ALTER TYPE ... ADD VALUE cannot run inside a transaction together with
-- statements that USE the new value, so this file contains ONLY the enum change.
-- Run it on its own in the Supabase SQL editor (after 038), then run 040.

alter type alert_type add value if not exists 'long_stop';
