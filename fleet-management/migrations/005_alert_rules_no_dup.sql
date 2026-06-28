-- Race-proof backstop: forbid two ACTIVE alert_rules in the same org with the
-- same (type, threshold, target_kind, target_ids). The app also returns a 409
-- (friendlier message); this index guards against concurrent inserts and any
-- code path that bypasses the app check.
-- Run once in the Supabase SQL editor. Safe to re-run (IF NOT EXISTS).
--
-- coalesce() is used so NULLs participate in uniqueness (Postgres treats raw
-- NULLs as distinct, which would let short_stop [threshold NULL] or
-- target_kind='all' [target_ids NULL] duplicates slip through):
--   * threshold  NULL -> -1   (no real threshold is negative)
--   * target_ids NULL -> '{}' (empty array == "all", matching app semantics)
-- Partial (WHERE is_active): inactive duplicates are still allowed, so you can
-- keep disabled rules around without tripping the constraint.

create unique index if not exists alert_rules_no_dup_active
    on alert_rules (
        org_id,
        type,
        coalesce(threshold, -1),
        target_kind,
        coalesce(target_ids, '{}'::uuid[])
    )
    where is_active;
