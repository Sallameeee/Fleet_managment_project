"use client";

import { useCallback, useEffect, useState } from "react";
import { getBusesToday, type BusToday } from "@/lib/manager";
import { useT } from "@/lib/i18n";
import { useIsSchool } from "@/lib/module";

// Poll so a manager watching this page sees approved changes / trip starts appear.
const POLL_MS = 20000;

function StatusPill({ status }: { status: BusToday["trip_status"] }) {
  const map: Record<BusToday["trip_status"], { label: string; cls: string }> = {
    active: { label: "En route", cls: "border-brand/40 bg-brand/10 text-brand-sage" },
    completed: { label: "Completed", cls: "border-ink-700 bg-ink-800 text-slate-400" },
    not_started: { label: "Not started", cls: "border-amber-500/30 bg-amber-500/10 text-amber-300" },
  };
  const s = map[status] ?? map.not_started;
  return <span className={"rounded-full border px-2.5 py-0.5 text-[11px] font-semibold " + s.cls}>{s.label}</span>;
}

export default function ManagerBusesTodayPage() {
  const { t } = useT();
  const isSchool = useIsSchool();
  const [buses, setBuses] = useState<BusToday[]>([]);
  const [date, setDate] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const d = await getBusesToday();
      setBuses(d.buses);
      setDate(d.date);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.loadFailed"));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!isSchool) return;
    load();
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, [isSchool, load]);

  if (!isSchool) {
    return <div className="text-sm text-slate-400">This page is only available for school organizations.</div>;
  }

  const totals = buses.reduce(
    (acc, b) => ({ onboard: acc.onboard + b.onboard_count, movedIn: acc.movedIn + b.moved_in_count, movedOut: acc.movedOut + b.moved_out_count }),
    { onboard: 0, movedIn: 0, movedOut: 0 },
  );

  return (
    <div className="max-w-5xl">
      <div className="mb-6 flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-white">{t("nav.busesToday")}</h1>
          <p className="text-sm text-slate-400">
            {t("busesToday.subtitle")}
            {date ? ` · ${date}` : ""}
          </p>
        </div>
        {buses.length > 0 && (
          <div className="hidden gap-4 text-right sm:flex">
            <div><div className="text-lg font-bold text-white">{totals.onboard}</div><div className="text-[11px] text-slate-500">{t("busesToday.onboard")}</div></div>
            <div><div className="text-lg font-bold text-brand-sage">{totals.movedIn}</div><div className="text-[11px] text-slate-500">{t("busesToday.joined")}</div></div>
            <div><div className="text-lg font-bold text-amber-300">{totals.movedOut}</div><div className="text-[11px] text-slate-500">{t("busesToday.left")}</div></div>
          </div>
        )}
      </div>

      {error && <div className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</div>}
      {loading ? (
        <div className="text-sm text-slate-500">…</div>
      ) : buses.length === 0 ? (
        <div className="rounded-xl border border-ink-800 bg-ink-900/40 px-4 py-10 text-center text-sm text-slate-500">{t("busesToday.none")}</div>
      ) : (
        <div className="space-y-4">
          {buses.map((b) => (
            <div key={b.route_id} className="overflow-hidden rounded-xl border border-ink-800 bg-ink-900/40">
              {/* Bus header */}
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-ink-800 px-4 py-3">
                <div className="flex items-center gap-3">
                  <span className="text-base font-semibold text-white">{b.route_name || "—"}</span>
                  <StatusPill status={b.trip_status} />
                </div>
                <div className="flex items-center gap-4 text-xs text-slate-400">
                  {b.vehicle_bus_number && <span>🚌 {b.vehicle_bus_number}</span>}
                  <span>{b.supervisor_name || t("busesToday.noSupervisor")}</span>
                  <span className="font-semibold text-slate-200">{b.onboard_count} {t("busesToday.onboard")}</span>
                </div>
              </div>

              {/* Riding */}
              <div className="px-4 py-3">
                {b.riding.length === 0 ? (
                  <div className="text-sm text-slate-500">{t("busesToday.empty")}</div>
                ) : (
                  <div className="grid gap-1.5 sm:grid-cols-2">
                    {b.riding.map((s) => (
                      <div key={s.student_id} className="flex items-center justify-between gap-2 rounded-lg border border-ink-800 px-3 py-2">
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="truncate text-sm text-white">{s.name || "—"}</span>
                            {s.moved_in && (
                              <span className="shrink-0 rounded-full border border-brand/40 bg-brand/10 px-1.5 py-0.5 text-[10px] font-semibold text-brand-sage">
                                {t("busesToday.joinedToday")}
                              </span>
                            )}
                          </div>
                          <div className="text-[11px] text-slate-500">
                            {s.class_name || s.grade ? `${s.class_name ?? ""}${s.class_name && s.grade ? " · " : ""}${s.grade ?? ""}` : ""}
                            {s.drop_off_stop ? `${s.class_name || s.grade ? " · " : ""}📍 ${s.drop_off_stop}` : ""}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {/* Moved out — children who ride another bus today */}
                {b.moved_out.length > 0 && (
                  <div className="mt-3 rounded-lg border border-amber-500/25 bg-amber-500/5 px-3 py-2">
                    <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-amber-300">
                      {t("busesToday.notToday")} ({b.moved_out.length})
                    </div>
                    <div className="flex flex-wrap gap-x-4 gap-y-1">
                      {b.moved_out.map((m) => (
                        <span key={m.student_id} className="text-xs text-slate-300">
                          {m.name}
                          {m.to_route_name && <span className="text-slate-500"> → {m.to_route_name}</span>}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
