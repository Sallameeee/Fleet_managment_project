-- Structured result on request-result notifications, so the app can color them
-- reliably (approved=green, rejected=red) instead of fragile text matching.
-- 'approved' | 'rejected' for change_request_result / profile_request_result;
-- NULL for every other notification type. Additive; University unaffected.
-- Run once in Supabase. Safe to re-run.

alter table notifications add column if not exists result text;
