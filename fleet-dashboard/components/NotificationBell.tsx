"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  listNotifications,
  getUnreadNotificationCount,
  markAllNotificationsRead,
  type ManagerNotification,
} from "@/lib/manager";
import { useT } from "@/lib/i18n";

/** Manager notification bell for the dashboard header (school orgs only). Polls
 * the unread count; opening the panel loads the list and marks everything read. */
export default function NotificationBell() {
  const { t } = useT();
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<ManagerNotification[]>([]);
  const [loading, setLoading] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const refreshCount = useCallback(() => {
    getUnreadNotificationCount().then(setUnread).catch(() => {});
  }, []);

  useEffect(() => {
    refreshCount();
    const id = setInterval(refreshCount, 30000);
    return () => clearInterval(id);
  }, [refreshCount]);

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (!next) return;
    setLoading(true);
    try {
      const { notifications } = await listNotifications();
      setItems(notifications);
      if (notifications.some((n) => !n.is_read)) {
        await markAllNotificationsRead(); // mark read on view
        setUnread(0);
      }
    } catch {
      /* keep the panel; count stays */
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={toggle}
        aria-label={t("notif.title")}
        className="relative rounded-lg border border-ink-700 p-1.5 text-slate-300 transition-colors hover:border-brand hover:text-white"
      >
        <BellIcon />
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-bold text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 z-50 mt-2 w-80 overflow-hidden rounded-xl border border-ink-700 bg-ink-900 shadow-xl">
          <div className="flex items-center justify-between border-b border-ink-800 px-4 py-2.5">
            <span className="text-sm font-semibold text-white">{t("notif.title")}</span>
          </div>
          <div className="max-h-96 overflow-y-auto">
            {loading ? (
              <div className="px-4 py-8 text-center text-sm text-slate-500">…</div>
            ) : items.length === 0 ? (
              <div className="px-4 py-8 text-center text-sm text-slate-500">{t("notif.none")}</div>
            ) : (
              items.map((n) => (
                <div key={n.id} className={"border-b border-ink-800 px-4 py-3 " + (n.is_read ? "" : "bg-brand/5")}>
                  <div className="flex items-start gap-2">
                    {!n.is_read && <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-brand" />}
                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-white">{n.title}</p>
                      {n.body && <p className="mt-0.5 text-xs text-slate-400">{n.body}</p>}
                      <p className="mt-1 text-[11px] text-slate-600">{timeAgo(n.created_at)}</p>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function timeAgo(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const s = Math.floor((Date.now() - d.getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return d.toLocaleDateString();
}

function BellIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
      <path d="M13.73 21a2 2 0 0 1-3.46 0" />
    </svg>
  );
}
