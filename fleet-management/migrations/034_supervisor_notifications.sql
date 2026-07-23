-- School module: let SUPERVISORS (driver-role app users) receive notifications.
--
-- Until now `notifications.audience` allowed only ('parent','manager'). Approved
-- one-day bus changes now also notify the two supervisors involved:
--   * the LOSING supervisor  ("<child> is not with you today")
--   * the GAINING supervisor ("<child> joins your bus today")
-- and a supervisor can flag a child who boarded despite a change, which raises a
-- MANAGER notification (audience='manager', type='boarding_flag').
--
-- Recipient model for the new audience:
--   audience='supervisor' → recipient_id = that supervisor's profiles.id (personal,
--                           read state on notifications.is_read, exactly like a parent)
--
-- Additive + idempotent. University is unaffected: the generation layer is
-- school-gated, so no rows are ever created for a non-school org.
-- Run once in the Supabase SQL editor. Safe to re-run.

alter table notifications drop constraint if exists notifications_audience_check;

alter table notifications
    add constraint notifications_audience_check
    check (audience in ('parent', 'manager', 'supervisor'));

-- Personal-inbox lookup for a supervisor (mirrors the parent index).
create index if not exists idx_notifications_supervisor
    on notifications (org_id, recipient_id, is_read, created_at desc)
    where audience = 'supervisor';
