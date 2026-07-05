-- Per-route display color (hex string like "#3AA76D"), chosen by the manager and
-- used to draw the route line in the editor, detail view, and the all-routes
-- overview. Run once in the Supabase SQL editor. Safe to re-run. Nullable — a
-- route with no color falls back to the brand green (or a palette slot in the
-- overview), so existing routes are unaffected.

alter table routes
    add column if not exists color text;
