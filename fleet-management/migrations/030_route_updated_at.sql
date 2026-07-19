-- Instant route sync: a bump-on-edit timestamp so the driver/supervisor app can
-- cheaply detect when a route (its path or stops) changed and re-pull it.
-- The route editor always saves via PATCH /routes/{id} (name + geometry + stops
-- together), so stamping updated_at there covers stop edits too.
-- Additive; University routes get one too but nothing reads it there. Safe to re-run.

alter table routes add column if not exists updated_at timestamptz not null default now();
