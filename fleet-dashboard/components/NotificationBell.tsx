"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  listNotifications,
  getUnreadNotificationCount,
  markAllNotificationsRead,
  type ManagerNotification,
} from "@/lib/manager";
import { useT } from "@/lib/i18n";

// Auto-refresh cadence for the badge (polling — no push/Firebase).
const POLL_MS = 15000;

/** Manager notification bell for the dashboard header (school orgs only). Polls
 * the unread count every 15s; opening the panel loads the list and marks it read;
 * a request notification navigates to that request. */
export default function NotificationBell() {
  const { t } = useT();
  const router = useRouter();
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<ManagerNotification[]>([]);
  const [loading, setLoading] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const refreshCount = useCallback(() => {
    getUnreadNotificationCount().then(setUnread).catch(() => {});
  }, []);

  // Auto-light-up: poll the unread count so the badge appears on its own.
  useEffect(() => {
    refreshCount();
    const id = setInterval(refreshCount, POLL_MS);
    return () => clearInterval(id);
  }, [refreshCount]);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  const loadList = useCallback(async (markRead: boolean) => {
    setLoading(true);
    try {
      const { notifications } = await listNotifications();
      setItems(notifications);
      if (markRead && notifications.some((n) => !n.is_read)) {
        await markAllNotificationsRead();
        setUnread(0);
      }
    } catch {
      /* keep the panel; count stays */
    } finally {
      setLoading(false);
    }
  }, []);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next) await loadList(true); // mark read on view
  }

  // Request notifications jump to the item on the relevant page.
  function targetFor(n: ManagerNotification): string | null {
    if (!n.related_id) return null;
    if (n.type === "change_request_new") return `/manager/change-requests?focus=${n.related_id}`;
    if (n.type === "profile_request_new") return `/manager/profile-requests?focus=${n.related_id}`;
    if (n.type === "parent_report_new") return `/manager/parent-reports?focus=${n.related_id}`;
    return null;
  }

  function openItem(n: ManagerNotification) {
    const href = targetFor(n);
    if (!href) return;
    setOpen(false);
    router.push(href);
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
        <div className="absolute right-0 z-50 mt-2 w-80 max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-ink-700 bg-ink-900 shadow-xl">
          <div className="flex items-center justify-between border-b border-ink-800 px-4 py-2.5">
            <span className="text-sm font-semibold text-white">{t("notif.title")}</span>
            <button
              onClick={() => loadList(false)}
              aria-label={t("common.reload")}
              title={t("common.reload")}
              className="rounded-md p-1 text-slate-400 hover:bg-ink-800 hover:text-white"
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="23 4 23 10 17 10" /><polyline points="1 20 1 14 7 14" /><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" /></svg>
            </button>
          </div>
          <div className="max-h-96 overflow-y-auto">
            {loading ? (
              <div className="px-4 py-8 text-center text-sm text-slate-500">…</div>
            ) : items.length === 0 ? (
              <div className="px-4 py-8 text-center text-sm text-slate-500">{t("notif.none")}</div>
            ) : (
              items.map((n) => {
                const clickable = targetFor(n) !== null;
                return (
                  <button
                    key={n.id}
                    onClick={() => openItem(n)}
                    disabled={!clickable}
                    className={
                      "block w-full border-b border-ink-800 px-4 py-3 text-left " +
                      (n.is_read ? "" : "bg-brand/5 ") +
                      (clickable ? "cursor-pointer hover:bg-ink-800" : "cursor-default")
                    }
                  >
                    <div className="flex items-start gap-2">
                      {!n.is_read && <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-brand" />}
                      <div className="min-w-0">
                        <p className="text-sm font-semibold text-white">{n.title}</p>
                        {n.body && <p className="mt-0.5 text-xs text-slate-400">{n.body}</p>}
                        <p className="mt-1 text-[11px] text-slate-600">{timeAgo(n.created_at)}{clickable ? " · tap to open" : ""}</p>
                      </div>
                    </div>
                  </button>
                );
              })
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
