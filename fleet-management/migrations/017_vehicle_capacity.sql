-- Vehicle seating capacity. Additive + nullable, so every existing vehicle and
-- every University org is unaffected. The FULL/seats-free calculation arrives
-- later with students; this is just the stored number.
-- Run once in the Supabase SQL editor. Safe to re-run.

alter table vehicles
    add column if not exists capacity integer;
