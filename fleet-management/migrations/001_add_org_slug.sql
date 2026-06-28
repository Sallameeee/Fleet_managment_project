-- Adds a unique, human-friendly login slug to organizations.
-- Run once in the Supabase SQL editor (Dashboard -> SQL Editor -> New query).
-- Safe to re-run: guarded with IF NOT EXISTS and only backfills empty slugs.

-- 1. Add the column (nullable for now so we can backfill).
alter table organizations
    add column if not exists slug text;

-- 2. Backfill existing rows: slugify(name), de-duplicated with -2, -3, ...
with base as (
    select
        id,
        nullif(
            regexp_replace(
                regexp_replace(lower(coalesce(name, 'org')), '[^a-z0-9]+', '-', 'g'),
                '(^-+|-+$)', '', 'g'
            ),
            ''
        ) as base_slug
    from organizations
    where slug is null or slug = ''
),
numbered as (
    select
        id,
        coalesce(base_slug, 'org') as base_slug,
        row_number() over (partition by coalesce(base_slug, 'org') order by id) as rn
    from base
)
update organizations o
set slug = case when n.rn = 1 then n.base_slug else n.base_slug || '-' || n.rn end
from numbered n
where o.id = n.id;

-- 3. Final safety net for any still-empty slug (e.g. name was all symbols).
update organizations
set slug = 'org-' || left(id::text, 8)
where slug is null or slug = '';

-- 4. Enforce going forward: not null + unique.
alter table organizations
    alter column slug set not null;

create unique index if not exists organizations_slug_key
    on organizations (slug);
