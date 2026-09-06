-- Fix: deleting a route left it stranded (route_stops gone, routes row still
-- there) whenever any HISTORICAL trip referenced it. Cause: trips.route_id has a
-- plain foreign key (default RESTRICT) to routes(id), so Postgres silently
-- blocked the `DELETE FROM routes ...` statement with a foreign-key-violation
-- error AFTER route_stops (which cascades) had already been removed.
--
-- The code's own intent (routers/routes.py delete_route docstring) was always
-- "historical trips keep their route_id (route_name just shows blank for them)"
-- — i.e. deleting a route should NOT delete or block on its trip history, it
-- should just null out the dangling reference. That requires ON DELETE SET NULL,
-- matching the pattern already used for passengers.route_id,
-- change_requests.current_route_id, and trip_performance.route_id.
--
-- This is SHARED logic — trips belong to both school and university orgs
-- identically, so this single fix applies to both modules with no forking.
--
-- Finds the constraint by its actual definition (not by an assumed name, since
-- this table predates the migrations/ convention) so it's safe to re-run.
-- Run once in the Supabase SQL editor.

do $$
declare
    fk_name text;
begin
    select tc.constraint_name into fk_name
    from information_schema.table_constraints tc
    join information_schema.key_column_usage kcu
        on tc.constraint_name = kcu.constraint_name and tc.table_schema = kcu.table_schema
    join information_schema.constraint_column_usage ccu
        on tc.constraint_name = ccu.constraint_name and tc.table_schema = ccu.table_schema
    where tc.table_name = 'trips'
      and tc.constraint_type = 'FOREIGN KEY'
      and kcu.column_name = 'route_id'
      and ccu.table_name = 'routes'
    limit 1;

    if fk_name is not null then
        execute format('alter table trips drop constraint %I', fk_name);
    end if;

    alter table trips
        add constraint trips_route_id_fkey
        foreign key (route_id) references routes(id) on delete set null;
end $$;
