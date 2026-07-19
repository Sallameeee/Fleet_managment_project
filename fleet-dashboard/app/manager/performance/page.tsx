"use client";

import { useCallback, useEffect, useState } from "react";
import { getPerformance, type PerfTrip, type PerfSupervisor } from "@/lib/manager";
import { useT } from "@/lib/i18n";
import { useIsSchool } from "@/lib/module";

export default function ManagerPerformancePage() {
  const { t } = useT();
  const isSchool = useIsSchool();
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [trips, setTrips] = useState<PerfTrip[]>([]);
  const [supervisors, setSupervisors] = useState<PerfSupervisor[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const d = await getPerformance(from || undefined, to || undefined);
      setTrips(d.trips);
      setSupervisors(d.supervisors);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.loadFailed"));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [from, to]);

  useEffect(() => {
    if (isSchool) load();
  }, [isSchool, load]);

  if (!isSchool) {
    return <div className="text-sm text-slate-400">This page is only available for school organizations.</div>;
  }

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-white">{t("perf.title")}</h1>
          <p className="text-sm text-slate-400">{loading ? t("common.loading") : t("perf.subtitle")}</p>
        </div>
        <div className="flex items-end gap-2">
          <label className="text-xs text-slate-400">
            <span className="mb-1 block">{t("perf.date")}</span>
            <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="rounded-lg border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-slate-100 focus:border-brand focus:outline-none" />
          </label>
          <span className="pb-2 text-slate-500">→</span>
          <label className="text-xs text-slate-400">
            <span className="mb-1 block">&nbsp;</span>
            <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className="rounded-lg border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-slate-100 focus:border-brand focus:outline-none" />
          </label>
          <button onClick={load} className="rounded-lg border border-ink-700 px-3 py-2 text-sm text-slate-300 transition-colors hover:border-brand hover:text-white">↻ {t("common.reload")}</button>
        </div>
      </div>

      {error && <div className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">{error}</div>}

      {!loading && trips.length === 0 && !error ? (
        <div className="rounded-xl border border-ink-800 px-4 py-12 text-center text-slate-500">{t("perf.none")}</div>
      ) : (
        <>
          {/* Per-supervisor summary */}
          <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-400">{t("perf.supervisors")}</h2>
          <div className="mb-8 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {supervisors.map((s) => (
              <div key={s.driver_id ?? s.name} className="rounded-xl border border-ink-800 bg-ink-900/50 p-4">
                <div className="flex items-center justify-between">
                  <span className="truncate font-semibold text-white">{s.name ?? "—"}</span>
                  <span className="text-xs text-slate-500">{s.trips} {t("perf.trips")}</span>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-2 text-sm">
                  <Stat label={t("perf.onTimePct")} value={s.on_time_pct == null ? "—" : `${s.on_time_pct}%`} tone={s.on_time_pct == null ? "muted" : s.on_time_pct >= 85 ? "good" : s.on_time_pct >= 60 ? "warn" : "bad"} />
                  <Stat label={t("perf.late")} value={String(s.stops_late)} tone={s.stops_late === 0 ? "good" : "warn"} />
                  <Stat label={t("perf.speeding")} value={String(s.speeding)} tone={s.speeding === 0 ? "good" : "bad"} />
                  <Stat label={t("perf.offRoute")} value={String(s.off_route)} tone={s.off_route === 0 ? "good" : "bad"} />
                </div>
              </div>
            ))}
          </div>

          {/* Per-trip table */}
          <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-400">{t("perf.tripsTitle")}</h2>
          <div className="overflow-x-auto rounded-xl border border-ink-800">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead className="bg-ink-900/70 text-xs uppercase tracking-wide text-slate-400">
                <tr>
                  <th className="px-4 py-3">{t("perf.date")}</th>
                  <th className="px-4 py-3">{t("perf.supervisor")}</th>
                  <th className="px-4 py-3">{t("perf.route")}</th>
                  <th className="px-4 py-3 text-center">{t("perf.onTimePct")}</th>
                  <th className="px-4 py-3 text-center">{t("perf.late")}</th>
                  <th className="px-4 py-3 text-center">{t("perf.speeding")}</th>
                  <th className="px-4 py-3 text-center">{t("perf.offRoute")}</th>
                  <th className="px-4 py-3 text-center">{t("perf.avgDelay")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-800">
                {trips.map((tr) => {
                  const pct = tr.stops_total ? Math.round((100 * tr.stops_on_time) / tr.stops_total) : null;
                  return (
                    <tr key={tr.trip_id} className="hover:bg-ink-900/40">
                      <td className="px-4 py-3 text-slate-300">{tr.trip_date ?? "—"}</td>
                      <td className="px-4 py-3 font-medium text-white">{tr.driver_name ?? "—"}</td>
                      <td className="px-4 py-3 text-slate-400">{tr.route_name ?? "—"}</td>
                      <td className="px-4 py-3 text-center">{pct == null ? "—" : <span className={pct >= 85 ? "text-emerald-300" : pct >= 60 ? "text-amber-300" : "text-red-300"}>{pct}%</span>}</td>
                      <td className={"px-4 py-3 text-center " + (tr.stops_late ? "text-amber-300" : "text-slate-400")}>{tr.stops_late}</td>
                      <td className={"px-4 py-3 text-center " + (tr.speeding_count ? "text-red-300" : "text-slate-400")}>{tr.speeding_count}</td>
                      <td className={"px-4 py-3 text-center " + (tr.off_route_count ? "text-red-300" : "text-slate-400")}>{tr.off_route_count}</td>
                      <td className="px-4 py-3 text-center text-slate-300">{tr.avg_delay_min == null ? "—" : `${tr.avg_delay_min} ${t("perf.min")}`}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone: "good" | "warn" | "bad" | "muted" }) {
  const color = tone === "good" ? "text-emerald-300" : tone === "warn" ? "text-amber-300" : tone === "bad" ? "text-red-300" : "text-slate-400";
  return (
    <div className="rounded-lg border border-ink-700 bg-ink-850 px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className={"mt-0.5 text-lg font-bold " + color}>{value}</div>
    </div>
  );
}
