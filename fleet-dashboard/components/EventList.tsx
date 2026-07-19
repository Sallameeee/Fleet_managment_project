"use client";

import { useT } from "@/lib/i18n";
import { type LogEvent } from "@/lib/manager";

// Per-type styling for the event badge (reuses the alert types the engine emits).
const TYPE_STYLE: Record<string, { cls: string; icon: string }> = {
  speeding: { cls: "border-red-500/40 bg-red-500/10 text-red-300", icon: "⚠" },
  off_route: { cls: "border-amber-500/40 bg-amber-500/10 text-amber-300", icon: "🧭" },
  short_stop: { cls: "border-sky-500/40 bg-sky-500/10 text-sky-300", icon: "⏱" },
  offline: { cls: "border-slate-500/40 bg-slate-500/10 text-slate-300", icon: "📴" },
};
const DEFAULT_STYLE = { cls: "border-slate-500/40 bg-slate-500/10 text-slate-300", icon: "•" };

function fmtTime(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso.replace(" ", "T"));
  if (Number.isNaN(d.getTime())) return "";
  const sameDay = new Date().toDateString() === d.toDateString();
  return sameDay
    ? d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** Renders a list of detected driver events. `showDriver=false` on a per-driver
 * view (the driver is already known). */
export default function EventList({
  events,
  showDriver = true,
  emptyText,
}: {
  events: LogEvent[];
  showDriver?: boolean;
  emptyText: string;
}) {
  const { t } = useT();
  if (events.length === 0) {
    return <div className="rounded-lg border border-ink-800 px-4 py-6 text-center text-sm text-slate-500">{emptyText}</div>;
  }
  return (
    <div className="space-y-2">
      {events.map((e) => {
        const st = TYPE_STYLE[e.type] ?? DEFAULT_STYLE;
        return (
          <div key={e.id} className="rounded-lg border border-ink-800 bg-ink-900/40 p-3">
            <div className="flex items-start gap-3">
              <span className={"mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-sm " + st.cls}>{st.icon}</span>
              <div className="min-w-0 flex-1">
                <div className="text-sm text-slate-200">
                  {showDriver && <span className="font-semibold text-white">{e.driver_name ?? "—"} </span>}
                  {showDriver && <span className="text-slate-400">{t("logs.on")} </span>}
                  <span className="font-medium text-white">🚌 {e.vehicle_bus_number ?? "—"}</span>
                  <span className="text-slate-500"> · {t("logs.route")} </span>
                  <span className="text-slate-200">{e.route_name ?? "—"}</span>
                  <span className="text-slate-500"> — </span>
                  <span className={"font-semibold " + st.cls.split(" ").find((c) => c.startsWith("text-"))}>{e.label}</span>
                </div>
                {e.detail && <div className="mt-0.5 text-xs text-slate-400">{e.detail}</div>}
                <div className="mt-1 text-[11px] text-slate-500">{fmtTime(e.occurred_at)}</div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
