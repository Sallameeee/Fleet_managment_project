-- Per-org FEATURE FLAGS. The super admin, after choosing an org's module, picks
-- which toggleable features are enabled. Stored as a JSON array of feature keys.
--
--   * CORE features are ALWAYS on (enforced in code) and need not be listed here.
--   * NULL  = a LEGACY org (created before this migration) → treated as ALL of its
--             module's features ON, so nothing an existing org uses disappears.
--   * '[]'  = an explicit empty set (core-only) — what a NEW org starts with unless
--             the super admin enables extras.
--   * Changing an org's module RESETS this to core-only for the new module (done in
--     the API), so a school flag can never linger on a university org.
--
-- Additive + nullable → University and every existing org are unaffected.
-- Run once in the Supabase SQL editor. Safe to re-run.

alter table organizations
    add column if not exists enabled_features jsonb;
