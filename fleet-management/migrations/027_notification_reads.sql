-- Per-MANAGER read state for org-wide manager notifications (School module).
-- Manager notifications (audience='manager', recipient_id NULL) are shared by all
-- of an org's managers, but each manager now has their OWN read/unread state via a
-- read-receipt row here. (Parent notifications stay per-recipient on
-- notifications.is_read and are unaffected.)
-- Additive only; University unaffected. Run once in Supabase. Safe to re-run.

create table if not exists notification_reads (
    notification_id uuid not null references notifications(id) on delete cascade,
    user_id         uuid not null references profiles(id) on delete cascade,
    read_at         timestamptz not null default now(),
    primary key (notification_id, user_id)
);

create index if not exists idx_notification_reads_user on notification_reads (user_id);
