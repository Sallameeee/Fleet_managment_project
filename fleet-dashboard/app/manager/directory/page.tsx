"use client";

import { useCallback, useEffect, useState } from "react";
import { getSchoolDirectory, getDriverLogs, type DirectoryPerson, type LogEvent } from "@/lib/manager";
import { useT } from "@/lib/i18n";
import { useIsSchool } from "@/lib/module";
import EventList from "@/components/EventList";

export default function ManagerDirectoryPage() {
  const { t } = useT();
  const isSchool = useIsSchool();
  const [supervisors, setSupervisors] = useState<DirectoryPerson[]>([]);
  const [busDrivers, setBusDrivers] = useState<DirectoryPerson[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const d = await getSchoolDirectory();
      setSupervisors(d.supervisors);
      setBusDrivers(d.bus_drivers);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.loadFailed"));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (isSchool) load();
  }, [isSchool, load]);

  if (!isSchool) {
    return <div className="text-sm text-slate-400">This page is only available for school organizations.</div>;
  }

  return (
    <div>
      <div className="mb-6 flex items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-white">{t("dir.title")}</h1>
          <p className="text-sm text-slate-400">{loading ? t("common.loading") : t("dir.subtitle")}</p>
        </div>
        <button
          onClick={load}
          className="rounded-lg border border-ink-700 px-3 py-2 text-sm text-slate-300 transition-colors hover:border-brand hover:text-white"
        >
          ↻ {t("common.reload")}
        </button>
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error} <button onClick={load} className="underline hover:text-red-200">{t("common.retry")}</button>
        </div>
      )}

      {/* Supervisors are the app drivers that generate events → show per-driver logs. */}
      <Section title={t("dir.supervisors")} people={supervisors} loading={loading} withEvents />
      <div className="h-8" />
      <Section title={t("dir.busDrivers")} people={busDrivers} loading={loading} />
    </div>
  );
}

function Section({ title, people, loading, withEvents = false }: { title: string; people: DirectoryPerson[]; loading: boolean; withEvents?: boolean }) {
  const { t } = useT();
  return (
    <div>
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-400">
        {title} {!loading && <span className="text-slate-600">· {people.length}</span>}
      </h2>
      {!loading && people.length === 0 ? (
        <div className="rounded-xl border border-ink-800 px-4 py-8 text-center text-sm text-slate-500">{t("dir.none")}</div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {people.map((p) => (
            <PersonCard key={p.id} p={p} withEvents={withEvents} />
          ))}
        </div>
      )}
    </div>
  );
}

function PersonCard({ p, withEvents = false }: { p: DirectoryPerson; withEvents?: boolean }) {
  const { t } = useT();
  const initials = (p.name ?? "?").trim().charAt(0).toUpperCase() || "?";
  const route = p.route_name ?? t("dir.noRoute");

  // Per-driver events (lazy-loaded on first expand).
  const [open, setOpen] = useState(false);
  const [events, setEvents] = useState<LogEvent[] | null>(null);
  const [loadingEvents, setLoadingEvents] = useState(false);

  async function toggleEvents() {
    const next = !open;
    setOpen(next);
    if (next && events === null) {
      setLoadingEvents(true);
      try {
        setEvents(await getDriverLogs(p.id));
      } catch {
        setEvents([]);
      } finally {
        setLoadingEvents(false);
      }
    }
  }

  return (
    <div className="rounded-xl border border-ink-800 bg-ink-900/50 p-4">
      <div className="flex items-center gap-3">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-brand/15 text-sm font-bold text-brand-sage">{initials}</div>
        <div className="min-w-0 flex-1">
          <div className="truncate font-semibold text-white">{p.name ?? "—"}</div>
          <div className="truncate text-xs text-slate-400">
            {route}
            {p.vehicle_bus_number ? ` · 🚌 ${p.vehicle_bus_number}` : ""}
          </div>
        </div>
      </div>
      <div className="mt-3">
        {p.phone ? (
          <a
            href={`tel:${p.phone}`}
            className="flex items-center justify-center gap-2 rounded-lg border border-brand/40 bg-brand/10 px-3 py-2 text-sm font-medium text-brand-sage transition-colors hover:bg-brand/20"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z" /></svg>
            {t("dir.call")} · {p.phone}
          </a>
        ) : (
          <div className="rounded-lg border border-ink-800 px-3 py-2 text-center text-xs text-slate-600">{t("dir.noPhone")}</div>
        )}
      </div>

      {withEvents && (
        <div className="mt-3 border-t border-ink-800 pt-3">
          <button
            onClick={toggleEvents}
            className="flex w-full items-center justify-between text-sm text-slate-300 transition-colors hover:text-white"
          >
            <span className="font-medium">{t("logs.driverEvents")}</span>
            <span className="text-xs text-slate-500">{open ? t("logs.hideEvents") : t("logs.viewEvents")} {open ? "▲" : "▼"}</span>
          </button>
          {open && (
            <div className="mt-3">
              {loadingEvents ? (
                <div className="px-2 py-4 text-center text-sm text-slate-500">{t("common.loading")}</div>
              ) : (
                <EventList events={events ?? []} showDriver={false} emptyText={t("logs.none")} />
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
